"""Playwright browser management for dedicated and current Chrome sessions."""

import asyncio
import json
import logging
import os
import platform
import random
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from chrome_session import BROWSER_MODE, CONNECT_ENDPOINT, check_current_endpoint
from config import (
    BOSS_BASE_URL,
    BOSS_SEARCH_URL,
    COOKIES_DIR,
    COOKIES_FILE,
    MAX_DELAY,
    MIN_DELAY,
)
from tab_lock import TabLockUnavailable, tab_state_lock

log = logging.getLogger("boss-browser")

# Dedicated-mode settings retained from the upstream implementation.
CDP_URL = os.getenv("BOSS_CDP_URL", "http://localhost:9222")
CDP_DETECT_PORTS = [9222, 9229, 19222]

# Current-mode state records only the tab target ID. It never stores cookies.
TAB_STATE = Path(__file__).with_name(".boss-current-tab.json")
MIN_CURRENT_CHROME_MAJOR = 144


class BossBrowserUnavailable(RuntimeError):
    """A safe, user-actionable browser connection failure."""

    def __init__(self, message: str, code: str = "browser_not_ready"):
        super().__init__(message)
        self.code = code


def connection_error(exc: Exception) -> dict:
    """Convert connection exceptions to a stable, non-sensitive result."""
    if isinstance(exc, BossBrowserUnavailable):
        return {"error": exc.code, "message": str(exc)}
    return {
        "error": "browser_connection_failed",
        "message": (
            f"Chrome 连接失败（{type(exc).__name__}）；current 模式请确认已开启远程调试并允许连接，"
            "dedicated 模式请检查调试端口"
        ),
    }


def is_boss_url(url: str) -> bool:
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    return parsed.scheme in {"http", "https"} and (
        host == "zhipin.com" or host.endswith(".zhipin.com")
    )


def chrome_major(version: str) -> int | None:
    """Return the leading Chrome major version, if available."""
    try:
        return int(version.split(".", 1)[0])
    except (AttributeError, TypeError, ValueError):
        return None


class BossBrowser:
    """Connect to BOSS through either an isolated or the current Chrome."""

    def __init__(self):
        self._playwright = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._login_lock = asyncio.Lock()

    async def launch(self, ensure_page: bool = True):
        """Connect using the selected mode.

        Current mode is intentionally fail-closed: it never launches another
        browser or falls back to a fresh Chromium profile.
        """
        if BROWSER_MODE == "current":
            await self._launch_current(ensure_page=ensure_page)
            return
        await self._launch_dedicated()

    async def _launch_current(self, ensure_page: bool = True):
        status = await asyncio.to_thread(check_current_endpoint)
        if status["status"] != "endpoint_available":
            raise BossBrowserUnavailable(status["message"], status["status"])

        try:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.connect_over_cdp(
                CONNECT_ENDPOINT,
                timeout=45000,
                no_defaults=True,
            )
            if platform.system() == "Windows":
                major = chrome_major(self._browser.version)
                if major is None or major < MIN_CURRENT_CHROME_MAJOR:
                    raise BossBrowserUnavailable(
                        f"Windows current 模式需要 Chrome {MIN_CURRENT_CHROME_MAJOR}+；"
                        f"当前检测到 {self._browser.version or '未知版本'}",
                        "unsupported_chrome_version",
                    )
            if not self._browser.contexts:
                raise BossBrowserUnavailable("Chrome 没有可用浏览器上下文")
            self._context = self._browser.contexts[0]
            if ensure_page:
                await self._ensure_current_page()
        except Exception as exc:
            await self.close()
            if isinstance(exc, BossBrowserUnavailable):
                raise
            error = connection_error(exc)
            raise BossBrowserUnavailable(error["message"], error["error"]) from exc

    async def _launch_dedicated(self):
        """Retain the upstream dedicated-mode connection and fallback flow."""
        self._playwright = await async_playwright().start()

        if await self._try_cdp_connect(CDP_URL):
            log.info("Connected via CDP: %s", CDP_URL)
            return

        for port in CDP_DETECT_PORTS:
            url = f"http://localhost:{port}"
            if url == CDP_URL:
                continue
            if await self._try_cdp_connect(url):
                log.info("Auto-detected Chrome at port %s", port)
                return

        log.info("No running Chrome found, launching system Chrome with debug port")
        launched_port = await self._launch_system_chrome()
        if launched_port and await self._try_cdp_connect(
            f"http://localhost:{launched_port}"
        ):
            return

        log.info("System Chrome launch failed, falling back to bare Chromium")
        await self._launch_new_browser()

    async def _launch_system_chrome(self) -> int | None:
        """Launch an isolated Chrome profile for dedicated mode."""
        import subprocess

        port = 9222
        system = platform.system()
        if system == "Darwin":
            chrome_path = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        elif system == "Linux":
            chrome_path = "google-chrome"
        else:
            chrome_path = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

        profile_dir = os.path.join(os.path.dirname(__file__), "chrome-profile")
        try:
            subprocess.Popen(
                [
                    chrome_path,
                    f"--remote-debugging-port={port}",
                    f"--user-data-dir={profile_dir}",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            for _ in range(15):
                await asyncio.sleep(1)
                try:
                    import urllib.request

                    urllib.request.urlopen(
                        f"http://localhost:{port}/json/version", timeout=2
                    )
                    return port
                except Exception:
                    continue
        except FileNotFoundError:
            log.warning("Chrome not found at %s", chrome_path)
        except Exception as exc:
            log.warning("Failed to launch system Chrome: %s", exc)
        return None

    async def _try_cdp_connect(self, url: str) -> bool:
        try:
            self._browser = await self._playwright.chromium.connect_over_cdp(
                url, timeout=5000
            )
            contexts = self._browser.contexts
            if contexts:
                self._context = contexts[0]
                pages = self._context.pages
                self._page = pages[0] if pages else await self._context.new_page()
            else:
                self._context = await self._browser.new_context(
                    viewport={"width": 1440, "height": 900}, locale="zh-CN"
                )
                self._page = await self._context.new_page()
            return True
        except Exception:
            return False

    async def _launch_new_browser(self):
        self._browser = await self._playwright.chromium.launch(
            headless=False, args=["--disable-blink-features=AutomationControlled"]
        )
        self._context = await self._browser.new_context(
            viewport={"width": 1440, "height": 900}, locale="zh-CN"
        )
        await self._load_cookies()
        self._page = await self._context.new_page()

    async def _target_id(self, page: Page) -> str:
        session = await self._context.new_cdp_session(page)
        try:
            result = await session.send("Target.getTargetInfo")
            return result["targetInfo"]["targetId"]
        finally:
            await session.detach()

    async def _ensure_current_page(self):
        """Reuse only the MCP-owned BOSS tab; never take over personal tabs."""
        if not is_boss_url(BOSS_SEARCH_URL):
            raise BossBrowserUnavailable(
                "BOSS_SEARCH_URL 必须指向 BOSS 网站", "invalid_configuration"
            )
        if self._page and not self._page.is_closed():
            if not is_boss_url(self._page.url):
                raise BossBrowserUnavailable(
                    "MCP 工作标签页已离开 BOSS；请关闭该标签页后重试",
                    "tab_scope_changed",
                )
            return self._page

        try:
            with tab_state_lock(TAB_STATE.with_suffix(".lock")):
                saved = self._read_tab_state()
                target_id = saved.get("target_id")
                if target_id:
                    for page in self._context.pages:
                        if (
                            not page.is_closed()
                            and is_boss_url(page.url)
                            and await self._target_id(page) == target_id
                        ):
                            self._page = page
                            return page

                    session = await self._browser.new_browser_cdp_session()
                    try:
                        targets = (await session.send("Target.getTargets"))[
                            "targetInfos"
                        ]
                        if any(t["targetId"] == target_id for t in targets):
                            raise BossBrowserUnavailable(
                                "MCP 工作标签页已离开 BOSS；请关闭该标签页后重试",
                                "tab_scope_changed",
                            )
                    finally:
                        await session.detach()

                page = await self._context.new_page()
                try:
                    await page.goto(BOSS_SEARCH_URL, wait_until="domcontentloaded")
                    if not is_boss_url(page.url):
                        raise BossBrowserUnavailable(
                            "新工作标签页未进入 BOSS，已停止操作",
                            "tab_scope_changed",
                        )
                    self._write_tab_state({"target_id": await self._target_id(page)})
                except Exception:
                    await page.close()
                    raise
                self._page = page
                return page
        except TabLockUnavailable as exc:
            raise BossBrowserUnavailable(
                "另一服务正在绑定 BOSS 工作标签页，请稍后重试", "tab_in_use"
            ) from exc

    @staticmethod
    def _read_tab_state() -> dict:
        try:
            data = json.loads(TAB_STATE.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    @staticmethod
    def _write_tab_state(state: dict):
        tmp = TAB_STATE.with_suffix(".tmp")
        tmp.write_text(json.dumps(state), encoding="utf-8")
        tmp.replace(TAB_STATE)

    async def close(self):
        """Disconnect without closing the user's Chrome."""
        if BROWSER_MODE == "dedicated" and self._context:
            await self._save_cookies()
        if self._playwright:
            await self._playwright.stop()
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

    @property
    def page(self) -> Page:
        if not self._page or self._page.is_closed():
            raise BossBrowserUnavailable("BOSS 标签页已关闭，请重新调用工具以连接")
        if BROWSER_MODE == "current" and not is_boss_url(self._page.url):
            raise BossBrowserUnavailable(
                "工作标签页已离开 BOSS，已停止操作", "tab_scope_changed"
            )
        return self._page

    @property
    def is_alive(self) -> bool:
        try:
            return self._browser is not None and self._browser.is_connected()
        except Exception:
            return False

    async def _load_cookies(self):
        if os.path.exists(COOKIES_FILE):
            with open(COOKIES_FILE, "r", encoding="utf-8") as handle:
                await self._context.add_cookies(json.load(handle))

    async def _save_cookies(self):
        os.makedirs(COOKIES_DIR, exist_ok=True)
        try:
            cookies = await self._context.cookies()
            with open(COOKIES_FILE, "w", encoding="utf-8") as handle:
                json.dump(cookies, handle, indent=2)
        except Exception:
            pass

    async def _current_auth_state(self) -> str:
        try:
            state = await self.page.evaluate("""() => {
                const visible = e => e && e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden';
                const any = s => Array.from(document.querySelectorAll(s)).some(visible);
                const text = document.body?.innerText || '';
                if (any('.verify-wrap, .captcha, .slider-verify') || /安全验证|滑动验证|请完成验证/.test(text)) return 'verification_required';
                if (any('.login-register-content, .login-box, .sign-form') || /扫码登录|短信登录/.test(text)) return 'login_required';
                const recruiterNav = any('a[href*="/web/chat/job/list"]') &&
                    any('a[href*="/web/chat/index"], iframe[src*="/web/frame/recommend"]');
                if (recruiterNav || (any('.user-name') && any('dl.menu-geeksearch, dl.menu-recommend'))) return 'logged_in';
                return 'unknown';
            }""")
            if "/web/user" in urlsplit(self.page.url).path:
                return (
                    "verification_required"
                    if state == "verification_required"
                    else "login_required"
                )
            return state
        except Exception:
            return "unknown"

    async def is_logged_in(self) -> bool:
        if BROWSER_MODE == "current":
            return await self._current_auth_state() == "logged_in"

        try:
            if "zhipin.com" in self.page.url:
                return await self._check_current_page_logged_in()
        except Exception:
            pass
        await self.page.goto(BOSS_BASE_URL, wait_until="networkidle")
        await asyncio.sleep(3)
        return await self._check_current_page_logged_in()

    async def _check_current_page_logged_in(self) -> bool:
        if BROWSER_MODE == "current":
            return await self._current_auth_state() == "logged_in"
        current_url = self.page.url
        if (
            "login" in current_url
            or "/web/user" in current_url
            or "bticket" in current_url
        ):
            body_class = await self.page.evaluate("document.body.className || ''")
            if "login" in body_class:
                return False
        if "/web/boss/" in current_url or "/web/chat/" in current_url:
            return True
        try:
            logged_in = await self.page.query_selector(
                ".user-nav, .btn-post-job, .nav-figure, .menu-list"
            )
            return logged_in is not None
        except Exception:
            return False

    async def check_and_screenshot_verification(self) -> dict | None:
        try:
            has_verify = await self.page.evaluate("""() => {
                const text = document.body.innerText || '';
                const selectors = ['.verify-wrap', '.captcha', '.slider-verify',
                    '[class*="verify"]', '[class*="captcha"]', '.boss-popup__wrapper'];
                for (const sel of selectors) {
                    const el = document.querySelector(sel);
                    if (el && el.offsetWidth > 0) return true;
                }
                return text.includes('安全验证') || text.includes('滑动验证') || text.includes('请完成验证');
            }""")
            if has_verify:
                screenshot_path = os.path.join(
                    os.path.dirname(__file__), "screenshot_verification.png"
                )
                await self.page.screenshot(path=screenshot_path)
                return {
                    "needs_verification": True,
                    "screenshot": screenshot_path,
                    "message": "检测到安全验证，请在浏览器中手动完成验证后告知",
                }
        except Exception:
            pass
        return None

    async def login(self) -> dict:
        if BROWSER_MODE == "current":
            async with self._login_lock:
                state = await self._current_auth_state()
                if state == "logged_in":
                    return {
                        "status": "success",
                        "mode": "current",
                        "message": "已确认招聘者账号登录，复用所连接 Chrome 的登录态",
                    }
                if state == "verification_required":
                    return {
                        "status": state,
                        "message": "请在现有窗口完成安全验证",
                    }
                if state == "unknown":
                    return {
                        "status": "page_not_ready",
                        "message": "页面尚未完成加载或状态无法识别，请稍后重试",
                    }
                return {
                    "status": "login_required",
                    "message": "请在现有 BOSS 页面扫码或短信登录，完成后再次调用 boss_login",
                }

        await self.page.goto(
            f"{BOSS_BASE_URL}/web/user/?ka=header-login",
            wait_until="domcontentloaded",
        )
        for _ in range(90):
            await asyncio.sleep(2)
            if await self._check_current_page_logged_in():
                await self._save_cookies()
                return {"status": "success", "message": "登录成功，Cookie 已保存"}
        return {
            "status": "timeout",
            "message": "登录超时（3分钟），请在浏览器中完成登录后重试",
        }

    async def random_delay(self):
        await asyncio.sleep(random.uniform(MIN_DELAY, MAX_DELAY))
