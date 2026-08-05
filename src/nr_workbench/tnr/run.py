"""Loading a run and resolving its reference and late blocks.

Every metric needs the same three things: the loaded matrix, an inverse-variance
reference coadd, and a late block to define "fully changed". Resolving them once
into a :class:`TnrRun` keeps that consistent across metrics -- an amplitude
computed against one reference and a Q-band change computed against another
would not be comparable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from nr_workbench.tnr.io import find_reduction_json, load_run
from nr_workbench.tnr.reference import build_reference, resolve_interval_set


@dataclass
class BlockOptions:
    """Selection of the reference and late interval blocks.

    Attributes:
        ref_seconds: Take intervals within this many seconds of the run start.
        ref_labels: Comma-separated labels or fnmatch globs.
        late_seconds: Take intervals within this many seconds of the run end.
        late_labels: Comma-separated labels or fnmatch globs.
        min_ref_snr: Drop reference Q bins below this many sigma above zero.
        loo: Use the exact leave-one-out variance for intervals inside the
            reference block. Turning this off restores the naive ``+``, which
            biases those intervals' chi-squared low by about 10%.
    """

    ref_seconds: float | None = None
    ref_labels: str | None = None
    late_seconds: float | None = None
    late_labels: str | None = None
    min_ref_snr: float = 0.0
    loo: bool = True


@dataclass
class TnrRun:
    """A loaded run with its reference resolved.

    Attributes:
        data_dir: Directory the slices were read from.
        json_path: The reduction JSON that defined the interval order.
        times: Interval start times in seconds since the first interval.
        types: Interval type per interval, e.g. ``hold`` or ``eis``.
        labels: Interval label per interval.
        q: The common Q grid.
        R: Reflectivity, shape ``(T, Nq)``.
        dR: Uncertainty, shape ``(T, Nq)``.
        valid: Where a real measurement exists, shape ``(T, Nq)``.
        durations: Interval duration in seconds.
        ref_idx: Indices forming the reference block.
        ref_desc: Human-readable description of the reference block.
        late_idx: Indices forming the late block.
        late_desc: Human-readable description of the late block.
        R_ref: The coadded reference reflectivity.
        dR_ref: The coadded reference uncertainty.
        ref_valid: Where the reference has a value.
        in_ref: Per-interval membership of the reference block.
        options: The selection options used.
    """

    data_dir: Path
    json_path: Path
    times: np.ndarray
    types: list[str]
    labels: list[str]
    q: np.ndarray
    R: np.ndarray
    dR: np.ndarray
    valid: np.ndarray
    durations: np.ndarray
    ref_idx: np.ndarray
    ref_desc: str
    late_idx: np.ndarray
    late_desc: str
    R_ref: np.ndarray
    dR_ref: np.ndarray
    ref_valid: np.ndarray
    in_ref: np.ndarray
    options: BlockOptions = field(default_factory=BlockOptions)

    @property
    def n_intervals(self) -> int:
        """Number of intervals loaded."""
        return len(self.times)

    @property
    def duration(self) -> float:
        """Total counting time in seconds."""
        return float(self.durations.sum())

    @property
    def type_counts(self) -> dict[str, int]:
        """Number of intervals of each type."""
        counts: dict[str, int] = {}
        for itype in self.types:
            counts[itype] = counts.get(itype, 0) + 1
        return counts

    @property
    def median_ref_rel_err(self) -> float:
        """Median relative error of the reference coadd.

        The number that says whether the reference contributes noise of its
        own. Coadding a leading block typically brings a single slice's ~0.14
        down to ~0.035, at which point it effectively does not.
        """
        ok = self.ref_valid & (self.R_ref > 0)
        if not ok.any():
            return float("nan")
        return float(np.median(self.dR_ref[ok] / self.R_ref[ok]))

    def summary(self) -> dict[str, Any]:
        """Return the run description embedded in ``assessment.json``."""
        return {
            "run": _run_number(self.json_path),
            "n_intervals": self.n_intervals,
            "interval_types": self.type_counts,
            "duration_s": round(self.duration, 3),
            "q_range": [float(self.q.min()), float(self.q.max())],
            "n_q": int(len(self.q)),
            "reference": {
                "description": self.ref_desc,
                "n_intervals": int(self.ref_idx.size),
                "seconds": round(float(self.durations[self.ref_idx].sum()), 3),
                "median_rel_err": _round(self.median_ref_rel_err, 5),
            },
            "late_block": {
                "description": self.late_desc,
                "n_intervals": int(self.late_idx.size),
                "seconds": round(float(self.durations[self.late_idx].sum()), 3),
            },
        }


def load(
    data_dir: Path,
    options: BlockOptions | None = None,
    *,
    json_path: Path | None = None,
    min_duration: float = 5.0,
) -> TnrRun:
    """Load a run and resolve its reference and late blocks.

    Args:
        data_dir: Directory of reduced slices and the reduction JSON.
        options: Block-selection options. Defaults are the documented ones.
        json_path: Explicit reduction JSON. Discovered if omitted.
        min_duration: Skip intervals shorter than this many seconds.

    Returns:
        The loaded run.

    Raises:
        FileNotFoundError: If the directory or the reduction JSON is missing.
        ValueError: If the run cannot be loaded or a block cannot be resolved.
    """
    options = options or BlockOptions()
    data_dir = Path(data_dir)
    resolved_json = (
        Path(json_path) if json_path else Path(find_reduction_json(str(data_dir)))
    )

    times, types, labels, q, R, dR, valid, durations = load_run(
        str(data_dir), str(resolved_json), min_duration=min_duration
    )

    ref_idx, ref_desc = resolve_interval_set(
        times,
        types,
        labels,
        seconds=options.ref_seconds,
        label_spec=options.ref_labels,
        mode="leading",
        durations=durations,
    )

    # Key the late block on the reference block's dominant type rather than on
    # the last interval's: many runs end with a single eis scan, which would
    # otherwise define the late block on its own.
    ref_types, counts = np.unique(np.array(types)[ref_idx], return_counts=True)
    late_idx, late_desc = resolve_interval_set(
        times,
        types,
        labels,
        seconds=options.late_seconds,
        label_spec=options.late_labels,
        mode="trailing",
        match_type=str(ref_types[np.argmax(counts)]),
        durations=durations,
    )

    R_ref, dR_ref, ref_valid = build_reference(R, dR, valid, ref_idx)
    in_ref = np.zeros(len(times), dtype=bool)
    in_ref[ref_idx] = True

    return TnrRun(
        data_dir=data_dir,
        json_path=resolved_json,
        times=times,
        types=list(types),
        labels=list(labels),
        q=q,
        R=R,
        dR=dR,
        valid=valid,
        durations=durations,
        ref_idx=ref_idx,
        ref_desc=ref_desc,
        late_idx=late_idx,
        late_desc=late_desc,
        R_ref=R_ref,
        dR_ref=dR_ref,
        ref_valid=ref_valid,
        in_ref=in_ref,
        options=options,
    )


def _run_number(json_path: Path) -> int | None:
    """Recover the run number from the reduction JSON, if readable."""
    import json as _json

    try:
        payload = _json.loads(Path(json_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    value = payload.get("run_number")
    return (
        int(value)
        if isinstance(value, int | float | str) and str(value).isdigit()
        else None
    )


def _round(value: float, digits: int) -> float | None:
    """Round for JSON, mapping non-finite values to None."""
    return None if not np.isfinite(value) else round(float(value), digits)
