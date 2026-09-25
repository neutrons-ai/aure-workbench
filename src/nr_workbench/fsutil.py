"""Two file operations several modules need, done one way.

The fit index, the scaffold lock, the experiment catalog and the copy record
are each written by more than one process at once -- ``nrw serve``'s request
threads, a terminal, an unattended agent -- and each of them once had its own
lock or its own temp-file-and-rename. Copies drift: one fsynced and another did
not, one named its temp file per writer and another shared a single name
between writers. They live here instead.
"""

from __future__ import annotations

import os
import secrets
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path


@contextmanager
def advisory_lock(path: Path) -> Iterator[None]:
    """Hold an exclusive advisory lock on ``<path>.lock`` while writing *path*.

    Advisory: it excludes only other callers of this function, which is every
    writer nrw has. Falls back to no locking where ``fcntl`` is unavailable
    (Windows). A lost write is worse than a slow one, but an unavailable lock is
    not a reason to refuse to write anything.

    Args:
        path: The file being protected. Its directory is created if missing.

    Raises:
        OSError: The lock file cannot be created -- a read-only mount, say.
    """
    try:
        import fcntl
    except ImportError:  # pragma: no cover - Windows
        yield
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    handle = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield
    finally:
        with suppress(OSError):
            fcntl.flock(handle, fcntl.LOCK_UN)
        os.close(handle)


def atomic_write_bytes(
    target: Path, data: bytes, *, scratch: Path | None = None
) -> None:
    """Replace *target* with *data*, so a reader sees all of the old or the new.

    The temp file is unique to this call, so two writers never write into one
    another's; it is fsynced before the rename, so a crash cannot leave a
    renamed but empty file; and it is removed if anything fails.

    Args:
        target: The file to replace.
        data: Its new contents.
        scratch: Where to put the temp file, when not beside *target* -- for
            a directory whose every file is tracked or watched. It must be on
            the same filesystem as *target*, or the rename is not atomic.
    """
    folder = scratch if scratch is not None else target.parent
    folder.mkdir(parents=True, exist_ok=True)
    temp = folder / f".{target.name}.{secrets.token_hex(6)}.tmp"
    try:
        with temp.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)
