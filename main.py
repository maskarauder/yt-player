#!/usr/bin/env python3

from obs import OBSThread
from browser import perform

if __name__ == '__main__':

    obs_thread = OBSThread()
    obs_thread.start()

    perform()