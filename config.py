#!/usr/bin/env python3

# Browser Options
PAUSE_FOR_BROWSER_LOGIN           = False           # Pause to let the user log into the browser, should only be necessary once.
PLAYLISTS_LOCATION                = 'https://www.youtube.com/@BurrPlays1/playlists'

# OBS Options
OBS_HOST:str                      = "localhost"     # Default: OBS is on the system running this script.
OBS_PORT:int                      = 4455            # From OBS, the default is 4455
OBS_WEBSOCKET_PASSWORD:str        = ""              # From OBS, empty string if no password
RESTART_INTERVAL:int              = 48 * 60 * 60    # Default: 48 hours from the time stream is started, if not interrupted, -1 to disable.
RESTART_ON_UNEXPECTED_END:bool    = True            # Default: Restart if stream dies unexpectedly. Note: You'll need to close OBS to kill the stream.