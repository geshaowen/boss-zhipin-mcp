"""Read-only current-Chrome smoke test for macOS and Windows."""

import asyncio
import hashlib
import json
import os
from pathlib import Path

os.environ.setdefault("BOSS_BROWSER_MODE", "current")

from browser import BossBrowser, connection_error  # noqa: E402
from config import CANDIDATE_DB_FILE  # noqa: E402
from server import mcp  # noqa: E402


def file_sha256(path: str) -> str | None:
    target = Path(path)
    if not target.exists():
        return None
    digest = hashlib.sha256()
    with target.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


async def verify() -> tuple[bool, dict]:
    browser = BossBrowser()
    database_before = file_sha256(CANDIDATE_DB_FILE)
    try:
        await browser.launch(ensure_page=False)
        pages_before = len([p for p in browser._context.pages if not p.is_closed()])

        first_page = await browser._ensure_current_page()
        pages_after_first = len(
            [p for p in browser._context.pages if not p.is_closed()]
        )
        login_first = await browser.login()

        second_page = await browser._ensure_current_page()
        pages_after_repeat = len(
            [p for p in browser._context.pages if not p.is_closed()]
        )
        login_repeat = await browser.login()

        tools = await mcp.list_tools()
        database_after = file_sha256(CANDIDATE_DB_FILE)
        result = {
            "mode": "current",
            "work_url": second_page.url,
            "pages_before": pages_before,
            "pages_after_first": pages_after_first,
            "pages_after_repeat": pages_after_repeat,
            "work_tab_delta": pages_after_first - pages_before,
            "same_work_page": first_page is second_page,
            "login_first": login_first,
            "login_repeat": login_repeat,
            "tool_count": len(tools),
            "tool_names": [tool.name for tool in tools],
            "candidate_database_sha256_before": database_before,
            "candidate_database_sha256_after": database_after,
        }
        passed = all(
            [
                result["same_work_page"],
                pages_after_repeat == pages_after_first,
                login_first.get("status") == "success",
                login_repeat.get("status") == "success",
                len(tools) == 17,
                database_before == database_after,
            ]
        )
        result["status"] = "passed" if passed else "failed"
        return passed, result
    except Exception as exc:
        error = connection_error(exc)
        return False, {"status": "failed", **error}
    finally:
        await browser.close()


def main() -> int:
    passed, result = asyncio.run(verify())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
