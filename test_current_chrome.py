"""Cross-platform current-Chrome behavior without touching a real account."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import browser
import chrome_session
from browser import BossBrowser, BossBrowserUnavailable


class UrlAndVersionTests(unittest.TestCase):
    def test_root_domain_is_allowed(self):
        self.assertTrue(browser.is_boss_url("https://zhipin.com/a"))

    def test_subdomain_is_allowed(self):
        self.assertTrue(browser.is_boss_url("https://www.zhipin.com/web/chat/recommend"))

    def test_spoofed_domain_is_rejected(self):
        self.assertFalse(browser.is_boss_url("https://zhipin.com.example.com/"))

    def test_non_http_scheme_is_rejected(self):
        self.assertFalse(browser.is_boss_url("file://zhipin.com/private"))

    def test_chrome_major_is_parsed(self):
        self.assertEqual(browser.chrome_major("144.0.7559.3"), 144)

    def test_invalid_chrome_major_is_none(self):
        self.assertIsNone(browser.chrome_major("Chrome unknown"))


class EndpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.port_file = Path(self.tmp.name) / "DevToolsActivePort"

    def test_macos_missing_authorization(self):
        with patch.object(chrome_session.sys, "platform", "darwin"), patch.object(
            chrome_session, "CURRENT_PORT_FILE", self.port_file
        ):
            self.assertEqual(
                chrome_session.check_current_endpoint()["status"],
                "authorization_required",
            )

    def test_macos_valid_endpoint(self):
        self.port_file.write_text("9223\n/devtools/browser/id", encoding="utf-8")
        with patch.object(chrome_session.sys, "platform", "darwin"), patch.object(
            chrome_session, "CURRENT_PORT_FILE", self.port_file
        ):
            self.assertEqual(
                chrome_session.check_current_endpoint()["status"],
                "endpoint_available",
            )

    def test_windows_does_not_require_macos_port_file(self):
        with patch.object(chrome_session.sys, "platform", "win32"), patch.object(
            chrome_session, "CURRENT_PORT_FILE", self.port_file
        ):
            result = chrome_session.check_current_endpoint()
        self.assertEqual(result["status"], "endpoint_available")
        self.assertFalse(self.port_file.exists())

    def test_other_platform_is_explicitly_unsupported(self):
        with patch.object(chrome_session.sys, "platform", "linux"):
            self.assertEqual(
                chrome_session.check_current_endpoint()["status"],
                "unsupported_platform",
            )


class CurrentTabTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / "tab.json"
        self.patches = [
            patch.object(browser, "BROWSER_MODE", "current"),
            patch.object(browser, "TAB_STATE", self.state),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    @staticmethod
    def page(url):
        return Mock(
            url=url,
            is_closed=Mock(return_value=False),
            goto=AsyncMock(),
            close=AsyncMock(),
        )

    async def test_personal_tabs_are_not_reused(self):
        personal = self.page("https://www.zhipin.com/web/chat/recommend")
        work = self.page("https://www.zhipin.com/web/chat/recommend")
        instance = BossBrowser()
        instance._context = Mock(pages=[personal], new_page=AsyncMock(return_value=work))
        instance._target_id = AsyncMock(return_value="work-tab")
        self.assertIs(await instance._ensure_current_page(), work)
        personal.goto.assert_not_awaited()
        self.assertEqual(json.loads(self.state.read_text()), {"target_id": "work-tab"})

    async def test_existing_work_tab_is_reused(self):
        self.state.write_text('{"target_id":"work-tab"}', encoding="utf-8")
        work = self.page("https://www.zhipin.com/web/chat/recommend")
        instance = BossBrowser()
        instance._context = Mock(pages=[work], new_page=AsyncMock())
        instance._target_id = AsyncMock(return_value="work-tab")
        self.assertIs(await instance._ensure_current_page(), work)
        instance._context.new_page.assert_not_awaited()

    async def test_navigation_away_stops_operations(self):
        instance = BossBrowser()
        instance._page = self.page("https://example.com/")
        with self.assertRaises(BossBrowserUnavailable) as caught:
            await instance._ensure_current_page()
        self.assertEqual(caught.exception.code, "tab_scope_changed")

    async def test_saved_tab_on_other_site_is_not_replaced(self):
        self.state.write_text('{"target_id":"work-tab"}', encoding="utf-8")
        instance = BossBrowser()
        instance._context = Mock(
            pages=[self.page("https://example.com/")], new_page=AsyncMock()
        )
        session = Mock(
            send=AsyncMock(
                return_value={"targetInfos": [{"targetId": "work-tab"}]}
            ),
            detach=AsyncMock(),
        )
        instance._browser = Mock(
            new_browser_cdp_session=AsyncMock(return_value=session)
        )
        with self.assertRaises(BossBrowserUnavailable) as caught:
            await instance._ensure_current_page()
        self.assertEqual(caught.exception.code, "tab_scope_changed")
        instance._context.new_page.assert_not_awaited()

    async def test_failed_navigation_closes_only_new_tab(self):
        new_page = self.page("https://example.com/")
        instance = BossBrowser()
        instance._context = Mock(pages=[], new_page=AsyncMock(return_value=new_page))
        with self.assertRaises(BossBrowserUnavailable):
            await instance._ensure_current_page()
        new_page.close.assert_awaited_once()
        self.assertFalse(self.state.exists())

    async def test_repeated_login_does_not_navigate(self):
        current = self.page("https://www.zhipin.com/web/chat/recommend")
        current.evaluate = AsyncMock(return_value="logged_in")
        instance = BossBrowser()
        instance._page = current
        first = await instance.login()
        second = await instance.login()
        self.assertEqual(first["status"], "success")
        self.assertEqual(second["status"], "success")
        current.goto.assert_not_awaited()

    async def test_boss_route_alone_does_not_prove_login(self):
        current = self.page("https://www.zhipin.com/web/chat/recommend")
        current.evaluate = AsyncMock(return_value="unknown")
        instance = BossBrowser()
        instance._page = current
        self.assertEqual(await instance._current_auth_state(), "unknown")


class CurrentConnectionTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def playwright(browser_result=None, error=None):
        chromium = Mock()
        if error:
            chromium.connect_over_cdp = AsyncMock(side_effect=error)
        else:
            chromium.connect_over_cdp = AsyncMock(return_value=browser_result)
        runtime = Mock(chromium=chromium, stop=AsyncMock())
        starter = Mock(start=AsyncMock(return_value=runtime))
        return chromium, runtime, Mock(return_value=starter)

    async def test_connection_uses_native_channel_and_no_defaults(self):
        attached = Mock(contexts=[Mock()], version="144.0")
        chromium, _, factory = self.playwright(attached)
        instance = BossBrowser()
        with patch.object(browser, "BROWSER_MODE", "current"), patch.object(
            browser, "CONNECT_ENDPOINT", "chrome"
        ), patch.object(
            browser,
            "check_current_endpoint",
            return_value={"status": "endpoint_available"},
        ), patch.object(browser, "async_playwright", factory), patch.object(
            browser.platform, "system", return_value="Darwin"
        ), patch.object(instance, "_ensure_current_page", AsyncMock()):
            await instance._launch_current()
        chromium.connect_over_cdp.assert_awaited_once_with(
            "chrome", timeout=45000, no_defaults=True
        )

    async def test_connection_failure_never_launches_fallback(self):
        chromium, _, factory = self.playwright(error=RuntimeError("denied"))
        instance = BossBrowser()
        instance._launch_new_browser = AsyncMock()
        with patch.object(
            browser,
            "check_current_endpoint",
            return_value={"status": "endpoint_available"},
        ), patch.object(browser, "async_playwright", factory):
            with self.assertRaises(BossBrowserUnavailable) as caught:
                await instance._launch_current()
        self.assertEqual(caught.exception.code, "browser_connection_failed")
        chromium.connect_over_cdp.assert_awaited_once()
        instance._launch_new_browser.assert_not_awaited()

    async def test_windows_rejects_chrome_before_144(self):
        attached = Mock(contexts=[Mock()], version="143.0.0.0")
        _, _, factory = self.playwright(attached)
        instance = BossBrowser()
        with patch.object(
            browser,
            "check_current_endpoint",
            return_value={"status": "endpoint_available"},
        ), patch.object(browser, "async_playwright", factory), patch.object(
            browser.platform, "system", return_value="Windows"
        ):
            with self.assertRaises(BossBrowserUnavailable) as caught:
                await instance._launch_current()
        self.assertEqual(caught.exception.code, "unsupported_chrome_version")

    def test_connection_error_does_not_leak_raw_message(self):
        result = browser.connection_error(RuntimeError("secret-token"))
        self.assertEqual(result["error"], "browser_connection_failed")
        self.assertNotIn("secret-token", result["message"])


if __name__ == "__main__":
    unittest.main()
