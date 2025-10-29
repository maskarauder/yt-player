# yt-player
Plays a random YouTube playlist from a list of playlists.

## Quick Start
- [Download source as a ZIP](./archive/refs/heads/main.zip)
- Unzip the source
- Edit main.py to change the URL pointed to by playlists_location if you're not @BurrPlays1
- Install python
- Install the packages in requirements.txt
- Run the code with `python .\main.py`
- Follow the prompt, hit enter to start it running.

## Installing Python
Open a PowerShell windows and type the python command.  

If you have python installed, it should drop you into a python interpreter. Type "quit()" to exit.  
Otherwise it should open the Windows store, press install to install the latest version of python3.  

Install python dependencies:  
Right click the folder where you unzipped this project without selecting a file.  
Click "Open in terminal" from the presented options.  
![Open in Terminal option](./images/terminal-option.png)  

In the terminal, type the following command:  
`python -m pip install --upgrade -r requirements.txt`

## Run the code
`python .\main.py`

or if your profile is already setup you can skip the setup prompt with

`python .\main.py --skip-prompt`

## Troubleshooting
If it tells you you are using Chrome 1.\<some number\> and you need to be using Chrome 1.\<some higher number\>, update Chrome by going to the About Chrome tab under Help and following the prompt. It should then work.  
![Help -> About Google Chrome](./images/chrome-help.png)


![Chrome Version](./images/chrome-help-version.png)