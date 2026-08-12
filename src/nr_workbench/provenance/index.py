"""The append-only fit index at ``.nrw/index.jsonl``.

One JSON object per line, appended and never rewritten. That choice buys three
things a database would not:

* **Git merges cleanly.** Two scientists fitting on separate branches produce
  appends to different lines, not a binary conflict.
* **It is greppable.** ``grep Sample4 .nrw/index.jsonl`` works with no tooling.
* **History is never lost.** Superseding a "final" result appends a new line;
  the old one stays. What was once considered final is itself provenance.

Appends are serialised with an advisory lock, because two fits finishing at the
same moment must not interleave a line.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any

INDEX_FILENAME = "index.jsonl"

#: Event kinds appended to the index. A fit run is not the only thing worth
#: recording -- blessing and superseding a result are decisions with authors.
EVENT_FIT = "fit"
EVENT_PROMOTE = "promote"
EVENT_SUPERSEDE = "supersede"


@contextmanager
def _locked(path: Path) -> Iterator[None]:
    """Hold an advisory lock for the duration of a write.

    Falls back to no locking where ``fcntl`` is unavailable (Windows). A lost
    append is worse than a slow one, but an unavailable lock is not a reason to
    refuse to record anything.
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


def _hash_of(fit_id: str) -> str:
    """The content-hash half of a fit id, or the whole thing if it has none.

    ``20260807-150822Z-b2cef12a`` -> ``b2cef12a``. A collision suffix
    (``-2``) is kept, so ``b2cef12a-2`` still resolves.
    """
    parts = fit_id.split("-", 2)
    return parts[2] if len(parts) == 3 else fit_id


class FitIndex:
    """Reads and appends to the project's fit index."""

    def __init__(self, path: Path) -> None:
        """Bind to an index file.

        Args:
            path: Path to ``.nrw/index.jsonl``.
        """
        self.path = Path(path)

    def append(self, entry: dict[str, Any], *, event: str = EVENT_FIT) -> None:
        """Append one entry.

        Args:
            entry: The record to append. A shallow copy is taken, so the
                caller's dict is not mutated.
            event: The event kind; see the module constants.
        """
        payload = {"event": event, **entry}
        line = json.dumps(payload, separators=(",", ":"), default=str)
        with _locked(self.path):
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")

    def forget(self, sample: str) -> int:
        """Remove every entry for one sample. Returns how many were dropped.

        The only operation here that is not append-only, and it exists because
        deleting files could not work without it. The index is the project's
        memory: removing a result directory by hand leaves the fit recorded,
        `nrw ls` reports it BROKEN forever, and an unattended session -- which
        reads the index, not the directory -- keeps counting from models it can
        no longer see. That is how a sample whose output had been deleted still
        started at `corefine6`.

        Rewritten through a temporary file inside the lock, so a crash mid-write
        leaves the original index rather than half of one.

        Args:
            sample: Sample identifier to forget.

        Returns:
            Number of entries removed.
        """
        with _locked(self.path):
            if not self.path.is_file():
                return 0
            kept: list[str] = []
            dropped = 0
            for line in self.path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    value = json.loads(stripped)
                except json.JSONDecodeError:
                    kept.append(stripped)  # unreadable: not ours to discard
                    continue
                if isinstance(value, dict) and value.get("sample") == sample:
                    dropped += 1
                    continue
                kept.append(stripped)

            if dropped:
                temporary = self.path.with_suffix(".jsonl.rewriting")
                temporary.write_text(
                    "".join(f"{line}\n" for line in kept), encoding="utf-8"
                )
                temporary.replace(self.path)
            return dropped

    def entries(self) -> list[dict[str, Any]]:
        """Read every entry, oldest first.

        A malformed line is skipped rather than fatal: the index is append-only
        and may have been touched by hand or truncated by a crash, and one bad
        line must not make every other record unreadable.

        Returns:
            Parsed entries in file order.
        """
        if not self.path.is_file():
            return []
        parsed: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                parsed.append(value)
        return parsed

    def fits(self, *, sample: str | None = None) -> list[dict[str, Any]]:
        """Return fit entries, newest first.

        Args:
            sample: Restrict to one sample.

        Returns:
            Fit entries, most recent first.
        """
        rows = [e for e in self.entries() if e.get("event", EVENT_FIT) == EVENT_FIT]
        if sample is not None:
            rows = [e for e in rows if e.get("sample") == sample]
        # `started_at` is only second-resolution, and two quick fits land inside
        # the same second. The index is append-only, so its own line order is
        # the true chronology; use it to break the tie. Sorting on the timestamp
        # alone is stable, which for reverse=True leaves ties *oldest* first --
        # the exact opposite of what "newest first" promises.
        ordered = sorted(
            enumerate(rows),
            key=lambda pair: (str(pair[1].get("started_at") or ""), pair[0]),
            reverse=True,
        )
        return [row for _, row in ordered]

    def find(self, fit_id: str) -> dict[str, Any] | None:
        """Look up one fit entry by identifier.

        Args:
            fit_id: The fit identifier.

        Returns:
            The entry, or ``None`` if unknown.
        """
        for entry in self.entries():
            if (
                entry.get("fit_id") == fit_id
                and entry.get("event", EVENT_FIT) == EVENT_FIT
            ):
                return entry
        return None

    def resolve(self, prefix: str) -> list[dict[str, Any]]:
        """Find fits by identifier prefix, or failing that by content hash.

        A fit id is ``<timestamp>-<hash>``, and the half that distinguishes
        two fits from the same afternoon is the hash. Prefix-only matching
        would mean typing all sixteen characters of the timestamp to reach the
        part that actually identifies the run, so the hash is accepted too.

        Prefixes are tried first and alone when they match, so this can only
        widen what already resolved, never change it.

        Args:
            prefix: Leading characters of a fit identifier, or its hash.

        Returns:
            Matching entries, newest first.
        """
        rows = self.fits()
        matches = [e for e in rows if str(e.get("fit_id", "")).startswith(prefix)]
        if matches:
            return matches
        return [
            e for e in rows if _hash_of(str(e.get("fit_id", ""))).startswith(prefix)
        ]

    def find_by_run_key(self, run_key: str) -> list[dict[str, Any]]:
        """Find fits with the same run key -- same script, inputs, settings, env.

        Args:
            run_key: The run key to match.

        Returns:
            Matching entries, newest first.
        """
        return [e for e in self.fits() if e.get("run_key") == run_key]

    def promotions(self) -> list[dict[str, Any]]:
        """Return promotion events, oldest first.

        Returns:
            Promotion entries in the order they happened.
        """
        return [e for e in self.entries() if e.get("event") == EVENT_PROMOTE]

    def current_label(
        self, label: str, *, sample: str | None = None
    ) -> dict[str, Any] | None:
        """Return the promotion currently holding ``label``.

        The last promotion of a label wins, but every earlier one stays in the
        index -- the history of what was once considered final is provenance in
        its own right.

        Args:
            label: The label, e.g. ``"final"``.
            sample: Restrict to one sample.

        Returns:
            The most recent matching promotion, or ``None``.
        """
        matches = [
            e
            for e in self.promotions()
            if e.get("label") == label and (sample is None or e.get("sample") == sample)
        ]
        return matches[-1] if matches else None
