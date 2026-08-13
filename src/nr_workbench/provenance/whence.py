"""Answer "what produced this?" for any path in a project.

This is the question the whole package exists to answer. Resolution is tried in
order, cheapest and most certain first:

1. **Inside a fit directory** -- walk up to the nearest ``manifest.json``.
2. **A stamped figure** -- read the ``nrw-fit:<id>`` marker embedded at write
   time, which survives the file being copied out of the project entirely.
3. **A recorded input** -- if the path is a data file, report every fit that
   consumed it. ("This file changed. What do I need to redo?")
4. **A report figure** -- a plot or table built from several fits by a script
   under ``reports/``, which belongs to all of them and to no single one.
5. **Content match** -- hash it and look for a fit that produced identical
   bytes, which catches a figure that was copied and renamed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from nr_workbench.provenance.hashing import sha256_file
from nr_workbench.provenance.index import FitIndex
from nr_workbench.provenance.record import FitDirectory
from nr_workbench.provenance.stamp import read_stamp


class Resolution(StrEnum):
    """How a path was traced back to a fit."""

    FIT_DIRECTORY = "fit-directory"
    STAMP = "stamp"
    RECORDED_INPUT = "recorded-input"
    REPORT_FIGURE = "report-figure"
    CONTENT_MATCH = "content-match"
    UNKNOWN = "unknown"


class Freshness(StrEnum):
    """Whether a fit still corresponds to what is on disk."""

    FRESH = "fresh"
    STALE = "stale"
    BROKEN = "broken"
    UNKNOWN = "unknown"


@dataclass
class InputStatus:
    """One recorded input, re-checked against the current file.

    Attributes:
        role: What the file was to the fit.
        path: Path relative to the project root.
        recorded_sha256: The digest recorded when the fit ran.
        current_sha256: The digest now, or ``None`` if the file is gone.
        state: ``fresh``, ``stale``, or ``broken``.
    """

    role: str
    path: str
    recorded_sha256: str
    current_sha256: str | None
    state: Freshness

    @property
    def changed(self) -> bool:
        """Whether the file differs from what the fit consumed."""
        return self.state is not Freshness.FRESH


@dataclass
class WhenceResult:
    """What produced a path, and whether it still holds.

    Attributes:
        query: The path that was asked about.
        resolution: How the answer was found.
        fit_id: The fit that produced it, if one was identified.
        manifest: The fit's full manifest.
        record: The fit's index entry.
        consumed_by: For a data file, the fits that used it.
        inputs: Recorded inputs re-checked against disk.
        freshness: Overall freshness of the identified fit.
        promotion: The promotion currently pointing at this fit, if any.
        note: A human-readable remark about the resolution.
    """

    query: Path
    resolution: Resolution
    fit_id: str | None = None
    manifest: dict[str, Any] | None = None
    record: dict[str, Any] | None = None
    consumed_by: list[dict[str, Any]] = field(default_factory=list)
    inputs: list[InputStatus] = field(default_factory=list)
    freshness: Freshness = Freshness.UNKNOWN
    promotion: dict[str, Any] | None = None
    note: str | None = None

    @property
    def found(self) -> bool:
        """Whether anything was identified."""
        return self.resolution is not Resolution.UNKNOWN

    @property
    def stale_inputs(self) -> list[InputStatus]:
        """Recorded inputs that no longer match what is on disk."""
        return [i for i in self.inputs if i.changed]


def find_fit_directory(path: Path) -> Path | None:
    """Walk up from ``path`` to the nearest directory holding a manifest.

    Args:
        path: A file or directory inside a project.

    Returns:
        The fit directory, or ``None`` if the path is not inside one.
    """
    current = Path(path).resolve()
    if current.is_file():
        current = current.parent
    for candidate in [current, *current.parents]:
        if (candidate / "manifest.json").is_file():
            return candidate
        # Do not climb out past the project root.
        if (candidate / "nrw.toml").is_file():
            return None
    return None


def check_inputs(fit_dir: Path, root: Path) -> tuple[list[InputStatus], Freshness]:
    """Re-hash a fit's recorded inputs and report whether they still match.

    Hashes directly rather than through the cache: this is the check that
    decides whether a result can be trusted, so a stale cache entry must not be
    able to answer it.

    Args:
        fit_dir: The fit directory.
        root: Project root, for resolving relative input paths.

    Returns:
        Per-input status and the overall freshness.
    """
    entries = FitDirectory(fit_dir).read_inputs()
    if not entries:
        return [], Freshness.UNKNOWN

    statuses: list[InputStatus] = []
    worst = Freshness.FRESH

    for entry in entries:
        relative = str(entry.get("path", ""))
        recorded = str(entry.get("sha256", ""))
        target = (
            (Path(root) / relative)
            if not Path(relative).is_absolute()
            else Path(relative)
        )

        if not target.is_file():
            statuses.append(
                InputStatus(
                    role=str(entry.get("role", "")),
                    path=relative,
                    recorded_sha256=recorded,
                    current_sha256=None,
                    state=Freshness.BROKEN,
                )
            )
            worst = Freshness.BROKEN
            continue

        current = sha256_file(target)
        state = Freshness.FRESH if current == recorded else Freshness.STALE
        if state is Freshness.STALE and worst is not Freshness.BROKEN:
            worst = Freshness.STALE
        statuses.append(
            InputStatus(
                role=str(entry.get("role", "")),
                path=relative,
                recorded_sha256=recorded,
                current_sha256=current,
                state=state,
            )
        )

    return statuses, worst


def whence(path: Path, root: Path, index: FitIndex) -> WhenceResult:
    """Trace a path back to the fit that produced or consumed it.

    Args:
        path: The file to ask about.
        root: Project root.
        index: The project's fit index.

    Returns:
        What was found, with freshness re-checked when a fit was identified.
    """
    query = Path(path)
    root = Path(root).resolve()

    fit_dir = find_fit_directory(query)
    if fit_dir is not None:
        return _from_fit_dir(query, fit_dir, root, index, Resolution.FIT_DIRECTORY)

    if query.is_file():
        stamped = read_stamp(query)
        if stamped:
            resolved = _fit_dir_for(stamped, root, index)
            if resolved is not None:
                return _from_fit_dir(query, resolved, root, index, Resolution.STAMP)
            return WhenceResult(
                query=query,
                resolution=Resolution.STAMP,
                fit_id=stamped,
                note=(
                    f"The file is stamped with fit {stamped}, but no such fit "
                    "exists in this project. It was probably produced elsewhere."
                ),
            )

    consumers = _fits_consuming(query, root, index)
    if consumers:
        return WhenceResult(
            query=query,
            resolution=Resolution.RECORDED_INPUT,
            consumed_by=consumers,
            note=f"{len(consumers)} fit(s) used this file as an input.",
        )

    figure = _report_figure_for(query, root)
    if figure is not None:
        fits = [str(f) for f in figure.get("fits", [])]
        stale = _figure_is_stale(query, root, figure)
        note = (
            f"Built by {figure.get('script')} from {len(fits)} fit(s): "
            f"{', '.join(fits) or 'none named'}."
        )
        if stale:
            note += (
                " The file has changed since that script last ran, so it is no "
                "longer the output recorded here -- re-run `nrw report figure`."
            )
        return WhenceResult(
            query=query,
            resolution=Resolution.REPORT_FIGURE,
            # A figure drawn from four fits belongs to all four; naming one as
            # `fit_id` would be a citation the script never made.
            consumed_by=[{"fit_id": fit} for fit in fits],
            freshness=Freshness.STALE if stale else Freshness.FRESH,
            note=note,
        )

    if query.is_file():
        match = _fit_producing_identical_bytes(query, root, index)
        if match is not None:
            fit_dir = _fit_dir_for(match, root, index)
            if fit_dir is not None:
                result = _from_fit_dir(
                    query, fit_dir, root, index, Resolution.CONTENT_MATCH
                )
                result.note = (
                    "Matched by content, not location -- this file has the same "
                    "bytes as an artifact of that fit."
                )
                return result

    return WhenceResult(query=query, resolution=Resolution.UNKNOWN)


def _report_figure_for(path: Path, root: Path) -> dict[str, Any] | None:
    """The report-figure manifest claiming this path, if any.

    Scans the manifests in the enclosing ``reports/`` directory rather than a
    project-wide index: figure manifests live beside the scripts that wrote
    them, so a report directory carries its own provenance and survives being
    copied out with the sample.
    """
    import json

    from nr_workbench.commands.report import FIGURE_MANIFEST_SUFFIX

    try:
        resolved = path.resolve()
        relative = resolved.relative_to(Path(root).resolve()).as_posix()
    except (OSError, ValueError):
        return None

    for parent in resolved.parents:
        if parent.name != "reports":
            continue
        for manifest_path in sorted(parent.glob(f"*{FIGURE_MANIFEST_SUFFIX}")):
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            outputs = manifest.get("outputs")
            if not isinstance(outputs, list):
                continue
            for output in outputs:
                if isinstance(output, dict) and output.get("path") == relative:
                    return manifest
        break
    return None


def _figure_is_stale(path: Path, root: Path, manifest: dict[str, Any]) -> bool:
    """Whether a figure differs from the bytes its script last produced."""
    del root
    try:
        current = sha256_file(path)
    except OSError:
        return True
    for output in manifest.get("outputs", []):
        if (
            isinstance(output, dict)
            and output.get("sha256")
            and path.name.endswith(Path(str(output.get("path"))).name)
        ):
            return str(output["sha256"]) != current
    return False


def _from_fit_dir(
    query: Path,
    fit_dir: Path,
    root: Path,
    index: FitIndex,
    resolution: Resolution,
) -> WhenceResult:
    """Build a result from an identified fit directory."""
    directory = FitDirectory(fit_dir)
    try:
        manifest = directory.read_manifest()
    except (FileNotFoundError, ValueError):
        return WhenceResult(query=query, resolution=Resolution.UNKNOWN)

    provenance = manifest.get("provenance", {})
    fit_id = provenance.get("fit_id")
    inputs, freshness = check_inputs(fit_dir, root)

    return WhenceResult(
        query=query,
        resolution=resolution,
        fit_id=fit_id,
        manifest=manifest,
        record=index.find(fit_id) if fit_id else None,
        inputs=inputs,
        freshness=freshness,
        promotion=_promotion_for(fit_id, index) if fit_id else None,
    )


def _fit_dir_for(fit_id: str, root: Path, index: FitIndex) -> Path | None:
    """Locate a fit directory by identifier."""
    entry = index.find(fit_id)
    sample = entry.get("sample") if entry else None
    candidates = []
    if sample:
        candidates.append(root / "samples" / sample / "results" / fit_id)
    candidates.append(root / "results" / fit_id)

    for candidate in candidates:
        if (candidate / "manifest.json").is_file():
            return candidate

    # Fall back to a search: a project may have been reorganised.
    for manifest in root.glob(f"**/results/{fit_id}/manifest.json"):
        return manifest.parent
    return None


def _fits_consuming(path: Path, root: Path, index: FitIndex) -> list[dict[str, Any]]:
    """Return fits whose recorded inputs include ``path``."""
    resolved = Path(path).resolve()
    try:
        relative = resolved.relative_to(root).as_posix()
    except ValueError:
        return []

    consumers: list[dict[str, Any]] = []
    for entry in index.fits():
        fit_id = entry.get("fit_id")
        if not fit_id:
            continue
        fit_dir = _fit_dir_for(str(fit_id), root, index)
        if fit_dir is None:
            continue
        for recorded in FitDirectory(fit_dir).read_inputs():
            if recorded.get("path") == relative:
                consumers.append(entry)
                break
    return consumers


def _fit_producing_identical_bytes(
    path: Path, root: Path, index: FitIndex
) -> str | None:
    """Find a fit that produced a file with the same bytes as ``path``."""
    target = sha256_file(path)
    for entry in index.fits():
        fit_id = entry.get("fit_id")
        if not fit_id:
            continue
        fit_dir = _fit_dir_for(str(fit_id), root, index)
        if fit_dir is None:
            continue
        for candidate in fit_dir.rglob("*"):
            if not candidate.is_file() or candidate.name == "manifest.json":
                continue
            if candidate.stat().st_size != path.stat().st_size:
                continue
            if sha256_file(candidate) == target:
                return str(fit_id)
    return None


def _promotion_for(fit_id: str, index: FitIndex) -> dict[str, Any] | None:
    """Return the promotion currently pointing at ``fit_id``, if any."""
    for entry in reversed(index.promotions()):
        if entry.get("fit_id") == fit_id:
            label = entry.get("label")
            current = index.current_label(str(label), sample=entry.get("sample"))
            if current and current.get("fit_id") == fit_id:
                return entry
            return None
    return None
