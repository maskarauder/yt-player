#!/usr/bin/env python3

from queue import Empty, SimpleQueue
from threading import Event, Thread
from time import monotonic

import obsws_python as obs

from config import (
    OBS_HOST,
    OBS_PORT,
    OBS_WEBSOCKET_PASSWORD,
    RESTART_INTERVAL,
    RESTART_ON_UNEXPECTED_END,
)


class OBSThread(Thread):
    POLL_INTERVAL = 1
    RESTART_DELAY = 2
    TRANSITION_TIMEOUT = 30
    MAX_RESTART_ATTEMPTS = 3

    def __init__(self):
        super().__init__(name='OBS monitor')
        self.cl = None
        self.ecl = None
        self.uptime = -1
        self._stop_event = Event()
        self._events = SimpleQueue()
        self._active = False
        self._has_streamed = False
        self._restart_phase = None
        self._deadline = 0
        self._attempts = 0
        self._restart_failed = False

    def stop(self):
        """Stop monitoring without changing OBS's current stream state."""
        self._stop_event.set()

    def on_exit_started(self, _):
        print('OBS closing!')
        self.stop()

    def on_stream_state_changed(self, data):
        # This runs on the event client's thread. Only run() sends requests.
        self._events.put(str(data.output_state))

    def connect_to_obs(self):
        options = dict(host=OBS_HOST, port=OBS_PORT,
                       password=OBS_WEBSOCKET_PASSWORD, timeout=3)
        # Assign immediately so a partially successful connection gets closed.
        self.cl = obs.ReqClient(**options)
        if self._stop_event.is_set():
            return
        self.ecl = obs.EventClient(**options)
        self.ecl.callback.register([
            self.on_exit_started,
            self.on_stream_state_changed,
        ])
        print('Connected to OBS.')

    def _disconnect(self):
        for name in ('ecl', 'cl'):
            client = getattr(self, name)
            setattr(self, name, None)
            if client is not None:
                try:
                    client.disconnect()
                except Exception as exc:
                    print(f'Could not close OBS {name} connection: {exc}')
        # Old connection events must not affect a new connection.
        self._events = SimpleQueue()

    def _failed_restart(self, message):
        print(f'{message} Waiting for a manual stream restart.')
        self._restart_phase = None
        self._restart_failed = True

    def _check_stream(self, status, now):
        was_active, previous_uptime = self._active, self.uptime
        self._active = status.output_active
        self.uptime = int(status.output_duration // 1000)

        if self._active:
            self._has_streamed = True
            if not was_active or self.uptime < previous_uptime:
                self._restart_failed = False

            if self._restart_phase in ('delay', 'starting'):
                print('Stream is running again.')
                self._restart_phase = None
            elif self._restart_phase == 'stopping':
                if now >= self._deadline:
                    if self._attempts >= self.MAX_RESTART_ATTEMPTS:
                        self._failed_restart('Stream did not stop after three attempts.')
                    else:
                        self._attempts += 1
                        self._deadline = now + self.TRANSITION_TIMEOUT
                        print(f'Retrying stream stop (attempt {self._attempts}).')
                        self.cl.stop_stream()
                return

            if (not self._restart_failed and RESTART_INTERVAL >= 0
                    and self.uptime >= RESTART_INTERVAL):
                print('Restarting stream at the configured interval.')
                self._restart_phase = 'stopping'
                self._deadline = now + self.TRANSITION_TIMEOUT
                self._attempts = 1
                self.cl.stop_stream()
            return

        if self._restart_phase == 'stopping':
            self._restart_phase = 'delay'
            self._deadline = now + self.RESTART_DELAY
            self._attempts = 0
        elif self._restart_phase == 'starting':
            if now < self._deadline:
                return
            if self._attempts >= self.MAX_RESTART_ATTEMPTS:
                self._failed_restart('Stream did not restart after three attempts.')
                return
            print('Stream has not started yet; retrying shortly.')
            self._restart_phase = 'delay'
            self._deadline = now + self.RESTART_DELAY
        elif (self._restart_phase is None and self._has_streamed
                and RESTART_ON_UNEXPECTED_END and not self._restart_failed):
            print('Stream stopped unexpectedly; restarting shortly.')
            self._restart_phase = 'delay'
            self._deadline = now + self.RESTART_DELAY
            self._attempts = 0

        if self._restart_phase == 'delay' and now >= self._deadline:
            self._attempts += 1
            self._restart_phase = 'starting'
            self._deadline = now + self.TRANSITION_TIMEOUT
            print(f'Starting stream (attempt {self._attempts}).')
            self.cl.start_stream()

    def run(self):
        try:
            while not self._stop_event.is_set():
                try:
                    if self.cl is None:
                        self.connect_to_obs()
                    if self._stop_event.is_set():
                        break
                    if not self.ecl.worker.is_alive():
                        raise ConnectionError('OBS event connection closed')
                    while True:
                        try:
                            state = self._events.get_nowait()
                        except Empty:
                            break
                        if state == 'OBS_WEBSOCKET_OUTPUT_STARTED':
                            self._has_streamed = True
                    status = self.cl.get_stream_status()
                    if not self._stop_event.is_set():
                        self._check_stream(status, monotonic())
                except Exception as exc:
                    print(f'OBS connection/request failed: {exc}. Retrying in 1 second.')
                    self._disconnect()
                if self._stop_event.wait(self.POLL_INTERVAL):
                    break
        finally:
            self._disconnect()
