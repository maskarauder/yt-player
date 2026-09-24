"""OBS regressions use fake clients; no OBS server or dependencies are needed."""

import importlib.util
from pathlib import Path
import sys
from threading import Event, get_ident
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch


spec = importlib.util.spec_from_file_location(
    'yt_player_obs_under_test', Path(__file__).resolve().parents[1] / 'obs.py')
obs_module = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {'obsws_python': ModuleType('obsws_python')}):
    spec.loader.exec_module(obs_module)


def status(active, seconds=0):
    return SimpleNamespace(output_active=active, output_duration=seconds * 1000)


def event_client():
    client = Mock()
    client.worker.is_alive.return_value = True
    return client


class OBSRegressionTests(unittest.TestCase):
    def setUp(self):
        self.settings = patch.multiple(
            obs_module, RESTART_INTERVAL=100, RESTART_ON_UNEXPECTED_END=True)
        self.settings.start()
        self.addCleanup(self.settings.stop)
        self.worker = obs_module.OBSThread()
        self.requests = Mock()
        self.worker.cl = self.requests
        self.output = patch('builtins.print')
        self.output.start()
        self.addCleanup(self.output.stop)

    def test_minus_one_disables_periodic_and_unexpected_restart_when_configured(self):
        obs_module.RESTART_INTERVAL = -1
        obs_module.RESTART_ON_UNEXPECTED_END = False
        self.worker._check_stream(status(True, 1000000), 0)
        self.worker._check_stream(status(False), 1)
        self.worker._check_stream(status(False), 1000)
        self.requests.stop_stream.assert_not_called()
        self.requests.start_stream.assert_not_called()

    def test_manual_stop_waits_when_unexpected_restart_is_disabled(self):
        obs_module.RESTART_ON_UNEXPECTED_END = False
        self.worker._check_stream(status(True, 10), 0)
        self.worker._check_stream(status(False), 1)
        self.worker._check_stream(status(False), 1000)
        self.requests.start_stream.assert_not_called()
        # Monitoring resumes when the user starts a new stream.
        self.worker._check_stream(status(True, 100), 1001)
        self.requests.stop_stream.assert_called_once()

    def test_scheduled_restart_works_with_unexpected_restart_disabled(self):
        obs_module.RESTART_ON_UNEXPECTED_END = False
        self.worker._check_stream(status(True, 100), 0)
        self.requests.stop_stream.assert_called_once()
        self.worker._check_stream(status(False), 1)
        self.worker._check_stream(status(False), 3)
        self.requests.start_stream.assert_called_once()
        # A slow startup stays monitored past the old ten-second cutoff.
        self.worker._check_stream(status(False), 20)
        self.worker._check_stream(status(True, 1), 25)
        self.assertFalse(self.worker._stop_event.is_set())
        self.worker._check_stream(status(True, 100), 125)
        self.assertEqual(self.requests.stop_stream.call_count, 2)

    def test_unexpected_stop_during_poll_keeps_monitor_alive(self):
        self.worker._check_stream(status(True, 10), 0)
        self.worker.on_stream_state_changed(SimpleNamespace(
            output_state='OBS_WEBSOCKET_OUTPUT_STOPPED'))
        self.requests.start_stream.assert_not_called()
        self.worker._check_stream(status(False), 1)
        self.worker._check_stream(status(False), 2)
        self.requests.start_stream.assert_not_called()
        self.worker._check_stream(status(False), 3)
        self.worker._check_stream(status(True, 1), 5)
        self.assertFalse(self.worker._stop_event.is_set())
        self.requests.start_stream.assert_called_once()
        self.worker._check_stream(status(True, 100), 105)
        self.requests.stop_stream.assert_called_once()

    def test_failed_start_attempts_are_bounded_and_manual_recovery_works(self):
        self.worker._check_stream(status(True, 10), 0)
        for now in (1, 3, 33, 35, 65, 67, 97, 200):
            self.worker._check_stream(status(False), now)
        self.assertEqual(self.requests.start_stream.call_count, 3)
        self.assertFalse(self.worker._stop_event.is_set())
        self.worker._check_stream(status(True, 1), 201)
        self.worker._check_stream(status(False), 202)
        self.worker._check_stream(status(False), 204)
        self.assertEqual(self.requests.start_stream.call_count, 4)

    def test_failed_stop_attempts_are_bounded(self):
        for now in (0, 30, 60, 90, 200):
            self.worker._check_stream(status(True, 100 + now), now)
        self.assertEqual(self.requests.stop_stream.call_count, 3)
        self.requests.start_stream.assert_not_called()
        self.assertFalse(self.worker._stop_event.is_set())

    def run_with_clients(self, request_clients, event_clients):
        self.worker.cl = None
        self.worker.POLL_INTERVAL = 0
        with patch.object(obs_module.obs, 'ReqClient', create=True,
                          side_effect=request_clients) as req_factory, \
                patch.object(obs_module.obs, 'EventClient', create=True,
                             side_effect=event_clients) as event_factory:
            self.worker.run()
        return req_factory, event_factory

    def stop_on_status(self):
        self.worker.stop()
        return status(False)

    def test_initial_connection_failure_retries(self):
        self.requests.get_stream_status.side_effect = self.stop_on_status
        events = event_client()
        factory, _ = self.run_with_clients(
            [ConnectionRefusedError('not listening'), self.requests], [events])
        self.assertEqual(factory.call_count, 2)
        self.requests.disconnect.assert_called_once()
        events.disconnect.assert_called_once()

    def test_request_disconnect_reconnects_both_clients(self):
        broken = Mock()
        broken.get_stream_status.side_effect = ConnectionError('socket closed')
        events1, events2 = event_client(), event_client()
        self.requests.get_stream_status.side_effect = self.stop_on_status
        factory, _ = self.run_with_clients(
            [broken, self.requests], [events1, events2])
        self.assertEqual(factory.call_count, 2)
        for client in (broken, self.requests, events1, events2):
            client.disconnect.assert_called_once()

    def test_partial_connection_is_closed_before_retry(self):
        partial = Mock()
        events = event_client()
        self.requests.get_stream_status.side_effect = self.stop_on_status
        self.run_with_clients([partial, self.requests],
                              [ConnectionError('event socket closed'), events])
        partial.disconnect.assert_called_once()
        self.requests.disconnect.assert_called_once()
        events.disconnect.assert_called_once()

    def test_event_connection_failure_is_detected_and_retried(self):
        first = Mock()
        events1, events2 = event_client(), event_client()
        events1.worker.is_alive.return_value = False
        self.requests.get_stream_status.side_effect = self.stop_on_status
        self.run_with_clients([first, self.requests], [events1, events2])
        first.get_stream_status.assert_not_called()
        first.disconnect.assert_called_once()
        events1.disconnect.assert_called_once()

    def test_stop_interrupts_wait_and_disconnects_on_worker_thread(self):
        polled = Event()
        callers = []
        def poll():
            callers.append(get_ident())
            polled.set()
            return status(False)
        self.requests.get_stream_status.side_effect = poll
        self.worker.ecl = event_client()
        events = self.worker.ecl
        self.worker.POLL_INTERVAL = 60
        self.worker.start()
        try:
            self.assertTrue(polled.wait(1), 'worker did not poll')
            self.worker.stop()
            self.worker.join(1)
            self.assertFalse(self.worker.is_alive())
            self.assertTrue(all(caller == self.worker.ident for caller in callers))
            self.requests.disconnect.assert_called_once()
            events.disconnect.assert_called_once()
        finally:
            self.worker.stop()
            self.worker.join(1)

    def test_callbacks_never_send_requests_and_exit_signals_shutdown(self):
        self.worker.on_stream_state_changed(SimpleNamespace(
            output_state='OBS_WEBSOCKET_OUTPUT_STOPPED'))
        self.worker.on_exit_started(None)
        self.assertFalse(self.requests.mock_calls)
        self.assertTrue(self.worker._stop_event.is_set())


if __name__ == '__main__':
    unittest.main()
