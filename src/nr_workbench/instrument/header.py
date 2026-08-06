"""Reading the metadata REF_L writes into a reduced file's header.

Every reduced steady-state file carries a full record of how it was made, in a
single JSON object on a ``# Meta:`` line:

    # Meta:{"theta": 0.007853609528660925, "norm_run": 218274,
            "dq_over_q": 0.0271, "scaling_factors": {"a": 4.0, ...}, ...}

That is exact, machine-readable, and already on disk. Guessing any of it is
therefore not a heuristic problem -- it is a parsing problem that was being
skipped.

**Angles are stored in radians.** ``theta: 0.00785`` is 0.45 degrees. Reading
it as degrees is the kind of error that produces a fit rather than a failure.

**Why this is not an LLM call.** ``theta`` sets the resolution of every point
through ``dT = dq/q * tan(theta)``, so a plausible-looking wrong value corrupts
a fit silently. A parser is exact, deterministic, offline, and testable; a
model call is none of those, and would be asked to read a number that is
already sitting in a JSON field. Where a model genuinely helps is an *unfamiliar*
header, and the right shape for that is: parse the format we know, fail loudly
when we meet one we do not, and document the format so an agent can read a new
one itself -- see the ``refl-reduced-headers`` skill.

Time-resolved slices carry no header at all. Nothing invents one; the caller is
told the angle is unknown and must declare it.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: The line carrying the JSON metadata block.
META_PREFIX = "# Meta:"

#: The fixed-width summary table REF_L also writes, used as a fallback when the
#: JSON block is absent. ``TwoTheta`` is in degrees and is twice theta.
_TABLE_RE = re.compile(
    r"^#\s+(?P<run>\d+)\s+(?P<norm>\d+)\s+(?P<twotheta>[\d.eE+-]+)\s+"
)

#: How many leading lines to scan. The header is a dozen lines; reading the
#: whole of a 250-row file to find it would still be cheap, but a malformed
#: file should not be read in full either.
_MAX_HEADER_LINES = 60


class HeaderError(Exception):
    """Raised when a header is present but cannot be understood."""


@dataclass
class ReducedHeader:
    """What a reduced REF_L file records about its own making.

    Attributes:
        path: The file this came from.
        theta: Incident angle in **degrees**, converted from the radians the
            file stores. ``None`` when the file carries no header.
        run: The run that produced this segment.
        norm_run: The direct-beam run it was divided by.
        sequence_number: Which angle segment this is, 1-based.
        sequence_id: The run number of the measurement as a whole.
        dq_over_q: Fractional resolution as reduced.
        scaling_factor: The constant the reduction multiplied this segment by.
        q_range: ``(q_min, q_max)`` as recorded.
        wavelength_range: ``(wl_min, wl_max)`` in angstroms.
        run_title: The operator's title for the run.
        start_time: ISO-8601 start, usable for a geometry lookup.
        experiment: The IPTS identifier.
        theta_offset: Any offset already applied by the reduction.
        source: ``meta`` when read from the JSON block, ``table`` from the
            fixed-width summary, ``none`` when there was no header.
        raw: The full JSON object, for anything not surfaced above.
    """

    path: Path
    theta: float | None = None
    run: int | None = None
    norm_run: int | None = None
    sequence_number: int | None = None
    sequence_id: int | None = None
    dq_over_q: float | None = None
    scaling_factor: float | None = None
    q_range: tuple[float | None, float | None] = (None, None)
    wavelength_range: tuple[float | None, float | None] = (None, None)
    run_title: str | None = None
    start_time: str | None = None
    experiment: str | None = None
    theta_offset: float | None = None
    source: str = "none"
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def has_theta(self) -> bool:
        """Whether an angle was actually recorded."""
        return self.theta is not None

    def as_dict(self) -> dict[str, Any]:
        """Return the JSON form."""
        return {
            "path": str(self.path),
            "theta_deg": self.theta,
            "run": self.run,
            "norm_run": self.norm_run,
            "sequence_number": self.sequence_number,
            "sequence_id": self.sequence_id,
            "dq_over_q": self.dq_over_q,
            "scaling_factor": self.scaling_factor,
            "q_range": list(self.q_range),
            "wavelength_range": list(self.wavelength_range),
            "run_title": self.run_title,
            "start_time": self.start_time,
            "experiment": self.experiment,
            "theta_offset": self.theta_offset,
            "source": self.source,
        }


def read_header(path: Path) -> ReducedHeader:
    """Read the metadata a reduced file carries about itself.

    Args:
        path: The reduced ASCII file.

    Returns:
        What the header said. ``source`` is ``none`` and ``theta`` is ``None``
        when the file has no header -- which is the normal case for a
        time-resolved slice, not an error.

    Raises:
        HeaderError: If a ``# Meta:`` line is present but is not valid JSON.
        OSError: If the file cannot be read.
    """
    path = Path(path)
    header = ReducedHeader(path=path)

    lines: list[str] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for index, line in enumerate(handle):
            if index >= _MAX_HEADER_LINES or not line.startswith("#"):
                break
            lines.append(line.rstrip("\n"))

    for line in lines:
        if line.startswith(META_PREFIX):
            _apply_meta(header, line[len(META_PREFIX) :], path)
            return header

    # No JSON block. The fixed-width table still carries TwoTheta.
    for line in lines:
        match = _TABLE_RE.match(line)
        if match:
            header.run = int(match.group("run"))
            header.norm_run = int(match.group("norm"))
            header.theta = float(match.group("twotheta")) / 2.0
            header.source = "table"
            return header

    return header


def _apply_meta(header: ReducedHeader, payload: str, path: Path) -> None:
    """Populate a header from the JSON metadata block."""
    try:
        meta = json.loads(payload)
    except ValueError as exc:
        raise HeaderError(
            f"{path}: the '# Meta:' line is not valid JSON ({exc}). "
            "The file may be truncated."
        ) from exc
    if not isinstance(meta, dict):
        raise HeaderError(f"{path}: '# Meta:' is not a JSON object.")

    header.raw = meta
    header.source = "meta"

    # Radians on disk, degrees everywhere in this package.
    theta = meta.get("theta")
    if isinstance(theta, int | float) and math.isfinite(theta):
        header.theta = math.degrees(float(theta))

    header.run = _as_int(meta.get("run_number"))
    header.norm_run = _as_int(meta.get("norm_run"))
    header.sequence_number = _as_int(meta.get("sequence_number"))
    header.sequence_id = _as_int(meta.get("sequence_id"))
    header.dq_over_q = _as_float(meta.get("dq_over_q"))
    header.theta_offset = _as_float(meta.get("theta_offset"))
    header.q_range = (_as_float(meta.get("q_min")), _as_float(meta.get("q_max")))
    header.wavelength_range = (
        _as_float(meta.get("wl_min")),
        _as_float(meta.get("wl_max")),
    )
    header.run_title = _as_str(meta.get("run_title"))
    header.start_time = _as_str(meta.get("start_time"))
    header.experiment = _as_str(meta.get("experiment"))

    factors = meta.get("scaling_factors")
    if isinstance(factors, dict):
        header.scaling_factor = _as_float(factors.get("a"))


def theta_for_run(steady_dir: Path, run: int) -> tuple[float | None, str | None]:
    """Find the incident angle of a run from its summed steady-state file.

    Time-resolved slices carry no header, and the angle appears in neither the
    reduction JSON nor the tNR template -- but the same run is *also* reduced
    as a summed dataset into the steady folder, and that file has the full
    ``# Meta:`` block. So the angle of a tNR series is on disk after all, one
    directory across.

    Args:
        steady_dir: The sample's ``data/steady`` directory.
        run: The run number to look for.

    Returns:
        ``(theta_degrees, source_filename)``, or ``(None, None)`` if no file
        for that run records an angle.
    """
    steady_dir = Path(steady_dir)
    if not steady_dir.is_dir():
        return (None, None)

    # Prefer a per-angle partial: it names its own sequence number, so a
    # measurement with several angles is unambiguous. The combined file is a
    # fine fallback and carries the same theta for a single-angle run.
    candidates = sorted(steady_dir.glob(f"REFL_{run}_*_partial.txt")) + sorted(
        steady_dir.glob(f"REFL_{run}_combined*.txt")
    )
    for candidate in candidates:
        try:
            header = read_header(candidate)
        except (HeaderError, OSError):
            continue
        if header.theta is not None:
            return (header.theta, candidate.name)
    return (None, None)


def thetas_for(paths: list[Path]) -> list[float | None]:
    """Read the incident angle of each file, in order.

    Args:
        paths: Reduced files, in segment order.

    Returns:
        Angles in degrees, ``None`` for any file with no recorded angle.
    """
    angles: list[float | None] = []
    for path in paths:
        try:
            angles.append(read_header(path).theta)
        except (HeaderError, OSError):
            angles.append(None)
    return angles


def _as_int(value: Any) -> int | None:
    """Coerce to int, tolerating the strings REF_L sometimes writes."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    """Coerce to a finite float, or ``None``."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _as_str(value: Any) -> str | None:
    """Coerce to a non-empty string, or ``None``."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None
