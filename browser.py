#!/usr/bin/env python3

import os
import random
from time import monotonic, sleep
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

import undetected_chromedriver as uc
from selenium.webdriver import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import StaleElementReferenceException, TimeoutException

from config import PAUSE_FOR_BROWSER_LOGIN, PLAYLIST_BLACKLIST, PLAYLISTS_LOCATION

SCRIPTDIR = os.path.dirname(os.path.realpath(__file__))
PAGE_TIMEOUT = 30
RETRY_DELAY = 30
PLAYLIST_LINKS = (
    'a.yt-lockup-view-model__content-image[href*="list="], '
    'ytd-grid-playlist-renderer a[href*="list="], '
    'ytd-playlist-renderer a[href*="list="]'
)
driver = None

# The watch-page player exposes the playlist/player methods also used by the
# YouTube player API. Latch the final ENDED event so autoplay cannot hide it
# between Selenium polls. Inspect IDs and position, never display titles.
PLAYER_STATE_SCRIPT = """
const player = document.getElementById('movie_player');
const methods = ['getPlaylist', 'getPlaylistIndex', 'getVideoUrl',
                 'getPlayerState', 'addEventListener'];
if (!player || methods.some(name => typeof player[name] !== 'function')) {
    return null;
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
        return {ready: ready, ended: tracker.ended};
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
    driver = uc.Chrome(headless=False, use_subprocess=True,
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
    while monotonic() < deadline:
        try:
            continuations = driver.find_elements(
                By.CSS_SELECTOR, 'ytd-browse ytd-continuation-item-renderer')
            visible = [item for item in continuations if item.is_displayed()]
            if not visible:
                return
            count = len(driver.find_elements(By.CSS_SELECTOR, PLAYLIST_LINKS))
            ActionChains(driver).scroll_to_element(visible[-1]).perform()
            WebDriverWait(driver, min(10, max(0, deadline - monotonic()))).until(
                lambda d: len(d.find_elements(By.CSS_SELECTOR, PLAYLIST_LINKS)) > count
                or not any(item.is_displayed() for item in d.find_elements(
                    By.CSS_SELECTOR, 'ytd-browse ytd-continuation-item-renderer')))
        except StaleElementReferenceException:
            continue
        except TimeoutException:
            # Keep the playlists already loaded if pagination stalls.
            return


def allow_setup_and_login() -> None:
    driver.get('https://www.youtube.com')
    input('Please login to YouTube and/or configure whatever extensions you want '
          'on this profile then press enter to continue...')


def get_available_playlists() -> list:
    driver.get(PLAYLISTS_LOCATION)
    WebDriverWait(driver, PAGE_TIMEOUT).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, PLAYLIST_LINKS)))
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


def get_playback_state():
    state = driver.execute_script(PLAYER_STATE_SCRIPT)
    return state if state and (state['ready'] or state['ended']) else False


def fullscreen_video() -> None:
    if driver.execute_script('return Boolean(document.fullscreenElement);'):
        return
    button = WebDriverWait(driver, PAGE_TIMEOUT).until(
        EC.element_to_be_clickable((By.CLASS_NAME, 'ytp-fullscreen-button')))
    ActionChains(driver).move_to_element(button).click().perform()


def watch_video(link) -> None:
    driver.get(link)
    if urlparse(link).path == '/playlist':
        first_video = WebDriverWait(driver, PAGE_TIMEOUT).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, 'a[href*="/watch?"][href*="list="]')))
        driver.get(first_video.get_attribute('href'))
    state = WebDriverWait(driver, PAGE_TIMEOUT).until(lambda _: get_playback_state())
    fullscreen_video()
    while not state['ended']:
        sleep(1)
        # Wait through temporary player replacement during playlist navigation.
        # A missing/broken player eventually times out and is retried by perform.
        state = WebDriverWait(driver, PAGE_TIMEOUT).until(lambda _: get_playback_state())
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
            watch_video(selected)
            last_selected = selected
        except (TimeoutException, StaleElementReferenceException) as exc:
            print(f'YouTube did not finish loading. Retrying in 30 seconds: {exc}')
            sleep(RETRY_DELAY)
