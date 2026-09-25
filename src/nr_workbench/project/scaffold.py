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
`MERGE`          Only for a ``PlannedFile`` with ``merge`` set. The file holds
                 a marked block we own inside content the user owns; insert or
                 refresh just that block.
===============  =========================================================

``UNTRACKED`` -- "the user brought their own, hands off" -- is the right answer
for every template here except ``.gitignore``, where it silently withheld the
rules that keep machine-local absolute paths out of a shared repository. That
one is planned with ``merge`` set instead; see
:mod:`nr_workbench.project.ignore` for what went wrong and why merging is the
fix rather than a louder warning.
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
    MERGE = "merge"


class LockProblemError(Exception):
    """The scaffold lock must not be written until a person fixes it."""


#: Outcomes that represent a change to the working tree.
CHANGING_OUTCOMES = frozenset(
    {Outcome.CREATE, Outcome.UPGRADE, Outcome.DRIFTED, Outcome.MERGE}
)


@dataclass(frozen=True)
class PlannedFile:
    """One file the scaffold intends to install.

    Attributes:
        relpath: POSIX-style path relative to the project root.
        content: The rendered bytes to install.
        template_id: Stable identity of the source template.
        template_version: Bumped by us when a template's content changes.
        merge: Install ``content`` as a marked block inside a file the user
            also owns, rather than as the whole file. Changes how this file is
            classified and applied -- see :data:`Outcome.MERGE`. Only
            ``.gitignore`` uses it.
        owner: Which producer's content this is, when more than one can plan
            the same file. Recorded in the lock; a plan from a *different*
            owner is never an UPGRADE -- see :func:`classify`. Only a
            catalog-rendered ``sample.md`` sets it (``"experiment"``).
    """

    relpath: str
    content: bytes
    template_id: str
    template_version: int = 1
    merge: bool = False
    owner: str | None = None


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


def _lock_is_healthy(lock_path: Path) -> bool:
    """Report whether the lock on disk is a lock we could have written.

    Distinguishes "absent or damaged" from "valid and says nothing", which
    :func:`load_lock` deliberately flattens into the same empty mapping.

    Args:
        lock_path: Path to ``.nrw/scaffold.lock.json``.

    Returns:
        True only if the file parses and carries a ``files`` object.
    """
    if not lock_path.is_file():
        return False
    try:
        document = json.loads(lock_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return False
    return isinstance(document, dict) and isinstance(document.get("files"), dict)


def lock_problem(lock_path: Path) -> str | None:
    """Why the lock on disk must not be written over, or ``None`` if it is fine.

    :func:`load_lock` reads a damaged lock as empty, which is safe for
    ``nrw init`` -- every file then reads as UNTRACKED and is left alone --
    but not for a partial plan. Writing a lock from a plan that covers one
    file, on top of a lock that failed to load, keeps that one entry and
    drops every other, permanently. The lock is tracked and shared, so a git
    conflict in it is the likely cause, and the fix is a person's.

    Args:
        lock_path: Path to ``.nrw/scaffold.lock.json``.

    Returns:
        The reason, or ``None`` when the lock is absent or healthy.
    """
    if not lock_path.exists():
        return None
    if _lock_is_healthy(lock_path):
        return None
    try:
        text = lock_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"{lock_path.name} cannot be read ({exc})."
    if "<<<<<<<" in text or ">>>>>>>" in text:
        return (
            f"{lock_path.name} contains git conflict markers. Resolve the merge "
            "(keep both sides' entries) and commit before nrw writes to it."
        )
    return (
        f"{lock_path.name} is not a lock nrw can read. Restore it from git "
        "before nrw writes to it, or every file it tracks would lose its entry."
    )


def _has_conflict_markers(lock_path: Path) -> bool:
    try:
        text = lock_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return "<<<<<<<" in text or ">>>>>>>" in text


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
    if planned.merge:
        return _classify_merge(planned, target)

    if not target.exists():
        return Outcome.CREATE

    on_disk = sha256_bytes(target.read_bytes())
    if on_disk == sha256_bytes(planned.content):
        # Already byte-identical to what we would write. Even if the lock is
        # missing or stale, there is nothing to do and nothing to warn about.
        return Outcome.UNCHANGED

    if lock_entry is None:
        return Outcome.UNTRACKED

    # A file another producer owns is not ours to "upgrade", even untouched.
    # The case this exists for: the experiment catalog renders sample.md, the
    # lock records that content, and `nrw sample new` then plans the *blank*
    # template for the same path. Unedited-since-install would read as a
    # template upgrade and reset the file to blank -- silently, since nothing
    # about it is an error. Whoever wrote it last owns it; anyone else drifts.
    recorded_owner = lock_entry.get("owner")
    if recorded_owner and recorded_owner != planned.owner:
        return Outcome.DRIFTED

    if on_disk != lock_entry.get("sha256_at_install"):
        return Outcome.DRIFTED

    # Unmodified since we installed it, but the template has changed.
    return Outcome.UPGRADE


def _classify_merge(planned: PlannedFile, target: Path) -> Outcome:
    """Decide what should happen to a merge-managed file.

    The scaffold lock is deliberately not consulted. A merged file is partly
    the user's, so a whole-file hash cannot say whether *our* part is current
    -- the markers can, and they travel with the file, so this stays correct
    through a lock loss, a fresh clone, or a hand-moved block.

    Args:
        planned: The file the scaffold wants to install, ``merge`` set.
        target: Absolute path where it would go.

    Returns:
        CREATE if the file is absent, UNCHANGED if the block is already
        current, MERGE if it needs inserting or refreshing, or DRIFTED if the
        existing block is unterminated and cannot be repaired safely.
    """
    from nr_workbench.project import ignore

    if not target.exists():
        return Outcome.CREATE

    try:
        existing = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        # Unreadable or not text. Refusing is the conservative answer: this is
        # the user's file and we would be guessing at its encoding.
        return Outcome.DRIFTED

    try:
        if ignore.is_current(existing, planned.content.decode("utf-8")):
            return Outcome.UNCHANGED
    except ignore.DamagedBlockError:
        return Outcome.DRIFTED
    return Outcome.MERGE


def merged_content(planned: PlannedFile, target: Path) -> bytes:
    """Compute what a merge-managed file should contain.

    Args:
        planned: The file the scaffold wants to install, ``merge`` set.
        target: Absolute path where it would go.

    Returns:
        The file's full new contents, with the managed block inserted or
        refreshed and everything outside it preserved.

    Raises:
        DamagedBlockError: If the existing block is unterminated. Callers
            reach this only after :func:`classify` returned MERGE, which
            already excludes that case.
    """
    from nr_workbench.project import ignore

    block = planned.content.decode("utf-8")
    if not target.exists():
        return block.encode("utf-8")
    existing = target.read_text(encoding="utf-8")
    return ignore.merge(existing, block).encode("utf-8")


def _forced_content(planned: PlannedFile, target: Path) -> bytes:
    """What ``--force`` should write over a DRIFTED file.

    Args:
        planned: The file the scaffold wants to install.
        target: Absolute path where it would go.

    Returns:
        The template bytes for an ordinary file. For a merge-managed one, the
        file with its damaged block resolved and the user's own rules kept.
    """
    if not planned.merge:
        return planned.content

    from nr_workbench.project import ignore

    try:
        existing = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        # Not text, so there is nothing to preserve and nothing to merge into.
        return planned.content
    return ignore.force_merge(existing, planned.content.decode("utf-8")).encode("utf-8")


def apply_scaffold(
    root: Path,
    planned_files: Iterable[PlannedFile],
    *,
    dry_run: bool = False,
    show_diff: bool = False,
    force: bool = False,
    lock_path: Path | None = None,
    diff_sink: list[str] | None = None,
    rebuild_lock: bool = False,
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
        rebuild_lock: Allow replacing a lock that cannot be read. Only a plan
            covering the whole project may: ``nrw init`` passes it, and its
            rebuilt lock loses only the entries it did not plan, which then
            read as UNTRACKED -- conservative. A partial plan (one sample)
            over a damaged lock would keep its own entries and drop every
            other one, so every other caller is refused.

    Returns:
        A report of what happened (or would happen).

    Raises:
        LockProblemError: The lock cannot be read and ``rebuild_lock`` is
            off -- or it holds git conflict markers, which even ``nrw init``
            must not paper over: they mean two people's entries, and a
            rebuild would keep neither side's samples.
    """
    root = Path(root).resolve()
    lock_path = lock_path or (root / ".nrw" / "scaffold.lock.json")
    trouble = lock_problem(lock_path)
    if trouble and (not rebuild_lock or _has_conflict_markers(lock_path)):
        raise LockProblemError(trouble)
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
            if outcome is Outcome.MERGE:
                _write(target, merged_content(planned, target))
                updated_lock[planned.relpath] = _lock_entry(planned)
            elif outcome is Outcome.CREATE or outcome is Outcome.UPGRADE:
                _write(target, planned.content)
                updated_lock[planned.relpath] = _lock_entry(planned)
            elif outcome is Outcome.DRIFTED:
                if force:
                    _backup(target, root, backup_root)
                    # A merge-managed file is only ever DRIFTED because its
                    # block is damaged, so `--force` must resolve the block
                    # rather than overwrite the file -- writing
                    # `planned.content` here would replace the user's whole
                    # `.gitignore` with the block and drop every rule they
                    # wrote. See `ignore.force_merge`.
                    _write(target, _forced_content(planned, target))
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

    # Only write the lock when an entry actually moved, or when what is on disk
    # is not a usable lock. The document carries an `updated` timestamp, so an
    # unconditional write made every `nrw init` -- including one that changed
    # nothing at all -- dirty a *tracked* file. For one person that is a
    # one-line diff to discard; for two sharing a project it is a merge
    # conflict on a file neither of them edited, arriving whenever either runs
    # `init`. Skipping the write also keeps `updated` meaning "when the
    # scaffold last changed" rather than "when init last ran".
    #
    # The health test is not redundant with the comparison: a corrupt lock
    # loads as empty, so a run in which every file reads as UNTRACKED would
    # compare equal and leave the corruption in place. Replacing it is how a
    # project recovers.
    if not dry_run and (updated_lock != lock or not _lock_is_healthy(lock_path)):
        write_lock(lock_path, updated_lock)

    return ScaffoldReport(root=root, files=results, dry_run=dry_run)


def _lock_entry(planned: PlannedFile) -> dict[str, Any]:
    """Build the lock entry for a freshly installed file."""
    entry: dict[str, Any] = {
        "template_id": planned.template_id,
        "template_version": planned.template_version,
        "sha256_at_install": sha256_bytes(planned.content),
    }
    if planned.owner:
        entry["owner"] = planned.owner
    return entry


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

    For a merge-managed file the comparison is against the *merged* result, not
    against the block on its own -- otherwise `nrw init --diff` would show the
    user's entire ``.gitignore`` being replaced, which is the opposite of what
    is about to happen.
    """
    old_bytes = target.read_bytes() if target.exists() else b""
    new_bytes = planned.content
    if planned.merge:
        try:
            new_bytes = merged_content(planned, target)
        except Exception:  # noqa: BLE001 - a damaged block is never applied
            new_bytes = planned.content

    if _is_binary(new_bytes) or _is_binary(old_bytes):
        return f"# {planned.relpath}: binary file, {len(new_bytes)} bytes\n"

    try:
        new_text = new_bytes.decode("utf-8")
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


# ---------------------------------------------------------------------------
# Changing ownership, with consent
# ---------------------------------------------------------------------------


def render_diff(planned: PlannedFile, target: Path) -> str:
    """A unified diff from the file on disk to ``planned``; see :func:`_render_diff`."""
    return _render_diff(planned, target)


def replace_owned(
    root: Path, planned: PlannedFile, *, lock_path: Path | None = None
) -> Path | None:
    """Replace a file somebody owns with ``planned``, keeping a backup.

    For an explicit hand-over only -- the experiment catalog adopting a
    ``sample.md`` a person wrote, after they have seen the diff and said yes.
    Deliberately separate from ``apply_scaffold(force=True)``: widening
    ``--force`` to UNTRACKED files would let ``nrw init --force`` overwrite
    files users brought themselves, which it has never done.

    Args:
        root: Project root.
        planned: The file to install, recorded in the lock as installed.
        lock_path: Override the lock location.

    Returns:
        Where the previous file was backed up, or ``None`` if there was none.

    Raises:
        LockProblemError: If the lock cannot be safely written.
    """
    root = Path(root).resolve()
    lock_path = lock_path or (root / ".nrw" / "scaffold.lock.json")
    trouble = lock_problem(lock_path)
    if trouble:
        raise LockProblemError(trouble)

    target = root / planned.relpath
    backup: Path | None = None
    if target.exists():
        backup_root = (
            root / ".nrw" / "backups" / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        )
        _backup(target, root, backup_root)
        backup = backup_root / planned.relpath
    _write(target, planned.content)

    entries = load_lock(lock_path)
    entries[planned.relpath] = _lock_entry(planned)
    write_lock(lock_path, entries)
    return backup


def forget(root: Path, relpath: str, *, lock_path: Path | None = None) -> bool:
    """Drop a file's lock entry, so nrw treats it as its owner's from now on.

    The file is not touched. With no entry it classifies as UNTRACKED, which
    no plan ever writes over.

    Returns:
        Whether there was an entry to drop.

    Raises:
        LockProblemError: If the lock cannot be safely written.
    """
    root = Path(root).resolve()
    lock_path = lock_path or (root / ".nrw" / "scaffold.lock.json")
    trouble = lock_problem(lock_path)
    if trouble:
        raise LockProblemError(trouble)
    entries = load_lock(lock_path)
    if relpath not in entries:
        return False
    del entries[relpath]
    write_lock(lock_path, entries)
    return True
