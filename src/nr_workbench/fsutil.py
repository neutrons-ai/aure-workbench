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
import stat
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path

#: Refuse a symbolic link where it would otherwise be followed. Every name nrw
#: creates in a project is one another account could plant a link at, when
#: the project sits in a group-writable shared directory.
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


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
    # Never through a link: in a project another account can write, one planted
    # at this name would otherwise have nrw create a file wherever it points.
    handle = os.open(lock_path, os.O_CREAT | os.O_RDWR | _NOFOLLOW, 0o644)
    try:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield
    finally:
        with suppress(OSError):
            fcntl.flock(handle, fcntl.LOCK_UN)
        os.close(handle)


def atomic_write_bytes(
    target: Path, data: bytes, *, scratch: Path | None = None, mode: int | None = None
) -> None:
    """Replace *target* with *data*, so a reader sees all of the old or the new.

    The temp file is new to this call -- created exclusively, never through a
    link -- so two writers never write into one another's, and a name another
    account planted in a shared project cannot redirect the data. It is
    fsynced before the rename, so a crash cannot leave a renamed but empty
    file; it is removed if anything fails; and the replaced file's permission
    bits are kept.

    Args:
        target: The file to replace.
        data: Its new contents.
        scratch: Where to put the temp file, when not beside *target* -- for
            a directory whose every file is tracked or watched. It must be on
            the same filesystem as *target*, or the rename is not atomic.
        mode: The permission bits to give it, instead of the replaced file's.
            For a caller that has already read the target through a descriptor
            of its own: *target* is then never looked up by name for its mode,
            which would follow a link put there meanwhile.
    """
    folder = scratch if scratch is not None else target.parent
    folder.mkdir(parents=True, exist_ok=True)
    temp = folder / f".{target.name}.{secrets.token_hex(6)}.tmp"
    # Born with the mode it is given: a temp file holding a .env's keys must
    # never be readable by more accounts than the file it replaces.
    created = 0o666 if mode is None else mode & 0o777
    descriptor = os.open(
        temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW, created
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            # The file keeps its permissions: set on the open file, not by
            # name, before its content, and only the permission bits -- a file
            # nrw writes never needs setuid, setgid or sticky, whatever the old
            # one had. The temp file has this process's umask, and a
            # group-writable nrw.toml in a shared project would otherwise come
            # back writable by its owner alone.
            if mode is None:
                with suppress(FileNotFoundError):
                    mode = stat.S_IMODE(os.stat(target).st_mode) & 0o777
            if mode is not None and hasattr(os, "fchmod"):
                os.fchmod(handle.fileno(), mode & 0o777)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)


def write_new_file(folder: Path, name: str, data: bytes, *, base: Path) -> Path:
    """Create ``folder/name`` holding *data*, reaching it through no link at all.

    For a copy that must land where it is meant to and nowhere else -- the
    backup of a file about to be replaced. Every folder from *base* down is
    opened without following a link, so a link another account put in place of
    one of them, even a moment before, is refused instead of written through;
    and the file must not exist yet.

    Args:
        folder: Where the file goes. Created if missing; must be below *base*.
        name: The file's name.
        data: Its content.
        base: A folder trusted as it is -- the project root.

    Raises:
        OSError: A folder on the way is a symbolic link (``ELOOP``), the file
            exists, or it cannot be written.
    """
    base, folder = Path(base), Path(folder)
    parts = folder.relative_to(base).parts
    if not (os.open in os.supports_dir_fd and os.mkdir in os.supports_dir_fd):
        # No descriptor-relative calls here (Windows): check, then create.
        for depth in range(1, len(parts) + 1):
            if base.joinpath(*parts[:depth]).is_symlink():
                raise OSError(f"{base.joinpath(*parts[:depth])} is a symbolic link")
        folder.mkdir(parents=True, exist_ok=True)
        with open(folder / name, "xb") as handle:
            handle.write(data)
        return folder / name

    directory = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    current = os.open(base, directory)
    try:
        for part in parts:
            with suppress(FileExistsError):
                os.mkdir(part, 0o777, dir_fd=current)
            below = os.open(part, directory | _NOFOLLOW, dir_fd=current)
            os.close(current)
            current = below
        descriptor = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW,
            0o666,
            dir_fd=current,
        )
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
    finally:
        os.close(current)
    return folder / name
