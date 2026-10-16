# yt-player
Plays a random YouTube playlist from a list of playlists. Restarts the stream within OBS after a period of time to avoid stream dying.

## Quick Start
- [Download source as a ZIP](./archive/refs/heads/main.zip)
- Unzip the source
- Install [uv](https://docs.astral.sh/uv/getting-started/installation/) (`winget install --id astral-sh.uv -e` on Windows)
- Reopen a terminal in the project folder.
- Edit config.py to change any of the configuration variables.
- Setup OBS WebSocket to allow connections.
- Run the code with `uv run main.py`
- Follow the prompt, hit enter to start it running.

`uv` manages Python, dependencies, and the virtual environment automatically.  
Install Google Chrome separately.

## Setting up the OBS WebSocket
In OBS, navigate to Tools -> WebSocket Server Settings.  
![WebSocket server settings](./images/websocket-server-settings.png)  

Ensure the "Enable WebSocket server" checkbox is checked, and Enable Authentication if you wish to run securely.  
You can then click to Copy the Server Password from the "Show Connect Info" screen and paste it between the quotation marks in the config.py.  
![WebSocket configuration](./images/websocket-connection-info.png)

## Run the code
`uv run main.py`

or if your profile is already setup you can skip the setup prompt by modifying ![config.py](./config.py) with the following setting

`PAUSE_FOR_BROWSER_LOGIN           = False`

## Playback and recovery

- Set `ENABLE_OBS = False` in `config.py` to test just the browser. No OBS module or monitor is started; the default is `True`.
- Add playlist IDs or playlist URLs to `PLAYLIST_BLACKLIST` in `config.py`. Matching uses the `list=` ID, so different video URLs for the same playlist are also excluded. If everything is excluded, the script waits and checks again.

For example:

```python
ENABLE_OBS = False
PLAYLIST_BLACKLIST = [
    'PL_REPLACE_WITH_PLAYLIST_ID',
    'https://www.youtube.com/playlist?list=PL_ANOTHER_ID',
]
```

- The same playlist is never selected twice in a row. If no different playlist is available, the script waits 30 seconds and checks again.
- Playlist completion uses the final video's ID, position, and ended state. Pausing or buffering does not count as finishing the video.
- Slow YouTube page loads are retried. Closing Chrome or stopping the script also stops the OBS monitor and closes its connections; it does not stop an active OBS stream.
- Set `RESTART_INTERVAL = -1` to disable scheduled restarts. `RESTART_ON_UNEXPECTED_END` separately controls recovery after an unexpected stop.
- OBS connection failures are retried. Stream restarts allow 30 seconds per stop/start attempt, with up to three attempts before waiting for a manual stream restart.

## Regression tests
Run `uv run python -m unittest discover -s tests -v`. The tests use fake browser and OBS clients, so they do not require a running Chrome or OBS instance. Player JavaScript tests also run when Node.js is installed; otherwise those tests are skipped.

## Troubleshooting
The console reports `Opening playlists page`, `Scrolling the playlists page`, `Found ... playlists`, and `Selected playlist`. If playback does not start, the last message identifies the stage that needs attention. A navigation timeout is allowed to use content already loaded; playlist discovery does not depend on thumbnail CSS classes or a visible loading marker.

If it tells you you are using Chrome 1.\<some number\> and you need to be using Chrome 1.\<some higher number\>, update Chrome by going to the About Chrome tab under Help and following the prompt. It should then work.  
![Help -> About Google Chrome](./images/chrome-help.png)


![Chrome Version](./images/chrome-help-version.png)
