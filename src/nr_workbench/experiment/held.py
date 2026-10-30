"""The runs the project's samples already hold, in their own ``data/`` folders.

The Experiment page lists what the data source holds and what the catalog
records. Data copied straight into ``samples/<id>/data/`` is neither, yet it is
the experiment's data, and what a fit of that sample reads. This module lists
it, from the same scan ``nrw sample scan`` makes, so each run can be shown and
organized like a run from the data folder.

Rebuilt on every request rather than stored, as the source's inventory is: a
stored copy would be wrong as soon as a file was copied in by hand.
"""

from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path

from nr_workbench.experiment.model import (
    SERIES,
    STEADY,
    RunKey,
    clean_title_snapshot,
)
from nr_workbench.instrument.header import HeaderError, ReducedHeader, read_header
from nr_workbench.project.layout import ProjectLayout
from nr_workbench.project.scan import scan_sample

#: How many file headers to remember. A beamtime's samples hold a few hundred
#: reduced files; this keeps all of them without growing forever.
HEADER_CACHE_SIZE = 8192


@dataclass(frozen=True)
class HeldRun:
    """One measurement a sample holds in its ``data/`` folder.

    Attributes:
        key: Which run: a ``steady`` key for angle segments or a combined
            curve in ``data/steady/``, a ``series`` key for slices in
            ``data/tnr/``.
        sample_ids: Every sample that holds it, sorted. The files and angles
            below are the first one's.
        files: Its files, relative to the project root: segments in order, then
            any combined curve; for a series, its folder.
        segments: The segment numbers present, in order.
        thetas: Incident angle in degrees for each entry of ``files``, ``None``
            where the header does not give one or for a combined curve.
        title: The run title from the first header that has one, or ``""``.
        n_slices: How many slices a series has; ``0`` for a steady run.
        reduction_json: A series' reduction sidecar, relative to the root.
    """

    key: RunKey
    sample_ids: tuple[str, ...]
    files: tuple[str, ...]
    segments: tuple[int, ...] = ()
    thetas: tuple[float | None, ...] = ()
    title: str = ""
    n_slices: int = 0
    reduction_json: str | None = None

    @property
    def folder(self) -> str:
        """Where its files are, relative to the project root, for the page."""
        if self.key.kind == SERIES:
            return self.files[0] + "/"
        return Path(self.files[0]).parent.as_posix() + "/"


class HeaderCache:
    """File headers read once per version of a file.

    Keyed by path, size and modification time, so a poll rereads only a file
    that changed. Safe to share between request threads.
    """

    def __init__(self, size: int = HEADER_CACHE_SIZE) -> None:
        self._size = size
        self._headers: OrderedDict[tuple[str, int, int], ReducedHeader | None] = (
            OrderedDict()
        )
        self._lock = threading.Lock()

    def get(self, path: Path) -> ReducedHeader | None:
        """The file's header, or ``None`` when it cannot be read."""
        try:
            info = path.stat()
        except OSError:
            return None
        key = (str(path), info.st_size, info.st_mtime_ns)
        with self._lock:
            if key in self._headers:
                self._headers.move_to_end(key)
                return self._headers[key]
        try:
            header: ReducedHeader | None = read_header(path, dq=False)
        except (HeaderError, OSError, UnicodeDecodeError, ValueError):
            header = None
        with self._lock:
            self._headers[key] = header
            while len(self._headers) > self._size:
                self._headers.popitem(last=False)
        return header


def held_runs(root: Path, *, headers: HeaderCache) -> dict[RunKey, HeldRun]:
    """Every run the project's samples hold, by key.

    A series whose run number cannot be read from its slice names has no key,
    and is shown on its sample's page only.

    Args:
        root: Project root.
        headers: Where headers already read are kept.

    Returns:
        The held runs, by key.
    """
    root = Path(root)
    held: dict[RunKey, HeldRun] = {}
    for sample_id in ProjectLayout(root=root).list_samples():
        try:
            scan = scan_sample(root, sample_id)
        except FileNotFoundError:
            continue
        for run, measurement in sorted(scan.steady.items()):
            key = RunKey(run, STEADY)
            if key in held:
                held[key] = _also_in(held[key], sample_id)
                continue
            segments = tuple(sorted(measurement.partials))
            files = [measurement.partials[s] for s in segments]
            if measurement.combined:
                files.append(measurement.combined)
            read = [headers.get(root / f) for f in files]
            held[key] = HeldRun(
                key=key,
                sample_ids=(sample_id,),
                files=tuple(files),
                segments=segments,
                thetas=tuple(
                    h.theta if h is not None and i < len(segments) else None
                    for i, h in enumerate(read)
                ),
                title=clean_title_snapshot(
                    next((h.run_title for h in read if h and h.run_title), "")
                ),
            )
        for series in scan.series:
            if series.run is None:
                continue
            key = RunKey(series.run, SERIES)
            if key in held:
                held[key] = _also_in(held[key], sample_id)
                continue
            held[key] = HeldRun(
                key=key,
                sample_ids=(sample_id,),
                files=(series.directory,),
                n_slices=series.n_slices,
                reduction_json=series.reduction_json,
            )
    return held


def held_version(held: Mapping[RunKey, HeldRun]) -> str:
    """A digest of what the samples hold, which changes when any run does.

    The page compares it between polls and reloads when it differs, as it does
    for the catalog's version.
    """
    digest = hashlib.sha256()
    for key in sorted(held):
        digest.update(repr(held[key]).encode())
    return digest.hexdigest()[:16]


def _also_in(run: HeldRun, sample_id: str) -> HeldRun:
    """The same run, held by one more sample."""
    return replace(run, sample_ids=tuple(sorted({*run.sample_ids, sample_id})))
