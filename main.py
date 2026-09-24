#!/usr/bin/env python3

from obs import OBSThread
from browser import close_browser, perform

def run() -> None:
    obs_thread = OBSThread()
    try:
        obs_thread.start()
        perform()
    except KeyboardInterrupt:
        print('Stopping...')
    finally:
        try:
            obs_thread.stop()
            if obs_thread.ident is not None:
                obs_thread.join()
        finally:
            close_browser()


if __name__ == '__main__':
    run()
