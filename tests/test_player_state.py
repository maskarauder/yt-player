"""Execute the shipped player observer against controlled player events.

Node is optional; these tests never contact YouTube or launch a browser.
"""

import ast
import json
from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


def browser_script(name):
    tree = ast.parse((ROOT / "browser.py").read_text())
    return next(
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == name
                for target in node.targets)
    )


@unittest.skipUnless(shutil.which("node"), "Node is needed to execute player JavaScript")
class PlayerStateTests(unittest.TestCase):
    def run_scenario(self, scenario):
        harness = """
const assert = require('node:assert/strict');
global.window = {};
global.location = {href: 'https://www.youtube.com/watch?v=first&list=PL1'};
const current = {ids: ['first', 'last'], index: 0, id: 'first', state: 1, ad: false};
let now = 0;
global.performance = {now: () => now};
const calls = {play: 0, next: 0};
const listeners = [];
const player = {
    getPlaylist: () => current.ids,
    getPlaylistIndex: () => current.index,
    getVideoUrl: () => 'https://www.youtube.com/watch?v=' + current.id,
    getPlayerState: () => current.state,
    classList: {contains: name => current.ad && name === 'ad-showing'},
    addEventListener: (name, callback) => listeners.push({name, callback}),
    playVideo: () => calls.play++,
    nextVideo: () => calls.next++,
};
global.document = {getElementById: () => player};
function fire(state) {
    current.state = state;
    for (const listener of listeners) {
        if (listener.name === 'onStateChange') {
            const callback = typeof listener.callback === 'string'
                ? window[listener.callback] : listener.callback;
            callback(state);
        }
    }
}
function finalVideo() { current.index = 1; current.id = 'last'; }
const read = new Function(OBSERVER_SCRIPT);
const recoverScript = new Function(RECOVERY_SCRIPT);
const recover = () => recoverScript(30000);
""".replace("OBSERVER_SCRIPT", json.dumps(browser_script("PLAYER_STATE_SCRIPT"))).replace(
            "RECOVERY_SCRIPT", json.dumps(browser_script("RECOVER_PLAYBACK_SCRIPT")))
        result = subprocess.run(
            [shutil.which("node"), "-e", harness + "\n" + scenario],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_duplicate_display_titles_do_not_end_playlist(self):
        self.run_scenario("""
player.getVideoData = () => ({title: 'Repeated title'});
assert.equal(read().ended, false);
fire(0);  // First video ends; it has the same display title as the last.
assert.equal(read().ended, false);
finalVideo();
fire(1);
assert.equal(read().ended, false);
fire(0);
assert.equal(read().ended, true);
""")

    def test_pause_buffering_and_ads_do_not_complete_last_video(self):
        self.run_scenario("""
finalVideo();
assert.equal(read().ended, false);
fire(2);  // Paused.
assert.equal(read().ended, false);
fire(3);  // Buffering.
assert.equal(read().ended, false);
current.ad = true;
fire(0);  // An advertisement ended, not the content.
assert.equal(read().ended, false);
current.ad = false;
fire(1);
assert.equal(read().ended, false);
fire(0);
assert.equal(read().ended, true);
""")

    def test_completion_is_latched_before_next_poll(self):
        self.run_scenario("""
finalVideo();
assert.equal(read().ended, false);
fire(0);
current.id = 'autoplay-recommendation';
current.index = -1;
fire(1);
assert.equal(read().ready, false);
assert.equal(read().ended, true);
""")

    def test_mismatched_current_video_cannot_end_playlist(self):
        self.run_scenario("""
finalVideo();
current.id = 'different-video';
assert.equal(read().ready, false);
fire(0);
assert.equal(read().ended, false);
""")

    def test_player_replacement_installs_fresh_observer(self):
        self.run_scenario("""
finalVideo();
read();
fire(0);
assert.equal(read().ended, true);
current.index = 0;
current.id = 'first';
current.state = 1;
const replacement = {...player};
document.getElementById = () => replacement;
assert.equal(read().ended, false);
assert.equal(read().ready, true);
""")

    def test_unstarted_video_is_started_once_and_manual_pause_is_respected(self):
        self.run_scenario("""
current.state = -1;
read();
assert.equal(recover().action, 'play');
assert.equal(recover(), null);
assert.equal(calls.play, 1);
fire(1);
recover();
fire(2);  // User pauses an already playing video.
now = 60000;
assert.equal(recover(), null);
assert.equal(calls.play, 1);
""")

    def test_stalled_autoplay_advances_once_after_grace_period(self):
        self.run_scenario("""
read();
recover();
fire(0);
assert.equal(recover(), null);
now = 4999;
assert.equal(recover(), null);
now = 5000;
assert.equal(recover().action, 'next');
assert.equal(recover(), null);
assert.equal(calls.next, 1);
finalVideo();
fire(5);  // The next item was cued but did not start.
assert.equal(recover().action, 'play');
assert.equal(calls.play, 1);
""")

    def test_native_autoplay_between_polls_does_not_skip_a_video(self):
        self.run_scenario("""
read();
recover();
fire(0);
recover();
read();  // Python's last observation still says the first video ended.
now = 5000;
finalVideo();
fire(1);  // Native autoplay advances just before recovery runs.
assert.equal(recover(), null);
assert.equal(calls.next, 0);
fire(0);
assert.equal(read().ended, true);
assert.equal(recover(), null);  // Do not leave the playlist at its end.
assert.equal(calls.next, 0);
""")

    def test_ads_and_buffering_are_never_treated_as_finished_content(self):
        self.run_scenario("""
read();
recover();
current.ad = true;
fire(0);
recover();
now = 60000;
assert.equal(recover(), null);
current.ad = false;
fire(3);
assert.equal(recover(), null);
assert.equal(calls.next, 0);
assert.equal(calls.play, 0);
""")

    def test_failed_start_and_failed_advance_have_bounded_waits(self):
        self.run_scenario("""
current.state = 2;
read();
assert.equal(recover().action, 'play');
now = 30000;
assert.match(recover().error, /did not start/);
fire(1);
recover();
fire(0);
recover();
now = 35000;
assert.equal(recover().action, 'next');
fire(3);  // A next request can stall in buffering, not just ENDED.
now = 65000;
assert.match(recover().error, /did not advance/);
assert.equal(calls.next, 1);
""")

    def test_incomplete_or_replaced_player_is_not_controlled(self):
        self.run_scenario("""
read();
current.id = 'mismatched';
assert.equal(recover(), null);
current.id = 'first';
document.getElementById = () => ({...player});
assert.equal(recover(), null);
assert.deepEqual(calls, {play: 0, next: 0});
""")

    def test_repeated_video_id_at_a_new_playlist_position_is_started(self):
        self.run_scenario("""
current.ids = ['first', 'first', 'last'];
read();
recover();
current.index = 1;
fire(5);
assert.equal(recover().action, 'play');
assert.equal(calls.play, 1);
""")


@unittest.skipUnless(shutil.which("node"), "Node is needed to execute chat JavaScript")
class ChatVisibilityTests(unittest.TestCase):
    def test_chat_closes_without_duplicate_styles_or_reopening_collapsed_chat(self):
        harness = """
const assert = require('node:assert/strict');
let fullscreen = false, collapsed = false, chatPresent = true, style = null;
let sidebarClicks = 0, fullscreenClicks = 0, styles = 0;
const sidebarButton = {click: () => { sidebarClicks++; collapsed = true; }};
const fullscreenButton = {click: () => { fullscreenClicks++; collapsed = true; }};
const chat = {
    hasAttribute: name => name === 'collapsed' && collapsed,
    querySelector: selector => {
        assert.equal(selector, '#show-hide-button button');
        return sidebarButton;
    },
};
global.document = {
    fullscreenElement: null,
    querySelector: selector => {
        if (selector === 'ytd-live-chat-frame') return chatPresent ? chat : null;
        if (selector === '#movie_player.ytp-fullscreen') return fullscreen;
        assert.match(selector, /aria-label="Hide chat"/);
        assert.match(selector, /aria-pressed="true"/);
        return fullscreenButton;
    },
    getElementById: id => style && style.id === id ? style : null,
    createElement: tag => { assert.equal(tag, 'style'); return {}; },
    head: {appendChild: element => { style = element; styles++; }},
};
const hide = new Function(SCRIPT);
hide();
assert.equal(sidebarClicks, 1);
assert.match(style.textContent, /ytd-live-chat-frame/);
assert.match(style.textContent, /#chat-container/);
hide();
assert.equal(sidebarClicks, 1);
fullscreen = true;
collapsed = false;  // YouTube reopens chat on a new replay.
hide();
assert.equal(fullscreenClicks, 1);
chatPresent = false;  // Ordinary videos have no chat.
hide();
assert.equal(styles, 1);
""".replace("SCRIPT", json.dumps(browser_script("HIDE_CHAT_SCRIPT")))
        result = subprocess.run([shutil.which("node"), "-e", harness],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
