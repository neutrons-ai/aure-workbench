"""Discover what data is actually on disk for a sample.

``sample.md`` records what the scientist meant to measure; this records what
arrived. Keeping them separate and reporting the difference is deliberate --
the prose is authoritative for intent, the register for files, and the gap
between them is usually the interesting part (a run that failed, a run nobody
wrote down, a number typed wrong).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: REF_L reduced-file conventions. Kept here rather than read from nrw.toml
#: because they are compiled patterns, not user preferences; nrw.toml records
#: them for humans and downstream tools.
COMBINED_RE = re.compile(r"^REFL_(?P<run>\d+)_combined_data_auto\.txt$")
PARTIAL_RE = re.compile(
    r"^REFL_(?P<run>\d+)_(?P<seg>\d+)_(?P<subrun>\d+)_partial\.txt$"
)
SLICE_RE = re.compile(r"^r(?P<run>\d+)_t(?P<t>\d+)\.txt$")
REDUCTION_RE = re.compile(r"^r?(?P<run>\d+)?_?.*reduction\.json$")

#: A run number mentioned in sample.md. Six digits is the REF_L range; the
#: bound stops it matching dates, concentrations, and stray integers.
RUN_IN_PROSE_RE = re.compile(r"\b(\d{6})\b")


@dataclass
class SteadyMeasurement:
    """A steady-state run found on disk.

    Attributes:
        run: Run number.
        combined: Path to the combined file, if present.
        partials: Segment index to path, for the per-angle files.
    """

    run: int
    combined: str | None = None
    partials: dict[int, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """Return the sample.yaml form."""
        entry: dict[str, Any] = {"run": self.run}
        if self.combined:
            entry["combined"] = self.combined
        if self.partials:
            entry["segments"] = [self.partials[k] for k in sorted(self.partials)]
        return entry


@dataclass
class SeriesMeasurement:
    """A time-resolved series found on disk.

    Attributes:
        run: Run number, when it can be determined.
        directory: Directory holding the slices, relative to the project root.
        n_slices: How many slices are present.
        kind: ``time_binned`` for r<run>_t<sec>.txt, ``labelled`` when a
            reduction JSON supplies interval labels.
        reduction_json: The sidecar, if present.
        t_start: First slice time in seconds, for a time-binned series.
        t_stop: Last slice time in seconds.
        t_step: Spacing in seconds, when it is uniform.
    """

    run: int | None
    directory: str
    n_slices: int
    kind: str
    reduction_json: str | None = None
    t_start: float | None = None
    t_stop: float | None = None
    t_step: float | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return the sample.yaml form."""
        entry: dict[str, Any] = {
            "run": self.run,
            "reduced_dir": self.directory,
            "n_slices": self.n_slices,
            "kind": self.kind,
        }
        if self.reduction_json:
            entry["reduction_json"] = self.reduction_json
        if self.t_step is not None:
            entry["t_start"] = self.t_start
            entry["t_stop"] = self.t_stop
            entry["t_step"] = self.t_step
        return entry


@dataclass
class ScanResult:
    """What a scan found, and how it compares with the prose.

    Attributes:
        sample: The sample identifier.
        steady: Steady-state runs on disk, by run number.
        series: Time-resolved series on disk.
        runs_in_prose: Run numbers mentioned in sample.md.
        unreadable: Files that look like data but could not be parsed.
    """

    sample: str
    steady: dict[int, SteadyMeasurement] = field(default_factory=dict)
    series: list[SeriesMeasurement] = field(default_factory=list)
    runs_in_prose: set[int] = field(default_factory=set)
    unreadable: list[str] = field(default_factory=list)

    @property
    def runs_on_disk(self) -> set[int]:
        """Every run number the scan found."""
        return set(self.steady) | {s.run for s in self.series if s.run is not None}

    @property
    def documented_but_absent(self) -> list[int]:
        """Runs mentioned in sample.md with no data on disk.

        Usually a run that failed, or one not copied across yet.
        """
        return sorted(self.runs_in_prose - self.runs_on_disk)

    @property
    def present_but_undocumented(self) -> list[int]:
        """Runs on disk that sample.md does not mention.

        Usually data nobody wrote up -- worth knowing before it is analysed.
        """
        return sorted(self.runs_on_disk - self.runs_in_prose)

    def as_dict(
        self, *, title: str = "", beamtime: str | None = None, created: str = ""
    ) -> dict[str, Any]:
        """Return the full sample.yaml document.

        Args:
            title: Human-readable sample title.
            beamtime: Beamtime label.
            created: ISO-8601 creation timestamp to preserve.

        Returns:
            The mapping to serialise.
        """
        document: dict[str, Any] = {
            "schema": "nrw-sample/1",
            "id": self.sample,
            "title": title or self.sample,
        }
        if beamtime:
            document["beamtime"] = beamtime
        if created:
            document["created"] = created
        document["steady"] = [self.steady[r].as_dict() for r in sorted(self.steady)]
        document["series"] = [s.as_dict() for s in self.series]
        return document


def scan_sample(root: Path, sample: str) -> ScanResult:
    """Inspect a sample's data directories and its prose.

    Args:
        root: Project root.
        sample: Sample identifier.

    Returns:
        What was found, including the prose/disk discrepancies.

    Raises:
        FileNotFoundError: If the sample directory does not exist.
    """
    sample_dir = Path(root) / "samples" / sample
    if not sample_dir.is_dir():
        raise FileNotFoundError(f"No sample {sample!r} at {sample_dir}")

    result = ScanResult(sample=sample)
    _scan_steady(sample_dir / "data" / "steady", root, result)
    _scan_series(sample_dir / "data" / "tnr", root, result)
    result.runs_in_prose = _runs_mentioned(sample_dir / "sample.md")
    return result


def _scan_steady(directory: Path, root: Path, result: ScanResult) -> None:
    """Collect steady-state runs from a directory."""
    if not directory.is_dir():
        return
    for path in sorted(directory.iterdir()):
        if not path.is_file() or path.suffix != ".txt":
            continue
        relative = path.relative_to(root).as_posix()

        combined = COMBINED_RE.match(path.name)
        if combined:
            run = int(combined.group("run"))
            result.steady.setdefault(run, SteadyMeasurement(run)).combined = relative
            continue

        partial = PARTIAL_RE.match(path.name)
        if partial:
            run = int(partial.group("run"))
            segment = int(partial.group("seg"))
            result.steady.setdefault(run, SteadyMeasurement(run)).partials[segment] = (
                relative
            )
            continue

        result.unreadable.append(relative)


def _scan_series(directory: Path, root: Path, result: ScanResult) -> None:
    """Collect time-resolved series, one per subdirectory."""
    if not directory.is_dir():
        return

    candidates = [d for d in sorted(directory.iterdir()) if d.is_dir()]
    # A flat tnr/ holding slices directly is also valid.
    if not candidates and any(directory.glob("*.txt")):
        candidates = [directory]

    for candidate in candidates:
        found = _describe_series(candidate, root)
        if found is not None:
            result.series.append(found)


def _describe_series(directory: Path, root: Path) -> SeriesMeasurement | None:
    """Characterise one series directory."""
    slices: dict[int, Path] = {}
    labelled = 0
    runs: set[int] = set()

    for path in sorted(directory.iterdir()):
        if not path.is_file() or path.suffix != ".txt":
            continue
        match = SLICE_RE.match(path.name)
        if match:
            slices[int(match.group("t"))] = path
            runs.add(int(match.group("run")))
        elif path.name.startswith("r") and "_" in path.name:
            labelled += 1
            leading = re.match(r"^r(\d+)_", path.name)
            if leading:
                runs.add(int(leading.group(1)))

    reduction = next((p for p in sorted(directory.glob("*reduction.json"))), None)
    total = len(slices) + labelled
    if total < 2:
        return None

    relative_dir = directory.relative_to(root).as_posix()
    run = next(iter(runs)) if len(runs) == 1 else None

    if slices:
        times = sorted(slices)
        spacings = {b - a for a, b in zip(times, times[1:], strict=False)}
        uniform = spacings.pop() if len(spacings) == 1 else None
        return SeriesMeasurement(
            run=run,
            directory=relative_dir,
            n_slices=len(slices),
            kind="time_binned",
            reduction_json=reduction.relative_to(root).as_posix()
            if reduction
            else None,
            t_start=float(times[0]),
            t_stop=float(times[-1]),
            t_step=float(uniform) if uniform else None,
        )

    return SeriesMeasurement(
        run=run,
        directory=relative_dir,
        n_slices=labelled,
        kind="labelled",
        reduction_json=reduction.relative_to(root).as_posix() if reduction else None,
    )


def _runs_mentioned(sample_md: Path) -> set[int]:
    """Extract run numbers from the prose, ignoring commented-out examples.

    The template ships a worked example inside an HTML comment; counting those
    would report every new sample as documenting runs it does not have.
    """
    if not sample_md.is_file():
        return set()
    text = sample_md.read_text(encoding="utf-8")
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    return {int(m) for m in RUN_IN_PROSE_RE.findall(text)}
