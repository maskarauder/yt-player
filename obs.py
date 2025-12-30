#!/usr/bin/env python3

# OBS API
import obsws_python as obs

# App Specific
from config import *
from time import sleep
from pprint import pprint
from threading import Thread

class OBSThread(Thread):
    uptime = -1
    ready_to_die = False

    def on_exit_started(self, _):
        """OBS is dying."""
        print("OBS closing!")
        self.ready_to_die = True

    def on_stream_state_changed(self, data):
        """Stream has either started or stopped."""

        if str(data.output_state) == "OBS_WEBSOCKET_OUTPUT_STARTING":
            print('Stream is starting...')
            return
        elif str(data.output_state) == "OBS_WEBSOCKET_OUTPUT_STOPPING":
            print('Stream is stopping...')
            return
        elif str(data.output_state) == "OBS_WEBSOCKET_OUTPUT_STOPPED":
            if self.uptime >= RESTART_INTERVAL or RESTART_ON_UNEXPECTED_END:
                if self.uptime < RESTART_INTERVAL:
                    print('Stream down unexpectedly? Attempting to restart it.')
                else:
                    print('Stream restarting at regularly scheduled interval.')
                    self.uptime = 0
                sleep(2) # it needs a sec, give it 2 to be safe.
                self.cl.start_stream()
            return
        elif data.output_active:
            # Stream switched on
            self.uptime = 0
                
    def __init__(self):
        self.cl = None
        self.ecl = None
        Thread.__init__(self)

    def __del__(self):
        try:
            if self.ecl:
                self.ecl.disconnect()
        except Exception:
            pass

        try:
            if self.cl:
                self.cl.disconnect()
        except Exception:
            pass

    def run(self) -> None:
        while not self.connect_to_obs():
            print('Failed to connect to OBS. Retrying in 1 sec...')
            sleep(1)
            return

        # If stream is already running, grab the current uptime.        
        stream_status = self.cl.get_stream_status()
        if not stream_status.output_active:
            print('Waiting for stream to start...')

        while not stream_status.output_active:
            stream_status = self.cl.get_stream_status()
            sleep(1)

        self.uptime = int(stream_status.output_duration // 1000)
        print(f'Stream is up. Restarting stream in {RESTART_INTERVAL - self.uptime} seconds.')

        # Start event listeners
        self.ecl.callback.register(
            [
                self.on_exit_started,
                self.on_stream_state_changed,
            ]
        )

        while not self.ready_to_die:
            if self.uptime >= RESTART_INTERVAL:
                self.cl.stop_stream()
                # Event Client will handle the restart.

            sleep(10)
            
            stream_status = self.cl.get_stream_status()
            if not stream_status.output_active:
                print('Stream failed to restart.')
                self.ready_to_die = True
            self.uptime = int(stream_status.output_duration // 1000)

        print('OBS thread is dying. sleepiHYUCK')


    def connect_to_obs(self) -> bool:
        global source

        cl = obs.ReqClient(host=OBS_HOST, port=OBS_PORT, password=OBS_WEBSOCKET_PASSWORD, timeout=3)
        if cl is None:
            return False

        ecl = obs.EventClient(host=OBS_HOST, port=OBS_PORT, password=OBS_WEBSOCKET_PASSWORD, timeout=3)
        if ecl is None:
            cl.disconnect()
            return False
        
        self.cl = cl
        self.ecl = ecl
        return True