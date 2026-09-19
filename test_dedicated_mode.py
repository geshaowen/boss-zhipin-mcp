import unittest
from unittest.mock import AsyncMock, Mock, patch

import browser
from browser import BossBrowser


class DedicatedModeRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_configured_cdp_is_attempted_first(self):
        instance = BossBrowser()
        instance._playwright = Mock()
        instance._try_cdp_connect = AsyncMock(return_value=True)
        with patch.object(browser, "async_playwright") as factory:
            factory.return_value.start = AsyncMock(return_value=instance._playwright)
            await instance._launch_dedicated()
        instance._try_cdp_connect.assert_awaited_once_with(browser.CDP_URL)

    async def test_common_ports_are_checked_before_launch(self):
        instance = BossBrowser()
        instance._try_cdp_connect = AsyncMock(side_effect=[False, True])
        instance._launch_system_chrome = AsyncMock()
        with patch.object(browser, "CDP_DETECT_PORTS", [9229]), patch.object(
            browser, "async_playwright"
        ) as factory:
            factory.return_value.start = AsyncMock(return_value=Mock())
            await instance._launch_dedicated()
        self.assertEqual(instance._try_cdp_connect.await_count, 2)
        instance._launch_system_chrome.assert_not_awaited()

    async def test_dedicated_fallback_is_retained(self):
        instance = BossBrowser()
        instance._try_cdp_connect = AsyncMock(return_value=False)
        instance._launch_system_chrome = AsyncMock(return_value=None)
        instance._launch_new_browser = AsyncMock()
        with patch.object(browser, "CDP_DETECT_PORTS", []), patch.object(
            browser, "async_playwright"
        ) as factory:
            factory.return_value.start = AsyncMock(return_value=Mock())
            await instance._launch_dedicated()
        instance._launch_new_browser.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
