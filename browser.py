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


def create_browser() -> None:
    global driver
    options = uc.ChromeOptions()
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
    print('Player metadata ready; entering fullscreen...')
    fullscreen_video()
    print('Monitoring playlist completion...')
    while not state['ended']:
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
