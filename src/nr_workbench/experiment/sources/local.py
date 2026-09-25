"""The data source for a machine with the data mount: one folder, polled.

Polling, not filesystem events: the folder is on NFS, where inotify is a
suggestion, and the reduction takes minutes anyway -- the same reasoning, and
the same conclusion, as ``nrw agent watch``.

Three things this is careful about, because the folder is shared:

* **One listing per poll.** Every file's size and times come from that single
  ``scandir``; nothing is stat'ed twice, and headers are re-read only when a
  file's identity changes.
* **Nothing is followed.** A symbolic link in a team-writable folder, named like
  a reduced file, could point anywhere the scientist can read; copying it would
  put that file into their repository. Links are reported and skipped, and
  files are opened with ``O_NOFOLLOW``.
* **Only canonical names count.** A name the reduction would not have written
  exactly is reported, not used -- see
  :func:`nr_workbench.instrument.reduced.canonical_name`.
"""

from __future__ import annotations

import contextlib
import os
import stat as stat_module
import threading
from collections import OrderedDict
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from nr_workbench.agent.watch import fingerprint_entries, segment_problems
from nr_workbench.experiment.config import normalize_ipts
from nr_workbench.experiment.inventory import Inventory, SourceFile, SourceRun
from nr_workbench.experiment.model import RunKey, clean_title_snapshot
from nr_workbench.experiment.sources import SourceChangedError, SourceFileTooLarge
from nr_workbench.instrument.reduced import (
    STEADY_SUFFIXES,
    ReducedName,
    canonical_name,
    parse_combined_name,
    parse_segment_name,
)
from nr_workbench.problems import Problem

#: How many file headers to remember between polls. A beamtime folder holds a
#: few thousand files; this keeps every one of them without growing forever.
HEADER_CACHE_SIZE = 8192


class LocalDirectorySource:
    """Reduced files in one directory on this machine.

    Args:
        path: The folder, resolved. ``None`` when the configuration could not
            produce one; the inventory then says why.
        location: The location as configured, for display.
        ipts: The project's IPTS, to notice a file from another experiment.
    """

    kind = "local"

    def __init__(self, path: Path | None, location: str, *, ipts: str | None) -> None:
        self.path = Path(path) if path is not None else None
        self.location = location
        self.ipts = normalize_ipts(ipts)
        self._headers: OrderedDict[tuple[Any, ...], Any] = OrderedDict()
        self._lock = threading.Lock()

    def describe(self) -> dict[str, Any]:
        """Kind and location, for the page."""
        return {
            "kind": self.kind,
            "location": self.location,
            "path": str(self.path) if self.path is not None else None,
        }

    # ------------------------------------------------------------------
    # Listing
    # ------------------------------------------------------------------

    def inventory(self) -> Inventory:
        """Every run in the folder, judged from one listing."""
        if self.path is None:
            return Inventory(
                reachable=False,
                problems=(
                    Problem(
                        "source",
                        "No data location is configured; see the configuration "
                        "problems above.",
                    ),
                ),
            )
        try:
            with os.scandir(self.path) as listing:
                entries = list(listing)
        except FileNotFoundError:
            return self._unreachable(
                f"{self.path} does not exist. Is the data mount available on "
                "this machine? The location is set by [experiment.source] "
                "location in nrw.toml."
            )
        except NotADirectoryError:
            return self._unreachable(f"{self.path} is not a directory.")
        except PermissionError:
            return self._unreachable(f"{self.path} cannot be read by this account.")
        except OSError as exc:
            return self._unreachable(f"{self.path} cannot be listed: {exc}")

        files: dict[int, list[tuple[SourceFile, os.stat_result]]] = {}
        unrecognized: list[str] = []
        noncanonical: list[str] = []
        links: list[str] = []
        other = 0
        problems: list[Problem] = []

        for entry in sorted(entries, key=lambda e: e.name):
            name = entry.name
            try:
                if entry.is_symlink():
                    links.append(name)
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
                info = entry.stat(follow_symlinks=False)
            except OSError as exc:
                problems.append(Problem(f"source:{name}", f"cannot be read: {exc}"))
                continue
            if Path(name).suffix not in STEADY_SUFFIXES:
                other += 1
                continue

            parsed = canonical_name(name)
            if parsed is None:
                loose = parse_segment_name(name) or parse_combined_name(name)
                (noncanonical if loose is not None else unrecognized).append(name)
                continue
            source_file = _source_file(name, info, parsed)
            run = parsed.run if isinstance(parsed, ReducedName) else parsed
            files.setdefault(run, []).append((source_file, info))

        if links:
            problems.append(
                Problem(
                    "source",
                    f"{len(links)} symbolic link(s) not followed, e.g. {links[0]}. "
                    "A link in a shared folder can point anywhere this account "
                    "can read, so nrw never copies through one.",
                )
            )
        if noncanonical:
            problems.append(
                Problem(
                    "source",
                    f"{len(noncanonical)} file(s) are named almost, but not "
                    f"exactly, like reduced data (e.g. {noncanonical[0]!r}) and "
                    "are not used.",
                )
            )
        if unrecognized:
            problems.append(
                Problem(
                    "source",
                    f"{len(unrecognized)} data file(s) match no known "
                    f"reduced-file name, e.g. {unrecognized[0]}. If this is a "
                    "new reduction format, instrument/reduced.py is where nrw "
                    "learns it.",
                )
            )

        runs = {
            RunKey(run): self._run(run, members)
            for run, members in sorted(files.items())
        }
        return Inventory(
            runs=runs,
            unrecognized=tuple(unrecognized),
            other_files=other,
            reachable=True,
            problems=tuple(problems),
        )

    def _unreachable(self, message: str) -> Inventory:
        return Inventory(reachable=False, problems=(Problem("source", message),))

    def _run(
        self, run: int, members: list[tuple[SourceFile, os.stat_result]]
    ) -> SourceRun:
        """Assemble one run and say what is wrong with it, if anything."""
        from nr_workbench.project.scan import SteadyMeasurement

        members.sort(key=lambda m: (m[0].segment is None, m[0].segment or 0, m[0].name))
        problems: list[str] = []

        by_segment: dict[int, list[SourceFile]] = {}
        for source_file, _ in members:
            if source_file.segment is not None:
                by_segment.setdefault(source_file.segment, []).append(source_file)
        for segment, copies in sorted(by_segment.items()):
            if len(copies) > 1:
                problems.append(
                    f"segment {segment} is here twice "
                    f"({' and '.join(c.name for c in copies)}): the run was "
                    "reduced again by the other pipeline, and which one to use "
                    "is a question for a person."
                )
        if not problems and by_segment:
            measurement = SteadyMeasurement(
                run=run, partials={s: c[0].name for s, c in by_segment.items()}
            )
            held = segment_problems(measurement)
            if held:
                problems.append(held)

        headers = [(f, self._header(f)) for f, _ in members]
        readable = [(f, h) for f, h in headers if not isinstance(h, str)]
        for source_file, header in headers:
            if isinstance(header, str):
                problems.append(f"{source_file.name}: {header}")

        title = next((h.run_title for _, h in readable if h.run_title), "") or ""
        start = next((h.start_time for _, h in readable if h.start_time), "") or ""
        planned = sorted(
            {h.n_segments for _, h in readable if h.n_segments is not None}
        )
        n_segments = planned[0] if len(planned) == 1 else None
        if len(planned) > 1:
            problems.append(
                "the headers disagree about how many segments were planned "
                f"({', '.join(map(str, planned))})."
            )
        if n_segments is not None and by_segment and max(by_segment) > n_segments:
            problems.append(
                f"segment {max(by_segment)} is present, but the header plans "
                f"only {n_segments}."
            )

        experiments = {
            normalize_ipts(h.experiment) for _, h in readable if h.experiment
        }
        experiments.discard(None)
        if self.ipts and experiments and experiments != {self.ipts}:
            problems.append(
                f"the file header names {', '.join(sorted(experiments))}, but "
                f"this project is {self.ipts}. It may belong to another "
                "experiment."
            )

        # One per file, None where unknown: positions must line up with files.
        thetas = tuple(
            None if isinstance(h, str) or f.segment is None else h.theta
            for f, h in headers
        )
        return SourceRun(
            key=RunKey(run),
            files=tuple(f for f, _ in members),
            title=clean_title_snapshot(title),
            start_time=clean_title_snapshot(start),
            experiment=next(iter(sorted(experiments)), None),
            thetas=thetas,
            n_segments=n_segments,
            changed_at=max(info.st_mtime for _, info in members),
            fingerprint=fingerprint_entries(
                [(f.name, info.st_size, info.st_mtime_ns) for f, info in members]
            ),
            problems=tuple(problems),
        )

    def _header(self, source_file: SourceFile) -> Any:
        """The file's header, or the reason it could not be read. Cached.

        Opened the way :meth:`read_bytes` opens files -- refusing a link, and
        checking it is still the file listed -- because a name that was a
        plain file at listing time can be a link to anything by now.
        """
        from nr_workbench.instrument.header import (
            MAX_HEADER_BYTES,
            HeaderError,
            read_header_bytes,
        )

        key = (source_file.name, source_file.version)
        with self._lock:
            if key in self._headers:
                self._headers.move_to_end(key)
                return self._headers[key]
        assert self.path is not None
        path = self.path / source_file.name
        try:
            data = self._open_listed(source_file, MAX_HEADER_BYTES)
            value: Any = read_header_bytes(data, path)
        except HeaderError as exc:
            value = str(exc).split(": ", 1)[-1]
        except SourceChangedError as exc:
            value = str(exc)
        except OSError as exc:
            value = f"header cannot be read ({exc})"
        with self._lock:
            self._headers[key] = value
            while len(self._headers) > HEADER_CACHE_SIZE:
                self._headers.popitem(last=False)
        return value

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def read_bytes(self, file: SourceFile, *, max_bytes: int) -> bytes:
        """The exact bytes of a listed file, if it is still the same file.

        Raises:
            SourceChangedError: The file was rewritten, replaced or moved since
                it was listed, or while it was being read.
            SourceFileTooLarge: It is bigger than ``max_bytes``.
            OSError: It cannot be opened -- including because it is now a
                symbolic link, which ``O_NOFOLLOW`` refuses with ``ELOOP``.
        """
        with self._opened(file) as (descriptor, before):
            if before.st_size > max_bytes:
                raise SourceFileTooLarge(
                    f"{file.name} is {before.st_size} bytes, more than a reduced "
                    f"file could be ({max_bytes})."
                )
            data = _read_up_to(descriptor, max_bytes + 1)
            after = os.fstat(descriptor)
        if _version(after) != file.version or len(data) != before.st_size:
            raise SourceChangedError(f"{file.name} changed while it was being read.")
        return data

    def _open_listed(self, file: SourceFile, limit: int) -> bytes:
        """The first *limit* bytes of a listed file, opened as safely as a copy."""
        with self._opened(file) as (descriptor, _):
            return _read_up_to(descriptor, limit)

    @contextlib.contextmanager
    def _opened(self, file: SourceFile) -> Iterator[tuple[int, os.stat_result]]:
        """Open a listed file without following links, if it is still that file.

        Raises:
            SourceChangedError: It is not a canonical name, not a regular file,
                or not the version listed.
            OSError: It cannot be opened -- including because it is now a
                symbolic link, which ``O_NOFOLLOW`` refuses with ``ELOOP``.
        """
        if self.path is None or canonical_name(file.name) is None:
            raise SourceChangedError(f"{file.name} is not a file this source listed")
        # O_NONBLOCK too: a name swapped for a FIFO would otherwise block the
        # open itself, forever, on a worker thread.
        flags = (
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        )
        descriptor = os.open(self.path / file.name, flags)
        try:
            info = os.fstat(descriptor)
            if not stat_module.S_ISREG(info.st_mode) or _version(info) != file.version:
                raise SourceChangedError(
                    f"{file.name} changed after it was listed; it is probably "
                    "being rewritten by the reduction."
                )
            yield (descriptor, info)
        finally:
            os.close(descriptor)


def _read_up_to(descriptor: int, limit: int) -> bytes:
    """Read at most *limit* bytes from an open descriptor."""
    chunks = []
    remaining = limit
    while remaining > 0:
        chunk = os.read(descriptor, min(65536, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _version(info: os.stat_result) -> str:
    """Identity of one version of a file: which file, how big, when written."""
    return f"{info.st_ino}:{info.st_size}:{info.st_mtime_ns}:{info.st_ctime_ns}"


def _source_file(
    name: str, info: os.stat_result, parsed: ReducedName | int
) -> SourceFile:
    if isinstance(parsed, ReducedName):
        return SourceFile(
            name=name,
            size=info.st_size,
            mtime=info.st_mtime,
            version=_version(info),
            segment=parsed.segment,
            subrun=parsed.subrun,
            dialect=parsed.dialect,
        )
    return SourceFile(
        name=name, size=info.st_size, mtime=info.st_mtime, version=_version(info)
    )
