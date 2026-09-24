"""Startup and shutdown regressions without actual OBS or browser processes."""

import builtins
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]


def load_main(enable_obs=True):
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
        "config": types.SimpleNamespace(ENABLE_OBS=enable_obs),
    }
    spec = importlib.util.spec_from_file_location("main_under_test", ROOT / "main.py")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, dependencies):
        spec.loader.exec_module(module)
    return module, thread, perform, close_browser, lifecycle, dependencies, thread_factory


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        (self.main, self.thread, self.perform, self.close_browser, self.lifecycle,
         dependencies, self.thread_factory) = load_main()
        imports = patch.dict(sys.modules, dependencies)
        imports.start()
        self.addCleanup(imports.stop)

    def test_import_does_not_start_obs_or_playback(self):
        self.thread_factory.assert_not_called()
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


class BrowserOnlyTests(unittest.TestCase):
    def setUp(self):
        (self.main, self.thread, self.perform, self.close_browser, self.lifecycle,
         self.dependencies, self.thread_factory) = load_main(enable_obs=False)
        imports = patch.dict(sys.modules, self.dependencies)
        imports.start()
        self.addCleanup(imports.stop)

    def assert_obs_untouched(self):
        self.thread_factory.assert_not_called()
        self.thread.start.assert_not_called()
        self.thread.stop.assert_not_called()
        self.thread.join.assert_not_called()

    def test_browser_runs_and_closes_without_obs_worker(self):
        self.main.run()
        self.assertEqual([item[0] for item in self.lifecycle.mock_calls],
                         ["perform", "close_browser"])
        self.assert_obs_untouched()

    def test_browser_error_still_closes_without_obs_worker(self):
        error = RuntimeError("browser disconnected")
        self.perform.side_effect = error
        with self.assertRaises(RuntimeError) as caught:
            self.main.run()
        self.assertIs(caught.exception, error)
        self.close_browser.assert_called_once()
        self.assert_obs_untouched()

    def test_disabled_obs_is_not_imported_at_startup_or_runtime(self):
        real_import = builtins.__import__

        def reject_obs(name, *args, **kwargs):
            if name == "obs" or name == "obsws_python" or name.startswith("obsws_python."):
                raise AssertionError("OBS must not be imported in browser-only mode")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=reject_obs):
            main, _, perform, close_browser, _, _, factory = load_main(enable_obs=False)
            main.run()
        perform.assert_called_once()
        close_browser.assert_called_once()
        factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
