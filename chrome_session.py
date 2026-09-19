"""Current Chrome session discovery without launching or replacing Chrome."""

import os
import sys
from pathlib import Path

BROWSER_MODE = os.getenv("BOSS_BROWSER_MODE", "dedicated").strip().lower()
if BROWSER_MODE not in {"current", "dedicated"}:
    raise ValueError("BOSS_BROWSER_MODE 必须是 current 或 dedicated")

# Playwright 1.60+ understands this native current-Chrome endpoint. Chrome
# displays a permission prompt for each new debugging connection.
CONNECT_ENDPOINT = "chrome" if BROWSER_MODE == "current" else None
CURRENT_PORT_FILE = (
    Path.home()
    / "Library"
    / "Application Support"
    / "Google"
    / "Chrome"
    / "DevToolsActivePort"
)


def check_current_endpoint() -> dict:
    """Check only non-invasive prerequisites for native current mode."""
    if sys.platform == "darwin":
        try:
            port = int(CURRENT_PORT_FILE.read_text(encoding="utf-8").splitlines()[0])
            if not 1 <= port <= 65535:
                raise ValueError("invalid port")
        except (OSError, ValueError, IndexError):
            return {
                "status": "authorization_required",
                "mode": "current",
                "message": (
                    "请在日常 Chrome 打开 chrome://inspect/#remote-debugging，开启 "
                    "Allow remote debugging for this browser instance，并允许随后出现的连接请求"
                ),
            }
        return {
            "status": "endpoint_available",
            "mode": "current",
            "endpoint_ready": True,
            "message": "已发现当前 Chrome 的原生调试入口；连接仍需浏览器授权",
        }

    if sys.platform == "win32":
        return {
            "status": "endpoint_available",
            "mode": "current",
            "endpoint_ready": True,
            "message": (
                "Windows 将通过 Chrome 原生授权连接；请先在 "
                "chrome://inspect/#remote-debugging 开启远程调试并在连接时点击允许"
            ),
        }

    return {
        "status": "unsupported_platform",
        "mode": "current",
        "message": "current 模式当前仅支持 macOS 和 Windows；可改用 dedicated 模式",
    }
