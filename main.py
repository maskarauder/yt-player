#!/usr/bin/env python3

import os
import sys
SCRIPTDIR = os.path.dirname(os.path.realpath(__file__))

import asyncio
from time import sleep
import nodriver as uc
from bs4 import BeautifulSoup as bs
import random

driver = None


playlists_location = 'https://www.youtube.com/@BurrPlays1/playlists'


async def scroll_to_load_content(page: uc.Tab) -> None:
    try:
        while True:
            bottom_of_list = await page.find('ytd-continuation-item-renderer', timeout=1)
            if bottom_of_list:
                await bottom_of_list.scroll_into_view()
                await asyncio.sleep(.5)
            else:
                break
    except TimeoutError:
        return


async def allow_setup_and_login() -> None:
    await driver.get('https://www.youtube.com')
    await asyncio.sleep(1)

    input('Please login to YouTube and/or configure whatever extensions you want on this profile then press enter to continue...')


async def get_available_playlists() -> list:
    page = await driver.get(playlists_location)
    await asyncio.sleep(1)
    await scroll_to_load_content(page)

    page_source = await page.get_content()
    soup = bs(page_source, 'html.parser')
    res = soup.find_all('a', attrs={'class': 'yt-lockup-view-model__content-image'})

    vids = []
    for i in res:
        vids.append('https://www.youtube.com' + i.get('href'))
    return vids


async def get_last_video_in_playlist(page: uc.Tab) -> str:
    page_source = await page.get_content()
    soup = bs(page_source, 'html.parser')
    res = soup.find_all('span', attrs={'id': 'video-title', 'class': 'style-scope ytd-playlist-panel-video-renderer'})

    return res[-1].get('title')


async def get_current_video_title(page: uc.Tab) -> str:
    page_source = await page.get_content()
    soup = bs(page_source, 'html.parser')
    title = soup.find('h1', attrs={'class': 'style-scope ytd-watch-metadata'}) \
                .find('yt-formatted-string')

    return title.get('title')


async def get_video_duration(page: uc.Tab) -> int:
    page_source = await page.get_content()
    soup = bs(page_source, 'html.parser')
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

async def fullscreen_video(page: uc.Tab) -> None:
    fullscreen_button = await page.find('.ytp-fullscreen-button', timeout=10)
    if fullscreen_button:
        await fullscreen_button.mouse_move()
        await asyncio.sleep(0.5)
        await fullscreen_button.click()

async def watch_video(link) -> None:
    page = await driver.get(link)
    await asyncio.sleep(5)

    last_video_in_list = await get_last_video_in_playlist(page)
    current_video = await get_current_video_title(page)

    await fullscreen_video(page)
    while current_video != last_video_in_list:
        await asyncio.sleep(10)
        current_video = await get_current_video_title(page)

    duration = await get_video_duration(page)
    print(f'Last vid detected! Sleeping for {duration}')
    await asyncio.sleep(duration)


async def main():
    global driver
    driver = await uc.start(user_data_dir=os.path.join(SCRIPTDIR, 'profile'), headless=False)

    if not '--skip-prompt' in sys.argv:
        await allow_setup_and_login()

    last_selected = None
    currently_selected = None
    while True:
        playlists = await get_available_playlists()

        while currently_selected == last_selected:
            currently_selected = random.choice(playlists)

        last_selected = currently_selected

        await watch_video(currently_selected)

if __name__ == '__main__':
    uc.loop().run_until_complete(main())
