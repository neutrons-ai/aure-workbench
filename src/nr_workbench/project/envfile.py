"""The project's ``.env``: the lines nrw's pages write, and nothing else.

``.env`` is the person's file. It may hold keys, comments and settings for
other tools, so nrw changes only the ``NAME=value`` lines of the variables it
was asked to set and leaves every other byte as it was. It is written by
replacing the file, never through a symbolic link, with its mode kept; a new
one is readable by its owner only, because it is where keys go.

A change is made against the revision of the file the page was shown, so an
edit made by hand in between is never written over.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

#: The file, at the project's root: the first one :mod:`nr_workbench.env` reads.
ENV_FILE = ".env"

#: The revision of a project with no ``.env``.
ABSENT = "absent"

#: What the lines nrw adds are headed with, so a person reading the file knows
#: where they came from.
HEADER = "# Set on the Settings page of `nrw serve`."

#: A value nrw writes: one plain word, or nothing. No quotes, spaces, ``#`` or
#: ``$``, which a dotenv reader takes as quoting, a comment or a variable.
_VALUE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@\[\]-]{0,127}")

#: A line that sets a variable, ``export`` or not.
_ASSIGNMENT_RE = re.compile(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")

#: The mode of a new file: the owner's alone.
NEW_FILE_MODE = 0o600


class EnvFileError(Exception):
    """The project's ``.env`` cannot be changed safely; the message says why."""


class EnvFileConflict(EnvFileError):
    """``.env`` changed since it was read."""


@dataclass(frozen=True)
class EnvFile:
    """The project's ``.env`` as it is now.

    Attributes:
        path: Where it is, or would be.
        text: What it holds; ``None`` when there is no file.
        revision: What a change must be made against.
    """

    path: Path
    text: str | None
    revision: str


def read_env_file(root: Path) -> EnvFile:
    """Read the project's ``.env``.

    Args:
        root: The project.

    Returns:
        Its text and revision.

    Raises:
        EnvFileError: It exists and cannot be read as text.
    """
    path = Path(root) / ENV_FILE
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return EnvFile(path=path, text=None, revision=ABSENT)
    except OSError as exc:
        raise EnvFileError(f"{ENV_FILE} cannot be read: {exc.strerror}") from exc
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EnvFileError(f"{ENV_FILE} is not UTF-8 text.") from exc
    return EnvFile(path=path, text=text, revision=hashlib.sha256(data).hexdigest())


def check_value(name: str, value: str) -> str:
    """A value nrw may write for *name*: one plain word, or empty.

    Raises:
        EnvFileError: It holds a space, a quote, ``#``, ``$`` or a line break,
            or is too long.
    """
    if value and not _VALUE_RE.fullmatch(value):
        raise EnvFileError(
            f"{name} must be one word of letters, digits and . _ - : / @ [ ], "
            f"128 characters at most, not {value!r}."
        )
    return value


def set_values(
    root: Path, values: dict[str, str | None], *, base_revision: str
) -> list[str]:
    """Set, or remove, variables in the project's ``.env``.

    A variable already set has its first line replaced where it is, and any
    later line setting it again removed, so the file says it once. One not set
    yet is added at the end, under :data:`HEADER`. Every other line is kept as
    it was.

    Args:
        root: The project.
        values: Each variable's new value, or ``None`` to remove it.
        base_revision: The revision the change was decided against.

    Returns:
        What changed, one line each, for the person to read; empty when nothing
        needed changing.

    Raises:
        EnvFileConflict: The file changed since *base_revision*.
        EnvFileError: A value is not one nrw writes, or the file cannot be
            written safely: it is a symbolic link, or not a regular file.
    """
    for name, value in values.items():
        if value is not None:
            check_value(name, value)
    current = read_env_file(root)
    if current.revision != base_revision:
        raise EnvFileConflict(
            f"{ENV_FILE} has changed since this page read it. Reload the page to "
            "see what it holds now."
        )
    path = current.path
    if path.is_symlink():
        raise EnvFileError(
            f"{ENV_FILE} is a symbolic link, and nrw never writes through one. "
            "Edit the file it points at by hand."
        )
    if path.exists() and not path.is_file():
        raise EnvFileError(f"{ENV_FILE} is not a regular file.")

    lines = (current.text or "").splitlines()
    kept: list[str] = []
    changes: list[str] = []
    done: set[str] = set()
    for line in lines:
        match = _ASSIGNMENT_RE.match(line)
        name = match.group(1) if match else None
        if name not in values:
            kept.append(line)
            continue
        wanted = values[name]
        if name in done or wanted is None:
            # Said once, or not at all: a later line would win over the first.
            changes.append(f"removed {line.strip()}")
            done.add(name)
            continue
        new = f"{name}={wanted}"
        if line != new:
            changes.append(f"{line.strip()} is now {new}")
        kept.append(new)
        done.add(name)
    added = [
        f"{name}={value}"
        for name, value in values.items()
        if value is not None and name not in done
    ]
    if added:
        while kept and not kept[-1].strip():
            kept.pop()
        if HEADER not in kept:
            kept += [""] if kept else []
            kept.append(HEADER)
        kept += added
        changes += [f"added {line}" for line in added]
    kept = _without_orphan_header(kept)
    if not changes:
        return []
    # A file written on Windows keeps its line endings.
    newline = "\r\n" if "\r\n" in (current.text or "") else "\n"
    _replace(path, "".join(line + newline for line in kept), current.text is not None)
    return changes


def _without_orphan_header(lines: list[str]) -> list[str]:
    """Drop :data:`HEADER` when nothing is left under it."""
    out: list[str] = []
    for index, line in enumerate(lines):
        after = lines[index + 1] if index + 1 < len(lines) else ""
        if line == HEADER and not after.strip():
            continue
        out.append(line)
    while out and not out[-1].strip():
        out.pop()
    return out


def _replace(path: Path, text: str, existed: bool) -> None:
    """Write *text* to *path* by replacing it: whole or not at all.

    Raises:
        EnvFileError: It could not be written.
    """
    mode = (path.stat().st_mode & 0o777) if existed else NEW_FILE_MODE
    handle, temporary = tempfile.mkstemp(
        dir=path.parent, prefix=f"{ENV_FILE}.", suffix=".nrw-tmp"
    )
    try:
        # newline="": the text's own line endings, untranslated.
        with os.fdopen(handle, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
        os.chmod(temporary, mode)
        # Replaces the directory entry: a link put there since the check above
        # is replaced, never written through.
        os.replace(temporary, path)
    except OSError as exc:
        Path(temporary).unlink(missing_ok=True)
        raise EnvFileError(f"{ENV_FILE} could not be written: {exc.strerror}") from exc
