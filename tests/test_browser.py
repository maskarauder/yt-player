"""Browser behavior tests using only the standard library and local doubles."""

import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, call, patch
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parents[1]


def load_browser():
    """Import without launching Chrome or requiring its optional dependencies."""
    chrome = Mock(name="Chrome")
    exceptions = types.ModuleType("selenium.common.exceptions")
    for name in (
        "NoSuchElementException", "StaleElementReferenceException",
        "TimeoutException", "WebDriverException",
    ):
        setattr(exceptions, name, type(name, (Exception,), {}))
    by = types.SimpleNamespace(
        TAG_NAME="tag name", CLASS_NAME="class name", CSS_SELECTOR="css selector",
        ID="id", XPATH="xpath",
    )
    dependencies = {
        "undetected_chromedriver": types.SimpleNamespace(Chrome=chrome),
        "bs4": types.SimpleNamespace(BeautifulSoup=Mock(name="BeautifulSoup")),
        "selenium": types.ModuleType("selenium"),
        "selenium.webdriver": types.SimpleNamespace(ActionChains=Mock()),
        "selenium.webdriver.common": types.ModuleType("selenium.webdriver.common"),
        "selenium.webdriver.common.by": types.SimpleNamespace(By=by),
        "selenium.common": types.ModuleType("selenium.common"),
        "selenium.common.exceptions": exceptions,
        "selenium.webdriver.support": types.ModuleType("selenium.webdriver.support"),
        "selenium.webdriver.support.ui": types.SimpleNamespace(WebDriverWait=Mock()),
        "selenium.webdriver.support.expected_conditions": Mock(),
    }
    config_spec = importlib.util.spec_from_file_location("config", ROOT / "config.py")
    config = importlib.util.module_from_spec(config_spec)
    config_spec.loader.exec_module(config)
    dependencies["config"] = config
    spec = importlib.util.spec_from_file_location("browser_under_test", ROOT / "browser.py")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, dependencies):
        spec.loader.exec_module(module)
    return module, chrome


class PlaylistSelectionTests(unittest.TestCase):
    def setUp(self):
        self.browser, self.chrome = load_browser()

    def test_import_does_not_launch_chrome(self):
        self.chrome.assert_not_called()
        self.assertIsNone(self.browser.driver)

    def test_empty_playlist_discovery_has_no_selection(self):
        self.assertIsNone(self.browser.choose_playlist([], None))
        self.assertIsNone(self.browser.choose_playlist([], "previous"))

    def test_sole_playlist_can_be_selected_initially(self):
        self.assertEqual(self.browser.choose_playlist(["only"], None), "only")

    def test_sole_previously_played_playlist_is_not_repeated(self):
        self.assertIsNone(self.browser.choose_playlist(["only"], "only"))

    def test_duplicate_entries_cannot_force_a_repeat(self):
        self.assertIsNone(self.browser.choose_playlist(["only", "only"], "only"))

    def test_only_alternative_is_selected(self):
        self.assertEqual(self.browser.choose_playlist(["previous", "next"], "previous"), "next")

    def test_same_playlist_with_different_video_url_is_not_repeated(self):
        previous = "https://www.youtube.com/watch?v=first&list=PL1"
        alternate = "https://www.youtube.com/watch?v=second&list=PL1"
        self.assertIsNone(self.browser.choose_playlist([alternate], previous))

    def test_blacklist_id_excludes_all_video_urls_for_that_playlist(self):
        self.browser.PLAYLIST_BLACKLIST = [" PL1 "]
        links = [
            "https://www.youtube.com/watch?v=first&list=PL1",
            "https://www.youtube.com/watch?v=second&list=PL1&index=2",
            "https://www.youtube.com/playlist?list=PL2",
        ]
        self.assertEqual(self.browser.choose_playlist(links, None), links[2])

    def test_blacklist_url_matches_playlist_id_despite_other_parameters(self):
        self.browser.PLAYLIST_BLACKLIST = [
            "https://www.youtube.com/watch?v=other&list=PL1&index=7",
            "https://www.youtube.com/playlist?list=PL2",
        ]
        links = [
            "https://www.youtube.com/playlist?list=PL1",
            "https://www.youtube.com/watch?v=first&list=PL2",
        ]
        self.assertIsNone(self.browser.choose_playlist(links, None))

    def test_blacklist_does_not_force_repeating_the_previous_playlist(self):
        self.browser.PLAYLIST_BLACKLIST = ["PL2"]
        previous = "https://www.youtube.com/playlist?list=PL1"
        blocked = "https://www.youtube.com/playlist?list=PL2"
        self.assertIsNone(self.browser.choose_playlist([previous, blocked], previous))

    def test_discovery_deduplicates_playlist_ids_and_normalizes_links(self):
        hrefs = [
            "/watch?v=first&list=PL1&index=7",
            "https://www.youtube.com/watch?v=second&list=PL1",
            "/playlist?list=PL2",
            None,
            "/watch?v=not-a-playlist",
        ]
        self.browser.driver = Mock()
        self.browser.driver.find_elements.return_value = [
            Mock(get_attribute=Mock(return_value=href)) for href in hrefs
        ]
        with patch.object(self.browser, "scroll_to_load_content"):
            links = self.browser.get_available_playlists()
        self.assertEqual(len(links), 2)
        parsed = [urlparse(link) for link in links]
        self.assertEqual([link.netloc for link in parsed], ["www.youtube.com"] * 2)
        self.assertEqual([link.path for link in parsed], ["/watch", "/playlist"])
        self.assertEqual(
            [parse_qs(link.query) for link in parsed],
            [{"list": ["PL1"], "v": ["first"]}, {"list": ["PL2"]}],
        )

    def test_selection_always_comes_from_available_alternatives(self):
        for _ in range(20):
            selected = self.browser.choose_playlist(["previous", "next", "another"], "previous")
            self.assertIn(selected, ["next", "another"])


class PlaybackTests(unittest.TestCase):
    def setUp(self):
        self.browser, _ = load_browser()
        self.browser.driver = Mock()
        self.browser.fullscreen_video = Mock()
        self.browser.sleep = Mock()

    def immediate_wait(self, driver, timeout):
        exception_type = self.browser.TimeoutException

        class Wait:
            def until(self, condition):
                for _ in range(5):
                    result = condition(driver)
                    if result:
                        return result
                raise exception_type("player remained unavailable")

        return Wait()

    def test_waits_through_initial_and_temporary_missing_player(self):
        self.browser.driver.execute_script.side_effect = [
            None, {"ready": False, "ended": False},
            {"ready": True, "ended": False},
            None, {"ready": True, "ended": False},
            {"ready": True, "ended": True},
        ]
        with patch.object(self.browser, "WebDriverWait", self.immediate_wait):
            self.browser.watch_video("https://www.youtube.com/watch?v=first&list=PL1")
        self.assertEqual(self.browser.sleep.call_args_list, [call(1), call(1)])
        self.browser.fullscreen_video.assert_called_once()

    def test_missing_player_times_out(self):
        self.browser.driver.execute_script.return_value = None
        with patch.object(self.browser, "WebDriverWait", self.immediate_wait):
            with self.assertRaises(self.browser.TimeoutException):
                self.browser.watch_video("https://www.youtube.com/watch?v=first&list=PL1")

    def test_playlist_page_opens_first_watch_link(self):
        watch_url = "https://www.youtube.com/watch?v=first&list=PL1"
        link = Mock(get_attribute=Mock(return_value=watch_url))
        wait = Mock()
        wait.until.side_effect = [link, {"ready": True, "ended": True}]
        self.browser.WebDriverWait = Mock(return_value=wait)
        self.browser.watch_video("https://www.youtube.com/playlist?list=PL1")
        self.assertEqual(self.browser.driver.get.call_args_list, [
            call("https://www.youtube.com/playlist?list=PL1"), call(watch_url),
        ])


class LoopStopped(RuntimeError):
    pass


class PlaybackLoopTests(unittest.TestCase):
    def setUp(self):
        self.browser, _ = load_browser()
        self.browser.create_browser = Mock()
        self.browser.PAUSE_FOR_BROWSER_LOGIN = False
        self.browser.sleep = Mock()

    def test_no_alternative_waits_until_another_playlist_is_available(self):
        self.browser.get_available_playlists = Mock(side_effect=[
            ["first"], ["first"], ["first", "second"],
        ])
        self.browser.watch_video = Mock(side_effect=[None, LoopStopped()])
        with self.assertRaises(LoopStopped):
            self.browser.perform()
        self.assertEqual(self.browser.watch_video.call_args_list, [call("first"), call("second")])
        self.browser.sleep.assert_called_once_with(self.browser.RETRY_DELAY)

    def test_empty_discovery_waits_then_recovers(self):
        self.browser.get_available_playlists = Mock(side_effect=[[], ["first"]])
        self.browser.watch_video = Mock(side_effect=LoopStopped())
        with self.assertRaises(LoopStopped):
            self.browser.perform()
        self.browser.sleep.assert_called_once_with(self.browser.RETRY_DELAY)
        self.browser.watch_video.assert_called_once_with("first")

    def test_loading_timeout_retries_unfinished_playlist(self):
        self.browser.get_available_playlists = Mock(return_value=["first"])
        self.browser.watch_video = Mock(side_effect=[
            self.browser.TimeoutException("slow page"), LoopStopped(),
        ])
        with self.assertRaises(LoopStopped):
            self.browser.perform()
        self.assertEqual(self.browser.watch_video.call_args_list, [call("first"), call("first")])
        self.browser.sleep.assert_called_once_with(self.browser.RETRY_DELAY)

    def test_fatal_browser_error_propagates_for_main_to_clean_up(self):
        self.browser.get_available_playlists = Mock(return_value=["first"])
        error = RuntimeError("browser disconnected")
        self.browser.watch_video = Mock(side_effect=error)
        with self.assertRaises(RuntimeError) as caught:
            self.browser.perform()
        self.assertIs(caught.exception, error)
        self.browser.sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
