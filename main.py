#!/usr/bin/env python3

import os
import sys
SCRIPTDIR = os.path.dirname(os.path.realpath(__file__))

from time import sleep
from selenium.webdriver import ActionChains, Chrome, ChromeOptions
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.support.ui import WebDriverWait
from bs4 import BeautifulSoup as bs
from bs4.element import Tag
from pprint import pprint
import random

options = ChromeOptions()
options.add_argument(f'--user-data-dir={os.path.join(SCRIPTDIR, 'profile')}')
options.add_argument('--profile-directory=Profile 1')
driver = Chrome(options=options)


playlists_location = 'https://www.youtube.com/@BurrPlays1/playlists'


def scroll_to_load_content() -> None:
    try:
        while True:
            bottom_of_list = driver.find_element(By.TAG_NAME, 'ytd-continuation-item-renderer')
            ActionChains(driver) \
                .scroll_to_element(bottom_of_list) \
                .perform()
            sleep(.5)
    except NoSuchElementException:
        return


def allow_setup_and_login() -> None:
    driver.get('https://www.youtube.com')
    sleep(1)

    input('Please login to YouTube and/or configure whatever extensions you want on this profile then press enter to continue...')


def get_available_playlists() -> list:
    driver.get(playlists_location)
    sleep(1)
    scroll_to_load_content()

    soup = bs(driver.page_source, 'html.parser')
    res = soup.find_all('a', attrs={'class': 'yt-lockup-view-model__content-image'})

    vids = []
    for i in res:
        vids.append('https://www.youtube.com' + i.get('href'))
    return vids


def get_last_video_in_playlist() -> str:
    soup = bs(driver.page_source, 'html.parser')
    res = soup.find_all('span', attrs={'id': 'video-title', 'class': 'style-scope ytd-playlist-panel-video-renderer'})

    return res[-1].get('title')


def get_current_video_title() -> str:
    soup = bs(driver.page_source, 'html.parser')
    title = soup.find('h1', attrs={'class': 'style-scope ytd-watch-metadata'}) \
                .find('yt-formatted-string')

    return title.get('title')


def get_video_duration() -> int:
    soup = bs(driver.page_source, 'html.parser')
    #end_of_video = soup.select_one('meta[itemprop="duration"][content]')['content']
    end_of_video = soup.find('span', attrs={'class': 'ytp-time-duration'})
    if end_of_video is None:
        raise Exception('Unable to determine when we should go to the next video!')
    
    duration = end_of_video.text
    
    tokens = duration.split(':')
    if len(tokens) == 3:
        h = int(tokens[0]) * 60 * 60
        m = int(tokens[1]) * 60
        return int(tokens[2]) + m + h
    elif len(tokens) == 2:
        m = int(tokens[0]) * 60
        return int(tokens[1]) + m

    return int(duration)


def watch_video(link) -> None:
    driver.get(link)
    sleep(5)

    last_video_in_list = get_last_video_in_playlist()
    current_video = get_current_video_title()

    while current_video != last_video_in_list:
        sleep(10)
        current_video = get_current_video_title()

    duration = get_video_duration()
    print(f'Last vid detected! Sleeping for {duration}')
    sleep(duration)


if __name__ == '__main__':

    if not '--skip-prompt' in sys.argv:
        allow_setup_and_login()

    last_selected = None
    currently_selected = None
    while True:
        playlists = get_available_playlists()

        while currently_selected == last_selected:
            currently_selected = random.choice(playlists)

        last_selected = currently_selected

        watch_video(currently_selected)