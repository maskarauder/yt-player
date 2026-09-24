"""Startup and shutdown regressions without actual OBS or browser processes."""

import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]


def load_main():
    lifecycle = Mock()
    thread = Mock(name="obs_thread")
    lifecycle.attach_mock(thread.start, "start")
    lifecycle.attach_mock(thread.stop, "stop")
    lifecycle.attach_mock(thread.join, "join")
    perform = Mock(name="perform")
    close_browser = Mock(name="close_browser")
    lifecycle.attach_mock(perform, "perform")
    lifecycle.attach_mock(close_browser, "close_browser")
    thread_factory = Mock(return_value=thread)
    dependencies = {
        "obs": types.SimpleNamespace(OBSThread=thread_factory),
        "browser": types.SimpleNamespace(perform=perform, close_browser=close_browser),
    }
    spec = importlib.util.spec_from_file_location("main_under_test", ROOT / "main.py")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, dependencies):
        spec.loader.exec_module(module)
    return module, thread, perform, close_browser, lifecycle


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.main, self.thread, self.perform, self.close_browser, self.lifecycle = load_main()

    def test_import_does_not_start_obs_or_playback(self):
        self.thread.start.assert_not_called()
        self.perform.assert_not_called()

    def test_normal_return_stops_worker_and_closes_browser(self):
        self.main.run()
        names = [item[0] for item in self.lifecycle.mock_calls]
        self.assertEqual(names, ["start", "perform", "stop", "join", "close_browser"])

    def test_browser_error_stops_worker_and_closes_browser(self):
        error = RuntimeError("browser parsing failed")
        self.perform.side_effect = error
        with self.assertRaises(RuntimeError) as caught:
            self.main.run()
        self.assertIs(caught.exception, error)
        self.thread.stop.assert_called_once()
        self.thread.join.assert_called_once()
        self.close_browser.assert_called_once()

    def test_keyboard_interrupt_also_cleans_up(self):
        self.perform.side_effect = KeyboardInterrupt()
        try:
            self.main.run()
        except KeyboardInterrupt:
            pass
        self.thread.stop.assert_called_once()
        self.thread.join.assert_called_once()
        self.close_browser.assert_called_once()

    def test_worker_start_failure_closes_browser(self):
        self.thread.start.side_effect = RuntimeError("worker cannot start")
        with self.assertRaisesRegex(RuntimeError, "worker cannot start"):
            self.main.run()
        self.perform.assert_not_called()
        self.close_browser.assert_called_once()

    def test_worker_stop_failure_still_closes_browser(self):
        self.thread.stop.side_effect = RuntimeError("worker cannot stop")
        try:
            self.main.run()
        except RuntimeError:
            pass
        self.close_browser.assert_called_once()

    def test_worker_join_failure_still_closes_browser(self):
        self.thread.join.side_effect = RuntimeError("worker cannot join")
        try:
            self.main.run()
        except RuntimeError:
            pass
        self.close_browser.assert_called_once()


if __name__ == "__main__":
    unittest.main()
