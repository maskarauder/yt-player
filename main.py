#!/usr/bin/env python3

from browser import close_browser, perform
from config import ENABLE_OBS


def run() -> None:
    obs_thread = None
    try:
        if ENABLE_OBS:
            from obs import OBSThread
            obs_thread = OBSThread()
            obs_thread.start()
        perform()
    except KeyboardInterrupt:
        print('Stopping...')
    finally:
        try:
            if obs_thread is not None:
                obs_thread.stop()
                if obs_thread.ident is not None:
                    obs_thread.join()
        finally:
            close_browser()


if __name__ == '__main__':
    run()
