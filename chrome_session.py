"""Current Chrome session discovery without launching or replacing Chrome."""

import os
import shutil
import subprocess
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


MAC_CHROME_BINARY = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def _valid_port(value) -> int | None:
    try:
        port = int(value)
    except (TypeError, ValueError):
        return None
    return port if 1 <= port <= 65535 else None


def _port_from_file() -> int | None:
    """Read Chrome's port file; macOS privacy rules may block this."""
    try:
        return _valid_port(CURRENT_PORT_FILE.read_text(encoding="utf-8").splitlines()[0])
    except (OSError, IndexError):
        return None


def _daily_chrome_pids() -> list[str]:
    """Main process of the everyday Chrome only.

    Automation browsers (boss-cli, chrome-devtools-mcp) always pass their own
    --user-data-dir, and helper processes carry --type=, so both are excluded.
    """
    try:
        output = subprocess.run(
            ["ps", "-axo", "pid=,command="],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    pids = []
    for line in output.splitlines():
        pid, _, command = line.strip().partition(" ")
        if (
            command.startswith(MAC_CHROME_BINARY)
            and "--type=" not in command
            and "--user-data-dir" not in command
            and "--remote-debugging-" not in command
        ):
            pids.append(pid)
    return pids


def _port_from_process() -> int | None:
    """Find the debugging port the everyday Chrome is listening on."""
    lsof = shutil.which("lsof") or "/usr/sbin/lsof"
    for pid in _daily_chrome_pids():
        try:
            output = subprocess.run(
                [lsof, "-nP", "-a", "-p", pid, "-iTCP", "-sTCP:LISTEN", "-Fn"],
                capture_output=True, text=True, timeout=5,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        for line in output.splitlines():
            if line.startswith("n") and line[1:].split(":")[0] in {"127.0.0.1", "localhost", "[::1]"}:
                port = _valid_port(line.rsplit(":", 1)[-1])
                if port:
                    return port
    return None


def check_current_endpoint() -> dict:
    """Check only non-invasive prerequisites for native current mode."""
    if sys.platform == "darwin":
        port = _port_from_file() or _port_from_process()
        if port is None:
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
            # Same endpoint Playwright derives from the port file; passing it
            # directly avoids a second read that macOS may block.
            "endpoint": f"ws://localhost:{port}/devtools/browser",
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
