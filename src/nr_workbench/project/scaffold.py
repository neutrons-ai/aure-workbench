"""The idempotent scaffold engine behind ``nrw init``.

`init` must be safe to run repeatedly, including on top of a live beamtime
folder full of a scientist's own files. The rule that makes that safe is
recorded per file in ``.nrw/scaffold.lock.json``: we remember the sha256 of
every file *as we installed it*, so on a later run we can tell "unchanged since
we wrote it" (safe to upgrade) apart from "the user edited this" (never touch).

Outcomes, one per templated file:

===============  =========================================================
`CREATE`         Not on disk. Write it.
`UNCHANGED`      On disk, matches the template already. Nothing to do.
`UPGRADE`        On disk, matches what *we* installed, template has moved on.
`DRIFTED`        On disk, differs from what we installed. Never overwrite --
                 write ``<path>.nrw-new`` beside it and report.
`UNTRACKED`      On disk but absent from the lock. Treat as the user's.
===============  =========================================================
"""

from __future__ import annotations

import difflib
import hashlib
import json
import shutil
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

LOCK_SCHEMA = "nrw-scaffold-lock/1"

#: Suffix used when we refuse to clobber a user-edited file.
NEW_SUFFIX = ".nrw-new"


class Outcome(StrEnum):
    """What happened (or would happen) to one templated file."""

    CREATE = "create"
    UNCHANGED = "unchanged"
    UPGRADE = "upgrade"
    DRIFTED = "drifted"
    UNTRACKED = "untracked"


#: Outcomes that represent a change to the working tree.
CHANGING_OUTCOMES = frozenset({Outcome.CREATE, Outcome.UPGRADE, Outcome.DRIFTED})


@dataclass(frozen=True)
class PlannedFile:
    """One file the scaffold intends to install.

    Attributes:
        relpath: POSIX-style path relative to the project root.
        content: The rendered bytes to install.
        template_id: Stable identity of the source template.
        template_version: Bumped by us when a template's content changes.
    """

    relpath: str
    content: bytes
    template_id: str
    template_version: int = 1


@dataclass(frozen=True)
class FileResult:
    """The outcome of planning or applying one file.

    Attributes:
        relpath: POSIX-style path relative to the project root.
        outcome: What happened, or would happen on a dry run.
        wrote_alongside: Path of the ``.nrw-new`` file written for a DRIFTED
            file, relative to the project root; ``None`` otherwise.
    """

    relpath: str
    outcome: Outcome
    wrote_alongside: str | None = None


@dataclass
class ScaffoldReport:
    """Summary of a scaffold run.

    Attributes:
        root: Project root the scaffold was applied to.
        files: Per-file results, in plan order.
        dry_run: True if nothing was written.
    """

    root: Path
    files: list[FileResult]
    dry_run: bool = False

    @property
    def changed(self) -> list[FileResult]:
        """Files whose outcome represents a change to the working tree."""
        return [f for f in self.files if f.outcome in CHANGING_OUTCOMES]

    @property
    def drifted(self) -> list[FileResult]:
        """Files the user has edited, which we refused to overwrite."""
        return [f for f in self.files if f.outcome is Outcome.DRIFTED]

    def count(self, outcome: Outcome) -> int:
        """Count files with a given outcome.

        Args:
            outcome: The outcome to count.

        Returns:
            The number of matching files.
        """
        return sum(1 for f in self.files if f.outcome is outcome)


def sha256_bytes(data: bytes) -> str:
    """Return the hex sha256 of ``data``.

    Args:
        data: Bytes to hash.

    Returns:
        Lowercase hex digest.
    """
    return hashlib.sha256(data).hexdigest()


def load_lock(lock_path: Path) -> dict[str, dict[str, Any]]:
    """Load the scaffold lock, tolerating absence and corruption.

    A damaged lock must not brick `init`. We fall back to an empty lock, which
    downgrades every existing file to UNTRACKED -- conservative, because
    UNTRACKED files are never modified.

    Args:
        lock_path: Path to ``.nrw/scaffold.lock.json``.

    Returns:
        Mapping of relpath to its lock entry.
    """
    if not lock_path.is_file():
        return {}
    try:
        document = json.loads(lock_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}
    entries = document.get("files")
    if not isinstance(entries, dict):
        return {}
    return entries


def write_lock(lock_path: Path, entries: Mapping[str, dict[str, Any]]) -> None:
    """Write the scaffold lock atomically.

    Args:
        lock_path: Path to ``.nrw/scaffold.lock.json``.
        entries: Mapping of relpath to lock entry.
    """
    document = {
        "schema": LOCK_SCHEMA,
        "updated": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "files": dict(sorted(entries.items())),
    }
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = lock_path.with_suffix(lock_path.suffix + ".tmp")
    tmp.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    tmp.replace(lock_path)


def classify(
    planned: PlannedFile,
    target: Path,
    lock_entry: Mapping[str, Any] | None,
) -> Outcome:
    """Decide what should happen to one file.

    Args:
        planned: The file the scaffold wants to install.
        target: Absolute path where it would go.
        lock_entry: The file's entry from the scaffold lock, if any.

    Returns:
        The outcome for this file.
    """
    if not target.exists():
        return Outcome.CREATE

    on_disk = sha256_bytes(target.read_bytes())
    if on_disk == sha256_bytes(planned.content):
        # Already byte-identical to what we would write. Even if the lock is
        # missing or stale, there is nothing to do and nothing to warn about.
        return Outcome.UNCHANGED

    if lock_entry is None:
        return Outcome.UNTRACKED

    if on_disk != lock_entry.get("sha256_at_install"):
        return Outcome.DRIFTED

    # Unmodified since we installed it, but the template has changed.
    return Outcome.UPGRADE


def apply_scaffold(
    root: Path,
    planned_files: Iterable[PlannedFile],
    *,
    dry_run: bool = False,
    show_diff: bool = False,
    force: bool = False,
    lock_path: Path | None = None,
    diff_sink: list[str] | None = None,
) -> ScaffoldReport:
    """Install a set of templated files under ``root``, idempotently.

    Args:
        root: Project root to install into. Created if absent.
        planned_files: The files to install.
        dry_run: Plan only; write nothing (this is what ``--check`` uses).
        show_diff: Collect unified diffs for changing files into ``diff_sink``.
        force: Overwrite DRIFTED files, backing the originals up under
            ``.nrw/backups/<timestamp>/``.
        lock_path: Override the lock location. Defaults to
            ``root/.nrw/scaffold.lock.json``.
        diff_sink: List to append rendered diffs to when ``show_diff`` is set.

    Returns:
        A report of what happened (or would happen).
    """
    root = Path(root).resolve()
    lock_path = lock_path or (root / ".nrw" / "scaffold.lock.json")
    lock = load_lock(lock_path)
    updated_lock = dict(lock)

    backup_root = (
        root / ".nrw" / "backups" / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    )
    results: list[FileResult] = []

    for planned in planned_files:
        target = root / planned.relpath
        outcome = classify(planned, target, lock.get(planned.relpath))

        if show_diff and diff_sink is not None and outcome in CHANGING_OUTCOMES:
            diff_sink.append(_render_diff(planned, target))

        alongside: str | None = None

        if not dry_run:
            if outcome is Outcome.CREATE or outcome is Outcome.UPGRADE:
                _write(target, planned.content)
                updated_lock[planned.relpath] = _lock_entry(planned)
            elif outcome is Outcome.DRIFTED:
                if force:
                    _backup(target, root, backup_root)
                    _write(target, planned.content)
                    updated_lock[planned.relpath] = _lock_entry(planned)
                    outcome = Outcome.UPGRADE
                else:
                    side = target.with_name(target.name + NEW_SUFFIX)
                    _write(side, planned.content)
                    alongside = side.relative_to(root).as_posix()
            elif outcome is Outcome.UNCHANGED:
                # Keep the lock honest even if it was missing or stale: the
                # bytes on disk are ours, so record them.
                updated_lock[planned.relpath] = _lock_entry(planned)

        results.append(FileResult(planned.relpath, outcome, alongside))

    if not dry_run:
        write_lock(lock_path, updated_lock)

    return ScaffoldReport(root=root, files=results, dry_run=dry_run)


def _lock_entry(planned: PlannedFile) -> dict[str, Any]:
    """Build the lock entry for a freshly installed file."""
    return {
        "template_id": planned.template_id,
        "template_version": planned.template_version,
        "sha256_at_install": sha256_bytes(planned.content),
    }


def _write(target: Path, content: bytes) -> None:
    """Create parent directories and write ``content`` to ``target``."""
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)


def _backup(target: Path, root: Path, backup_root: Path) -> None:
    """Copy ``target`` into the timestamped backup tree, preserving its path."""
    destination = backup_root / target.relative_to(root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(target, destination)


def _is_binary(data: bytes) -> bool:
    """Report whether ``data`` should be treated as binary for diffing.

    A NUL byte is the test git uses, and it catches content that decodes as
    UTF-8 but is not text -- control bytes are valid code points, so decoding
    alone is not a sufficient check.
    """
    if b"\x00" in data:
        return True
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False


def _render_diff(planned: PlannedFile, target: Path) -> str:
    """Render a unified diff between the on-disk file and the planned content.

    Binary content is reported as a one-line summary rather than mangled into
    the diff.
    """
    old_bytes = target.read_bytes() if target.exists() else b""
    if _is_binary(planned.content) or _is_binary(old_bytes):
        return f"# {planned.relpath}: binary file, {len(planned.content)} bytes\n"

    try:
        new_text = planned.content.decode("utf-8")
        old_text = old_bytes.decode("utf-8")
    except UnicodeDecodeError:  # pragma: no cover - guarded by _is_binary
        return f"# {planned.relpath}: binary file, {len(planned.content)} bytes\n"

    diff = difflib.unified_diff(
        old_text.splitlines(keepends=True),
        new_text.splitlines(keepends=True),
        fromfile=f"a/{planned.relpath}",
        tofile=f"b/{planned.relpath}",
    )
    return "".join(diff)
