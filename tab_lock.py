"""Small cross-platform non-blocking file lock for current-tab state."""

import os
from contextlib import contextmanager
from pathlib import Path

if os.name == "nt":  # pragma: no cover - exercised by Windows CI
    import msvcrt
else:  # pragma: no cover - backend-specific branches run on their native CI OS
    import fcntl


class TabLockUnavailable(RuntimeError):
    """Another process is updating the current-tab state."""


@contextmanager
def tab_state_lock(path: Path):
    """Acquire a one-byte non-blocking lock and release it on exit."""
    handle = path.open("a+b")
    locked = False
    try:
        if os.name == "nt":
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise TabLockUnavailable(str(path)) from exc
        else:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (BlockingIOError, OSError) as exc:
                raise TabLockUnavailable(str(path)) from exc
        locked = True
        yield handle
    finally:
        if locked:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
