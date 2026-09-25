"""What the data source and the run feed report: the runs that exist.

Kept apart from :mod:`~nr_workbench.experiment.model`, which is what a person
*decided* about the runs. This module is what the instrument side says, and it
is rebuilt on every poll rather than stored: persisting it would make a second
record of which files exist, and it would be wrong the moment the reduction
re-ran.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from nr_workbench.experiment.model import RunKey
from nr_workbench.problems import Problem


@dataclass(frozen=True)
class SourceFile:
    """One reduced file, as the source lists it.

    Attributes:
        name: The file's name, already checked to be exactly canonical -- it
            is the name the copy will have.
        size: Size in bytes.
        mtime: Modification time, seconds since the epoch, by the *file
            server's* clock.
        version: Opaque identity of these exact bytes. Changes whenever the
            reduction rewrites the file, which is how "the source changed since
            it was copied" is detected.
        segment: 1-based angle segment, or ``None`` for a combined curve.
        subrun: The run that produced this segment, or ``None``.
        dialect: ``partial`` or ``autoreduction``, or ``None`` for a combined
            curve.
    """

    name: str
    size: int
    mtime: float
    version: str
    segment: int | None = None
    subrun: int | None = None
    dialect: str | None = None


@dataclass(frozen=True)
class SourceRun:
    """One run, as the source's files describe it.

    Attributes:
        key: Which run.
        files: Its files, segments in order, then any combined curve.
        title: The run title from the header, or ``""``.
        start_time: The start time as the header recorded it, or ``""`` --
            the ``new_reduction`` header does not carry one, and none is
            invented.
        experiment: The IPTS the header names, or ``None``.
        thetas: Incident angle in degrees for each entry of ``files``, in the
            same order, ``None`` where it is not known -- a combined curve, or
            a header that could not be read. Aligned by position so that a
            missing one cannot shift the rest onto the wrong segment.
        n_segments: How many segments the header says were planned, or
            ``None`` when it does not say.
        changed_at: When any of its files last changed (file server clock).
        fingerprint: Digest of the file listing, to notice a rewrite between
            polls even on a filesystem with coarse timestamps.
        problems: Why these files should not be trusted as one measurement.
            Non-empty means the run is quarantined.
    """

    key: RunKey
    files: tuple[SourceFile, ...]
    title: str = ""
    start_time: str = ""
    experiment: str | None = None
    thetas: tuple[float | None, ...] = ()
    n_segments: int | None = None
    changed_at: float | None = None
    fingerprint: str = ""
    problems: tuple[str, ...] = ()

    @property
    def segments(self) -> tuple[int, ...]:
        """The segment numbers present, in order."""
        return tuple(f.segment for f in self.files if f.segment is not None)

    @property
    def last_subrun(self) -> int:
        """The highest run number any of its files came from."""
        return max([self.key.run, *(f.subrun for f in self.files if f.subrun)])


@dataclass(frozen=True)
class Inventory:
    """Everything the source could see on one poll.

    Attributes:
        runs: Runs by key.
        unrecognized: Names of files that look like data (``.txt``/``.dat``)
            but match no known reduced-file name. Reported, never skipped in
            silence: a directory of valid files in a new dialect once looked
            exactly like an empty one.
        other_files: How many other files (plots, JSON, XML) were ignored.
        reachable: Whether the location could be listed at all.
        problems: What went wrong, for the page.
    """

    runs: Mapping[RunKey, SourceRun] = field(default_factory=dict)
    unrecognized: tuple[str, ...] = ()
    other_files: int = 0
    reachable: bool = True
    problems: tuple[Problem, ...] = ()


@dataclass(frozen=True)
class Announcement:
    """A feed's statement that a run exists.

    Attributes:
        run: The run number.
        title: The title the feed reports, if any.
        start_time: The start time the feed reports, verbatim, if any.
        state: What the feed says about it (e.g. ``acquiring``), if anything.
    """

    run: int
    title: str = ""
    start_time: str = ""
    state: str = ""


@dataclass(frozen=True)
class FeedUpdate:
    """What a feed knows, on one poll.

    Attributes:
        announcements: Every run the feed currently knows about. A snapshot,
            not a delta: :class:`~nr_workbench.experiment.live.LiveInventory`
            works out what changed, so a feed never has to remember what it
            already said.
        problems: What went wrong, for the page.
    """

    announcements: tuple[Announcement, ...] = ()
    problems: tuple[Problem, ...] = ()
