"""Bringing an existing beamtime directory into the workbench layout.

The four beamtimes in ``experiments-2025`` use four mutually incompatible
layouts. That is the problem this package exists to end, but ending it must not
require anyone to reorganise a live directory by hand -- migration that costs
more than the mess is migration nobody does.

So this plans a mapping and, by default, only prints it. Three rules make the
result safe to run against a directory someone is working in:

* **Data is symlinked, never copied or moved.** Reduced files are the
  authoritative record and often large. The original tree keeps working exactly
  as it did, and there is one copy of every byte.
* **Prose and scripts are copied.** They are small, and the point of importing
  them is that you will edit them here.
* **Nothing is ever overwritten.** A destination that already exists is
  reported and skipped.

What cannot be classified is listed rather than guessed at. A file in the wrong
place is worse than a file left alone, because the first looks like it was
understood.
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from nr_workbench.project.scan import COMBINED_RE, PARTIAL_RE, SLICE_RE

#: A per-sample directory, e.g. ``Sample4``.
SAMPLE_DIR_RE = re.compile(r"^Sample[_-]?(\w+)$", re.IGNORECASE)

#: Directory names that hold reduced steady-state data, lowercased.
STEADY_DIRS = {"steady", "rawdata", "reduced", "data"}

#: Directory names that hold time-resolved data, lowercased.
TNR_DIRS = {"tnr", "t_nr", "time-resolved"}

#: Directory names that hold fitting scripts, lowercased.
MODEL_DIRS = {"models", "model"}

#: Prose files worth carrying across, lowercased.
PROSE_NAMES = {"measurements.md", "notes.md", "readme.md", "sample.md"}

#: Outputs of a previous fit, matched by suffix. These are deliberately left
#: behind, and the distinction from "not recognised" matters.
#:
#: A ``results/<fit_id>/`` directory here means something specific: a manifest
#: recording the hash of every input, the resolved environment, the git SHA,
#: and the exact command. A ``.dat`` dumped by a bumps run in 2025 has none of
#: that. Importing it would put an artifact where `nrw whence` promises to be
#: able to answer, and the honest answer for these is that nobody recorded it.
#:
#: Re-run the script through `nrw fit run` and the result arrives with real
#: provenance. That is the migration path, and it takes minutes.
DERIVED_SUFFIXES = {
    ".dat",
    ".err",
    ".mc",
    ".gz",
    ".png",
    ".svg",
    ".pdf",
    ".par",
    ".parquet",
}

#: Directory names whose contents are derived or transient, lowercased.
DERIVED_DIRS = {
    "results",
    "refl1d_output",
    "reports",
    "notebooks",
    ".ipynb_checkpoints",
    "ai-ready-data",
    "workflow",
}


class Action(StrEnum):
    """What the importer intends to do with one file."""

    LINK = "link"
    COPY = "copy"
    SKIP = "skip"


@dataclass
class PlannedImport:
    """One file's intended destination.

    Attributes:
        source: Absolute path in the original tree.
        destination: Path relative to the project root.
        action: Whether to symlink, copy, or skip.
        sample: The sample this belongs to.
        reason: Why, when the action is ``skip``.
    """

    source: Path
    destination: str
    action: Action
    sample: str
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        """Return the JSON form."""
        payload = {
            "source": str(self.source),
            "destination": self.destination,
            "action": str(self.action),
            "sample": self.sample,
        }
        if self.reason:
            payload["reason"] = self.reason
        return payload


@dataclass
class ImportPlan:
    """Everything an import would do.

    Attributes:
        source_root: The beamtime directory being read.
        layout: Which shape was detected.
        planned: Files with a destination.
        derived: Outputs of previous fits, deliberately left behind.
        unclassified: Files that were genuinely not recognised.
        samples: Sample identifiers the plan touches.
    """

    source_root: Path
    layout: str
    planned: list[PlannedImport] = field(default_factory=list)
    derived: list[str] = field(default_factory=list)
    unclassified: list[str] = field(default_factory=list)
    samples: set[str] = field(default_factory=set)

    @property
    def links(self) -> int:
        """How many files would be symlinked."""
        return sum(1 for p in self.planned if p.action is Action.LINK)

    @property
    def copies(self) -> int:
        """How many files would be copied."""
        return sum(1 for p in self.planned if p.action is Action.COPY)

    @property
    def skips(self) -> int:
        """How many files would be skipped."""
        return sum(1 for p in self.planned if p.action is Action.SKIP)

    def as_dict(self) -> dict[str, Any]:
        """Return the JSON form."""
        return {
            "schema": "nrw-import/1",
            "source": str(self.source_root),
            "layout": self.layout,
            "samples": sorted(self.samples),
            "counts": {
                "link": self.links,
                "copy": self.copies,
                "skip": self.skips,
                "derived": len(self.derived),
                "unclassified": len(self.unclassified),
            },
            "planned": [p.as_dict() for p in self.planned],
            "derived": self.derived,
            "unclassified": self.unclassified,
        }


def detect_layout(source: Path) -> str:
    """Name the shape of a beamtime directory.

    Args:
        source: The directory to inspect.

    Returns:
        ``per-sample`` when samples own their data, ``experiment-level`` when
        the beamtime does, ``mixed`` when both are present, ``unknown``
        otherwise.
    """
    source = Path(source)
    has_samples = any(
        entry.is_dir() and SAMPLE_DIR_RE.match(entry.name) for entry in source.iterdir()
    )
    has_shared = any(
        (source / name).is_dir()
        for name in ("data", "models", "results", "Results", "tNR")
    )
    if has_samples and has_shared:
        return "mixed"
    if has_samples:
        return "per-sample"
    if has_shared:
        return "experiment-level"
    return "unknown"


def plan_import(
    source: Path, *, default_sample: str = "Sample1", root: Path | None = None
) -> ImportPlan:
    """Work out where every file in a beamtime directory should go.

    Args:
        source: The beamtime directory to import.
        default_sample: Sample to assign files that name no sample of their own.
        root: Project root, used to detect destinations that already exist.

    Returns:
        The plan. Nothing is written.

    Raises:
        FileNotFoundError: If the source directory does not exist.
    """
    source = Path(source).resolve()
    if not source.is_dir():
        raise FileNotFoundError(f"No such directory: {source}")

    plan = ImportPlan(source_root=source, layout=detect_layout(source))

    for path in sorted(source.rglob("*")):
        if not path.is_file() or _ignored(path):
            continue
        sample = _sample_for(path, source, default_sample)
        entry = _classify(path, sample)
        if entry is None:
            relative = str(path.relative_to(source))
            if _is_derived(path):
                plan.derived.append(relative)
            else:
                plan.unclassified.append(relative)
            continue
        if root is not None and (Path(root) / entry.destination).exists():
            entry.action = Action.SKIP
            entry.reason = "destination already exists"
        plan.planned.append(entry)
        plan.samples.add(sample)

    return plan


def apply_import(plan: ImportPlan, root: Path) -> list[str]:
    """Execute a plan.

    Args:
        plan: The plan to apply.
        root: Project root to write into.

    Returns:
        Destinations that were created, relative to the root.

    Raises:
        OSError: If a link or copy fails for a reason other than an existing
            destination, which is skipped rather than raised.
    """
    root = Path(root).resolve()
    written: list[str] = []

    for entry in plan.planned:
        if entry.action is Action.SKIP:
            continue
        target = root / entry.destination
        if target.exists() or target.is_symlink():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)

        if entry.action is Action.LINK:
            target.symlink_to(_link_target(entry.source, target, root))
        else:
            shutil.copy2(entry.source, target)
        written.append(entry.destination)

    return written


def _link_target(source: Path, target: Path, root: Path) -> str:
    """Choose a relative or absolute symlink target.

    Relative only when the source sits under the project root or alongside it,
    where the two are plausibly moved together and a relative link survives.
    When the source is in another repository entirely, the relative path is a
    dozen ``..`` segments that gain nothing and are unreadable in ``ls -l``, and
    an absolute link says what it means.
    """
    # Both sides must be resolved, or a symlinked component on one of them
    # alone defeats the comparison: on macOS `/tmp` resolves to `/private/tmp`
    # and `/home` through autofs, so a source genuinely inside the project
    # would be judged foreign and silently linked absolutely.
    source = Path(source).resolve()
    neighbourhood = Path(root).resolve().parent
    try:
        source.relative_to(neighbourhood)
    except ValueError:
        return str(source)
    return os.path.relpath(source, Path(target).resolve().parent)


def _is_derived(path: Path) -> bool:
    """Whether a file is the output of a previous fit or pipeline run.

    Checked only after classification, so a reduced ``.txt`` sitting inside a
    ``results/`` directory is still imported as data -- the suffix and the
    directory both have to point the same way before something is written off.
    """
    lowered = {part.lower() for part in path.parts}
    if lowered & DERIVED_DIRS:
        return True
    return path.suffix.lower() in DERIVED_SUFFIXES


def _ignored(path: Path) -> bool:
    """Whether a path is machine state rather than someone's work."""
    parts = set(path.parts)
    if parts & {".git", "__pycache__", ".ipynb_checkpoints", ".nrw", ".venv"}:
        return True
    return path.suffix in {".pyc", ".pyo", ".DS_Store"} or path.name == ".DS_Store"


def _sample_for(path: Path, source: Path, default: str) -> str:
    """Decide which sample a file belongs to.

    A ``Sample<N>`` component anywhere in the path wins; a run number does not
    imply a sample, so anything else goes to the default and the human sorts it
    out. Guessing sample membership from run numbers is exactly the kind of
    inference that puts a file in the wrong place.
    """
    for part in path.relative_to(source).parts:
        match = SAMPLE_DIR_RE.match(part)
        if match:
            return part
    return default


def _classify(path: Path, sample: str) -> PlannedImport | None:
    """Decide a file's destination, or ``None`` if it is not recognised."""
    name = path.name
    lowered = {part.lower() for part in path.parts}
    base = f"samples/{sample}"

    # Reduced steady-state data, by filename rather than by directory: the
    # conventions are reliable and the directory names are not.
    if COMBINED_RE.match(name) or PARTIAL_RE.match(name):
        return PlannedImport(path, f"{base}/data/steady/{name}", Action.LINK, sample)

    # Time-resolved slices and their sidecar.
    if SLICE_RE.match(name) or name.endswith("_eis_reduction.json"):
        run = _run_from(name)
        folder = f"{base}/data/tnr/{run}" if run else f"{base}/data/tnr"
        return PlannedImport(path, f"{folder}/{name}", Action.LINK, sample)
    if lowered & TNR_DIRS and path.suffix == ".txt":
        run = _run_from(name)
        folder = f"{base}/data/tnr/{run}" if run else f"{base}/data/tnr"
        return PlannedImport(path, f"{folder}/{name}", Action.LINK, sample)

    # Raw NeXus stays linked and out of git.
    if name.endswith((".nxs.h5", ".nxs")):
        return PlannedImport(path, f"{base}/data/raw/{name}", Action.LINK, sample)

    # Fitting scripts and specs are copied: you will edit them here.
    if path.suffix == ".py" and (lowered & MODEL_DIRS or _looks_like_a_model(path)):
        return PlannedImport(path, f"{base}/models/{name}", Action.COPY, sample)
    if path.suffix in {".yaml", ".yml"} and (
        name.startswith("model") or _looks_like_a_spec(path)
    ):
        return PlannedImport(path, f"{base}/models/{name}", Action.COPY, sample)

    # Prose. measurements.md becomes sample.md only if that slot is free; the
    # scaffold writes one, so it usually is not, and clobbering the template a
    # scientist has started filling in would be worse than an extra file.
    if name.lower() in PROSE_NAMES:
        target = "sample.md" if name.lower() == "measurements.md" else name
        return PlannedImport(path, f"{base}/{target}", Action.COPY, sample)

    # A reduction template records which direct beam normalised each segment.
    if name.endswith("_auto_template.xml"):
        return PlannedImport(path, f"{base}/data/steady/{name}", Action.LINK, sample)

    return None


#: Bytes of a .py file to read when deciding whether it is a fit script.
#: Generous, because these are a few hundred lines at most and the deciding
#: token is at the very end.
_MODEL_SNIFF_BYTES = 200_000


def _looks_like_a_model(path: Path) -> bool:
    """Whether a stray .py file is a refl1d fitting script.

    Content, not location: in two of the four layouts the scripts sit loose in
    the sample directory rather than under ``models/``.

    The **whole** file is read, not the head. A refl1d script conventionally
    ends with ``problem = FitProblem(...)`` -- in the oct2025 reference that is
    line 344 of 344 -- and it imports via ``from refl1d.names import *``, so
    the name never appears near the top. Sniffing the first few kilobytes finds
    the imports and misses the thing that identifies the file.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")[:_MODEL_SNIFF_BYTES]
    except OSError:
        return False
    return "FitProblem" in text and ("refl1d" in text or "bumps" in text)


def _looks_like_a_spec(path: Path) -> bool:
    """Whether a YAML file is an nrw-model spec or a refl1d-shaped model."""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")[:8000]
    except OSError:
        return False
    if "nrw-model/" in text:
        return True
    # A pre-nrw hand-written model description: names a stack and materials.
    return "stack:" in text and ("materials:" in text or "layers:" in text)


def _run_from(name: str) -> str | None:
    """Extract a six-digit run number from a filename.

    The leading ``r`` of ``r223995_eis_reduction.json`` is a word character, so
    there is no word boundary before the digits and a ``\\b``-anchored pattern
    silently matches nothing -- which sent every tNR file to a flat directory
    instead of one per run. An explicit optional ``r`` prefix, and a boundary
    only where one can exist.
    """
    match = re.search(r"(?:^|[^0-9])r?(\d{6})(?![0-9])", name)
    return match.group(1) if match else None
