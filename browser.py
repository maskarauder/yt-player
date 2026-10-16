#!/usr/bin/env python3

import os
import random
from time import monotonic, sleep
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import StaleElementReferenceException, TimeoutException

from config import PAUSE_FOR_BROWSER_LOGIN, PLAYLIST_BLACKLIST, PLAYLISTS_LOCATION

SCRIPTDIR = os.path.dirname(os.path.realpath(__file__))
PAGE_TIMEOUT = 30
RETRY_DELAY = 30
PLAYLIST_LINKS = (
    'ytd-browse:not([hidden]) a[href*="list="], '
    '#page-manager > :not([hidden]) a[href*="list="], '
    'main a[href*="list="], [role="main"] a[href*="list="]'
)
driver = None

# The watch-page player exposes the playlist/player methods also used by the
# YouTube player API. Latch the final ENDED event so autoplay cannot hide it
# between Selenium polls. Inspect IDs and position, never display titles.
PLAYER_STATE_SCRIPT = """
const player = document.getElementById('movie_player');
const methods = ['getPlaylist', 'getPlaylistIndex', 'getVideoUrl',
                 'getPlayerState', 'addEventListener'];
if (!player) {
    return {ready: false, ended: false, reason: 'movie_player not found'};
}
const missingMethods = methods.filter(name => typeof player[name] !== 'function');
if (missingMethods.length) {
    return {ready: false, ended: false, reason: 'player methods unavailable',
            missingMethods: missingMethods};
}
if (!window.__ytPlayerCompletion || window.__ytPlayerCompletion.player !== player) {
    const tracker = {player: player, ended: false};
    window.__ytPlayerCompletion = tracker;
    tracker.read = function(state) {
        const ids = player.getPlaylist() || [];
        const index = player.getPlaylistIndex();
        const id = new URL(player.getVideoUrl(), location.href).searchParams.get('v');
        const advertising = player.classList.contains('ad-showing') ||
                            player.classList.contains('ad-interrupting');
        const ready = ids.length > 0 && index >= 0 && index < ids.length &&
                      id === ids[index];
        if (ready && !advertising && index === ids.length - 1 && state === 0) {
            tracker.ended = true;
        }
        return {ready: ready, ended: tracker.ended, playerState: state,
                playlistLength: ids.length, playlistIndex: index,
                videoId: id, expectedVideoId: ids[index] ?? null,
                advertising: advertising};
    };
    window.__ytPlayerStateChanged = function(event) {
        const state = typeof event === 'number' ? event : event.data;
        tracker.read(state);
    };
    player.addEventListener('onStateChange', '__ytPlayerStateChanged');
}
return window.__ytPlayerCompletion.read(player.getPlayerState());
"""

HIDE_CHAT_SCRIPT = """
const chat = document.querySelector('ytd-live-chat-frame');
if (chat && !chat.hasAttribute('collapsed')) {
    const fullscreen = document.fullscreenElement ||
                       document.querySelector('#movie_player.ytp-fullscreen');
    const close = fullscreen ? document.querySelector(
        '.ytp-fullscreen-quick-actions button[aria-label="Hide chat"], ' +
        '.ytp-fullscreen-quick-actions button[aria-label="Live chat"][aria-pressed="true"]'
    ) : chat.querySelector('#show-hide-button button');
    // Use YouTube's own control to release the space reserved for the panel.
    if (close) close.click();
}
// Keep late-loaded/reopened chat hidden across YouTube's in-page navigation.
if (!document.getElementById('yt-player-hide-chat')) {
    const style = document.createElement('style');
    style.id = 'yt-player-hide-chat';
    style.textContent = 'ytd-live-chat-frame, #chat-container {display:none!important}';
    document.head.appendChild(style);
}
"""

RECOVER_PLAYBACK_SCRIPT = """
const tracker = window.__ytPlayerCompletion;
if (!tracker || tracker.player !== document.getElementById('movie_player')) return null;
const player = tracker.player;
// Read again immediately before acting, so native autoplay cannot cause a skip.
const state = tracker.read(player.getPlayerState());
if (!state.ready || state.ended) return null;
const now = performance.now();
const key = state.playlistIndex + ':' + state.videoId;
if (!tracker.recovery || tracker.recovery.key !== key) {
    tracker.recovery = {key: key, played: false, since: now,
                        playRequested: false, endedAt: null, nextRequestedAt: null};
}
const recovery = tracker.recovery;
if (state.advertising) {
    recovery.since = now;
    recovery.endedAt = null;
    if (recovery.nextRequestedAt !== null) recovery.nextRequestedAt = now;
    return null;
}
const fail = reason => ({error: reason, state: state});
if (recovery.nextRequestedAt !== null && now - recovery.nextRequestedAt >= arguments[0]) {
    return fail('The playlist did not advance after requesting the next video.');
}
if (state.playerState === 1) {
    recovery.played = true;
    recovery.endedAt = null;
    return null;
}
if (state.playerState === 0 && state.playlistIndex < state.playlistLength - 1) {
    if (recovery.endedAt === null) recovery.endedAt = now;
    if (recovery.nextRequestedAt === null && now - recovery.endedAt >= 5000) {
        if (typeof player.nextVideo !== 'function') return fail('nextVideo is unavailable.');
        recovery.nextRequestedAt = now;
        player.nextVideo();
        return {action: 'next', state: state};
    }
    return null;
}
recovery.endedAt = null;
// Start each new item once; leave a manual pause alone after it has played.
if (!recovery.played) {
    if (now - recovery.since >= arguments[0]) {
        return fail('The video did not start playing.');
    }
    if ([-1, 2, 5].includes(state.playerState) && !recovery.playRequested) {
        if (typeof player.playVideo !== 'function') return fail('playVideo is unavailable.');
        recovery.playRequested = true;
        player.playVideo();
        return {action: 'play', state: state};
    }
}
return null;
"""


def create_browser() -> None:
    global driver
    options = uc.ChromeOptions()
    options.add_argument('--autoplay-policy=no-user-gesture-required')
    # YouTube can keep loading secondary resources after the page is usable.
    options.page_load_strategy = 'eager'
    driver = uc.Chrome(options=options, headless=False, use_subprocess=True,
                       user_data_dir=os.path.join(SCRIPTDIR, 'profile'))
    driver.set_page_load_timeout(PAGE_TIMEOUT)


def close_browser() -> None:
    global driver
    current, driver = driver, None
    if current is not None:
        try:
            current.quit()
        except Exception as exc:
            # A manually closed browser may already have disconnected.
            print(f'Could not close browser cleanly: {exc}')


def scroll_to_load_content() -> None:
    deadline = monotonic() + 60
    print('Scrolling the playlists page...')
    while monotonic() < deadline:
        try:
            count = len(driver.find_elements(By.CSS_SELECTOR, PLAYLIST_LINKS))
            # Scroll even if cards or the continuation marker are not ready yet.
            # A hidden/zero-size loading marker must not prevent pagination.
            driver.execute_script('''
                const page = document.scrollingElement || document.documentElement;
                window.scrollTo(0, page.scrollHeight);
            ''')
            wait = PAGE_TIMEOUT if count == 0 else 10
            WebDriverWait(driver, min(wait, max(0, deadline - monotonic()))).until(
                lambda d: len(d.find_elements(By.CSS_SELECTOR, PLAYLIST_LINKS)) > count)
        except StaleElementReferenceException:
            continue
        except TimeoutException:
            # No new cards arrived. Keep everything already loaded.
            return
    print('Reached the scrolling time limit; using the playlists loaded so far.')


def allow_setup_and_login() -> None:
    driver.get('https://www.youtube.com')
    input('Please login to YouTube and/or configure whatever extensions you want '
          'on this profile then press enter to continue...')


def get_available_playlists() -> list:
    print(f'Opening playlists page: {PLAYLISTS_LOCATION}')
    try:
        driver.get(PLAYLISTS_LOCATION)
    except TimeoutException:
        print('Navigation timed out; checking the content already loaded.')
    # Do not require playlist cards before scrolling: lazy loading may need a
    # scroll first, and thumbnail CSS classes vary between YouTube layouts.
    WebDriverWait(driver, PAGE_TIMEOUT).until(
        EC.presence_of_element_located((By.TAG_NAME, 'body')))
    scroll_to_load_content()
    playlists = {}
    for item in driver.find_elements(By.CSS_SELECTOR, PLAYLIST_LINKS):
        href = item.get_attribute('href')
        if not href:
            continue
        parsed = urlparse(urljoin('https://www.youtube.com', href))
        query = parse_qs(parsed.query)
        playlist_id = query.get('list', [None])[0]
        if not playlist_id:
            continue
        # Deduplicate by playlist ID, including cards with different video URLs.
        params = {'list': playlist_id}
        if query.get('v'):
            params['v'] = query['v'][0]
        path = '/watch' if 'v' in params else '/playlist'
        playlists.setdefault(playlist_id, 'https://www.youtube.com' + path + '?' + urlencode(params))
    print(f'Found {len(playlists)} playlists.')
    if not playlists:
        print(f'No playlist links were found at {driver.current_url}. '
              'Check that the playlists page is visible and no sign-in or consent prompt is blocking it.')
    return list(playlists.values())


def playlist_identity(link):
    link = link.strip()
    return parse_qs(urlparse(link).query).get('list', [link])[0]


def choose_playlist(playlists, last_selected):
    previous_id = playlist_identity(last_selected) if last_selected else None
    blocked_ids = {playlist_identity(entry) for entry in PLAYLIST_BLACKLIST}
    alternatives = [link for link in playlists
                    if playlist_identity(link) != previous_id
                    and playlist_identity(link) not in blocked_ids]
    return random.choice(alternatives) if alternatives else None


def wait_for_playback_state():
    last_state = None

    def ready(_):
        nonlocal last_state
        last_state = driver.execute_script(PLAYER_STATE_SCRIPT)
        return last_state if last_state and (last_state['ready'] or last_state['ended']) else False

    try:
        return WebDriverWait(driver, PAGE_TIMEOUT).until(ready)
    except TimeoutException as exc:
        raise TimeoutException(
            f'Playback/playlist metadata did not become ready within {PAGE_TIMEOUT}s. '
            f'Last player state: {last_state!r}') from exc


def fullscreen_video() -> None:
    def is_fullscreen(d):
        return d.execute_script('''
            return Boolean(document.fullscreenElement ||
                           document.querySelector('#movie_player.ytp-fullscreen'));
        ''')

    if is_fullscreen(driver):
        return
    stage = 'waiting for the video player to become visible'
    try:
        player = WebDriverWait(driver, PAGE_TIMEOUT).until(
            EC.visibility_of_element_located((By.ID, 'movie_player')))
        # Target the player so auto-hidden controls cannot block fullscreen,
        # and the shortcut cannot accidentally be typed into the search box.
        stage = 'sending the fullscreen shortcut to the video player'
        player.send_keys('f')
        stage = 'waiting for fullscreen after sending the shortcut'
        WebDriverWait(driver, PAGE_TIMEOUT).until(is_fullscreen)
    except TimeoutException as exc:
        raise TimeoutException(
            f'Entering fullscreen timed out while {stage}.') from exc


def hide_live_chat() -> None:
    driver.execute_script(HIDE_CHAT_SCRIPT)


def recover_playback() -> None:
    result = driver.execute_script(RECOVER_PLAYBACK_SCRIPT, PAGE_TIMEOUT * 1000)
    if not result:
        return
    if 'error' in result:
        raise TimeoutException(
            f"Automatic playback failed: {result['error']} Last player state: {result['state']!r}")
    state = result['state']
    print(f"Playback recovery: {result['action']} for video {state['videoId']} "
          f"(playlist item {state['playlistIndex'] + 1}/{state['playlistLength']}).")


def watch_video(link) -> None:
    print(f'Opening selected playlist/video: {link}')
    driver.get(link)
    if urlparse(link).path == '/playlist':
        print('Waiting for the first video link in the playlist...')
        first_video = WebDriverWait(driver, PAGE_TIMEOUT).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, 'a[href*="/watch?"][href*="list="]')))
        print('Opening the first playlist video...')
        driver.get(first_video.get_attribute('href'))
    print('Waiting for playback/playlist metadata...')
    state = wait_for_playback_state()
    hide_live_chat()
    print('Player metadata ready; entering fullscreen...')
    fullscreen_video()
    print('Monitoring playlist completion...')
    while not state['ended']:
        hide_live_chat()
        recover_playback()
        sleep(1)
        # Wait through temporary player replacement during playlist navigation.
        # A missing/broken player eventually times out and is retried by perform.
        state = wait_for_playback_state()
    print('Playlist finished.')


def perform() -> None:
    create_browser()
    if PAUSE_FOR_BROWSER_LOGIN:
        allow_setup_and_login()

    last_selected = None
    while True:
        try:
            playlists = get_available_playlists()
            selected = choose_playlist(playlists, last_selected)
            if selected is None:
                print('No different, non-blacklisted playlist is available. Checking again in 30 seconds...')
                sleep(RETRY_DELAY)
                continue
            print(f'Selected playlist: {selected}')
            watch_video(selected)
            last_selected = selected
        except (TimeoutException, StaleElementReferenceException) as exc:
            print(f'YouTube check failed ({type(exc).__name__}). Retrying in 30 seconds: {exc}')
            sleep(RETRY_DELAY)
