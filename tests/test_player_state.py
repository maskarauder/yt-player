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


@unittest.skipUnless(shutil.which("node"), "Node is needed to execute player JavaScript")
class PlayerStateTests(unittest.TestCase):
    def run_scenario(self, scenario):
        tree = ast.parse((ROOT / "browser.py").read_text())
        script = next(
            ast.literal_eval(node.value)
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "PLAYER_STATE_SCRIPT"
                    for target in node.targets)
        )
        harness = """
const assert = require('node:assert/strict');
global.window = {};
global.location = {href: 'https://www.youtube.com/watch?v=first&list=PL1'};
const current = {ids: ['first', 'last'], index: 0, id: 'first', state: 1, ad: false};
const listeners = [];
const player = {
    getPlaylist: () => current.ids,
    getPlaylistIndex: () => current.index,
    getVideoUrl: () => 'https://www.youtube.com/watch?v=' + current.id,
    getPlayerState: () => current.state,
    classList: {contains: name => current.ad && name === 'ad-showing'},
    addEventListener: (name, callback) => listeners.push({name, callback}),
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
const read = new Function(SCRIPT);
""".replace("SCRIPT", json.dumps(script))
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


if __name__ == "__main__":
    unittest.main()
