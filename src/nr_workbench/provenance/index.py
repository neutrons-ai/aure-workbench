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
        return sorted(rows, key=lambda e: str(e.get("started_at") or ""), reverse=True)

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
        """Find fits whose identifier starts with ``prefix``.

        Lets a user type the first few characters of a fit_id instead of all
        of it.

        Args:
            prefix: Leading characters of a fit identifier.

        Returns:
            Matching entries, newest first.
        """
        return [e for e in self.fits() if str(e.get("fit_id", "")).startswith(prefix)]

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
