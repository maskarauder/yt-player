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
        "undetected_chromedriver": types.SimpleNamespace(
            Chrome=chrome, ChromeOptions=lambda: types.SimpleNamespace(add_argument=Mock())),
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

    def test_chrome_does_not_wait_for_all_secondary_resources(self):
        self.browser.create_browser()
        options = self.chrome.call_args.kwargs["options"]
        self.assertEqual(options.page_load_strategy, "eager")
        options.add_argument.assert_called_once_with('--autoplay-policy=no-user-gesture-required')
        self.chrome.return_value.set_page_load_timeout.assert_called_once_with(
            self.browser.PAGE_TIMEOUT)

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


class DiscoveryFlowTests(unittest.TestCase):
    """Exercise discovery and actual wait predicates against a changing page."""

    def setUp(self):
        self.browser, _ = load_browser()
        self.browser.driver = Mock()
        self.cards = []
        self.scroll_batches = []
        self.scroll_count = 0
        self.predicate_calls = 0
        self.browser.driver.find_elements.side_effect = self.find_elements
        self.browser.driver.find_element.side_effect = self.find_element
        self.browser.driver.execute_script.side_effect = self.scroll_page
        self.browser.EC.presence_of_element_located = (
            lambda locator: lambda driver: driver.find_element(*locator))
        self.browser.WebDriverWait = self.wait

    def add_cards(self, hrefs):
        self.cards.extend(
            Mock(get_attribute=Mock(return_value=href)) for href in hrefs)

    def find_elements(self, by, selector):
        if selector == self.browser.PLAYLIST_LINKS:
            return list(self.cards)
        if "continuation" in selector:
            return []  # This page exposes no continuation marker.
        raise AssertionError(f"Unexpected lookup: {by}, {selector}")

    def find_element(self, by, selector):
        if (by, selector) == (self.browser.By.TAG_NAME, "body"):
            return object()
        elements = self.find_elements(by, selector)
        return elements[0] if elements else False

    def scroll_page(self, script):
        self.assertIn("scroll", script.lower())
        self.scroll_count += 1
        if self.scroll_count > 10:
            raise AssertionError("Pagination did not stop after content stopped growing")
        if self.scroll_batches:
            self.add_cards(self.scroll_batches.pop(0))

    def wait(self, driver, timeout):
        test = self

        class PredicateWait:
            def until(self, condition):
                for _ in range(5):
                    test.predicate_calls += 1
                    value = condition(driver)
                    if value:
                        return value
                raise test.browser.TimeoutException("No content change during wait")

        return PredicateWait()

    def test_navigation_timeout_keeps_usable_loaded_content(self):
        self.add_cards(["/playlist?list=PL1"])
        self.browser.driver.get.side_effect = self.browser.TimeoutException(
            "A secondary resource did not finish loading")
        playlists = self.browser.get_available_playlists()
        self.assertEqual(playlists, ["https://www.youtube.com/playlist?list=PL1"])
        self.browser.driver.get.assert_called_once_with(self.browser.PLAYLISTS_LOCATION)
        self.assertEqual(self.scroll_count, 1)
        self.assertGreater(self.predicate_calls, 0)

    def test_scrolling_reveals_first_cards_without_a_continuation_marker(self):
        self.scroll_batches = [["/playlist?list=PL1"], ["/playlist?list=PL2"]]
        playlists = self.browser.get_available_playlists()
        self.assertEqual(playlists, [
            "https://www.youtube.com/playlist?list=PL1",
            "https://www.youtube.com/playlist?list=PL2",
        ])
        self.assertEqual(self.scroll_count, 3)
        self.assertGreater(self.predicate_calls, self.scroll_count)

    def test_pagination_stops_when_existing_cards_do_not_grow(self):
        self.add_cards(["/playlist?list=PL1"])
        self.browser.scroll_to_load_content()
        self.assertEqual(self.scroll_count, 1)
        self.assertGreater(self.predicate_calls, 1)


class FullscreenTests(unittest.TestCase):
    def setUp(self):
        self.browser, _ = load_browser()
        self.browser.driver = Mock()
        self.fullscreen = False
        self.player = Mock(is_displayed=Mock(return_value=True))
        self.button = Mock(is_displayed=Mock(return_value=False))
        self.player.send_keys.side_effect = self.press_key
        self.browser.driver.execute_script.side_effect = lambda _: self.fullscreen
        self.browser.driver.find_element.side_effect = self.find_element
        self.browser.EC.visibility_of_element_located = self.visible_element
        self.browser.EC.element_to_be_clickable = self.visible_element
        self.browser.WebDriverWait = self.wait

    def press_key(self, key):
        self.assertEqual(key, "f")
        self.fullscreen = not self.fullscreen

    def find_element(self, by, selector):
        if (by, selector) == (self.browser.By.ID, "movie_player"):
            return self.player
        if (by, selector) == (self.browser.By.CLASS_NAME, "ytp-fullscreen-button"):
            return self.button  # Auto-hidden controls must not block fullscreen.
        raise AssertionError(f"Unexpected lookup: {by}, {selector}")

    def visible_element(self, locator):
        def predicate(driver):
            element = driver.find_element(*locator)
            return element if element.is_displayed() else False
        return predicate

    def wait(self, driver, timeout):
        exception_type = self.browser.TimeoutException

        class Wait:
            def until(self, condition):
                for _ in range(5):
                    result = condition(driver)
                    if result:
                        return result
                raise exception_type("condition remained false")

        return Wait()

    def test_hidden_controls_do_not_block_fullscreen(self):
        self.browser.fullscreen_video()
        self.assertTrue(self.fullscreen)
        self.player.send_keys.assert_called_once_with("f")

    def test_already_fullscreen_is_not_toggled_off(self):
        self.fullscreen = True
        self.browser.fullscreen_video()
        self.assertTrue(self.fullscreen)
        self.player.send_keys.assert_not_called()

    def test_waits_for_fullscreen_to_take_effect(self):
        states = iter([False, False, False, True])
        self.browser.driver.execute_script.side_effect = lambda _: next(states)
        self.browser.fullscreen_video()
        self.assertEqual(self.browser.driver.execute_script.call_count, 4)
        self.player.send_keys.assert_called_once_with("f")

    def test_shortcut_without_fullscreen_confirmation_times_out(self):
        self.player.send_keys.side_effect = None
        with self.assertRaisesRegex(self.browser.TimeoutException, "after sending"):
            self.browser.fullscreen_video()

    def test_invisible_player_times_out_without_sending_keys(self):
        self.player.is_displayed.return_value = False
        with self.assertRaisesRegex(self.browser.TimeoutException, "player to become visible"):
            self.browser.fullscreen_video()
        self.player.send_keys.assert_not_called()


class PlaybackTests(unittest.TestCase):
    def setUp(self):
        self.browser, _ = load_browser()
        self.browser.driver = Mock()
        self.browser.fullscreen_video = Mock()
        self.browser.hide_live_chat = Mock()
        self.browser.recover_playback = Mock()
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
        self.assertEqual(self.browser.hide_live_chat.call_count, 3)
        self.assertEqual(self.browser.recover_playback.call_count, 2)

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
