"""Consistency between the angle segments of one measurement.

REF_L measures a curve in three angle settings (0.45, 1.2, 3.5 degrees here),
each reduced separately and each carrying its own incident-intensity
normalisation. Where two segments overlap in Q they are measuring the same
sample and must agree. When they do not, the cause is almost always the
normalisation rather than the sample, and the symptom is a fitted model that
absorbs the discrepancy into a layer thickness or an SLD.

That failure is invisible on the usual log-R plot: a 5% step between segments
looks like nothing, and the fit quietly compensates. Measuring the ratio makes
it a number with an error bar instead.

This is also why the UI never stitches segments into one curve -- stitching
hides exactly the disagreement worth seeing.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

#: Ratio deviation, in sigma, past which segments are called inconsistent.
#: Three sigma on a weighted mean over tens of overlapping points is a real
#: normalisation error, not counting noise.
SIGNIFICANCE = 3.0

#: Fewest overlapping points worth forming a ratio from.
MIN_OVERLAP_POINTS = 3


@dataclass
class SegmentOverlap:
    """How two adjacent segments compare where they share Q.

    Attributes:
        lower: Label of the lower-Q segment.
        upper: Label of the higher-Q segment.
        n_points: Overlapping points used.
        q_min: Start of the shared Q range.
        q_max: End of the shared Q range.
        ratio: Weighted mean of ``R_lower / R_upper`` over the overlap.
        ratio_err: Uncertainty on that mean.
        chi2: Reduced chi-squared of the two curves against a constant ratio.
    """

    lower: str
    upper: str
    n_points: int
    q_min: float | None = None
    q_max: float | None = None
    ratio: float | None = None
    ratio_err: float | None = None
    chi2: float | None = None

    @property
    def sigma(self) -> float | None:
        """How many sigma the ratio sits from 1."""
        if self.ratio is None or not self.ratio_err:
            return None
        return abs(self.ratio - 1.0) / self.ratio_err

    @property
    def consistent(self) -> bool:
        """Whether the two segments agree within :data:`SIGNIFICANCE`."""
        deviation = self.sigma
        return deviation is None or deviation <= SIGNIFICANCE

    @property
    def scale_percent(self) -> float | None:
        """The disagreement as a percentage, which is how people discuss it."""
        if self.ratio is None:
            return None
        return (self.ratio - 1.0) * 100.0

    def as_dict(self) -> dict[str, Any]:
        """Return the JSON form."""
        return {
            "lower": self.lower,
            "upper": self.upper,
            "n_points": self.n_points,
            "q_min": self.q_min,
            "q_max": self.q_max,
            "ratio": self.ratio,
            "ratio_err": self.ratio_err,
            "sigma": self.sigma,
            "scale_percent": self.scale_percent,
            "chi2": self.chi2,
            "consistent": self.consistent,
        }


@dataclass
class Segment:
    """One angle segment's curve.

    Attributes:
        label: How to name it in output.
        q: Momentum transfer, ascending.
        r: Reflectivity.
        dr: Uncertainty.
    """

    label: str
    q: np.ndarray
    r: np.ndarray
    dr: np.ndarray


def load_segment(path: Path, label: str) -> Segment:
    """Read one reduced segment file.

    Args:
        path: The file to read.
        label: Label to attach.

    Returns:
        The segment, sorted by Q.

    Raises:
        ValueError: If the file has fewer than three columns.
    """
    data = np.loadtxt(path, ndmin=2)
    if data.size == 0 or data.shape[1] < 3:
        raise ValueError(
            f"{path} has {data.shape[1] if data.size else 0} column(s); "
            "need at least Q, R and dR."
        )
    order = np.argsort(data[:, 0])
    return Segment(
        label=label,
        q=data[order, 0],
        r=data[order, 1],
        dr=data[order, 2],
    )


def compare(lower: Segment, upper: Segment) -> SegmentOverlap:
    """Measure the scale ratio between two segments over their shared Q.

    The upper segment is interpolated onto the lower's Q points inside the
    overlap, and the ratio is the inverse-variance weighted mean of the
    pointwise ratios. Weighting matters: the overlap usually spans the noisy
    tail of one segment and the strong start of the other, and an unweighted
    mean lets the tail dominate.

    Args:
        lower: The lower-Q segment.
        upper: The higher-Q segment.

    Returns:
        The comparison. Fields are ``None`` when there is too little overlap.
    """
    q_min = max(lower.q.min(), upper.q.min())
    q_max = min(lower.q.max(), upper.q.max())
    result = SegmentOverlap(lower=lower.label, upper=upper.label, n_points=0)
    if not np.isfinite(q_min) or not np.isfinite(q_max) or q_min >= q_max:
        return result

    inside = (lower.q >= q_min) & (lower.q <= q_max)
    q = lower.q[inside]
    a = lower.r[inside]
    da = lower.dr[inside]
    if q.size < MIN_OVERLAP_POINTS:
        return result

    b, db = _interpolate_log(q, upper)

    # Both curves must be positive for a ratio to mean anything; reduced data
    # does contain negative R in the noise floor.
    good = (a > 0) & (b > 0) & (da > 0) & (db > 0)
    if good.sum() < MIN_OVERLAP_POINTS:
        return result

    q, a, da, b, db = q[good], a[good], da[good], b[good], db[good]
    ratio = a / b
    # Relative errors add in quadrature for a quotient.
    ratio_err = ratio * np.sqrt((da / a) ** 2 + (db / b) ** 2)

    weight = 1.0 / ratio_err**2
    mean = float(np.sum(weight * ratio) / np.sum(weight))
    mean_err = float(1.0 / np.sqrt(np.sum(weight)))
    residual = (ratio - mean) / ratio_err
    dof = max(len(ratio) - 1, 1)

    result.n_points = int(len(ratio))
    result.q_min = float(q.min())
    result.q_max = float(q.max())
    result.ratio = mean
    result.ratio_err = mean_err
    result.chi2 = float(np.sum(residual**2) / dof)
    return result


def _interpolate_log(q: np.ndarray, segment: Segment) -> tuple[np.ndarray, np.ndarray]:
    """Interpolate a segment onto ``q``, working in log R against log Q.

    Interpolating R linearly against Q biases the ratio, and the bias is not
    negligible for a check that reports at the 1% level. Reflectivity falls
    roughly as Q^-4 across an overlap; a chord drawn between two points of that
    convex curve sits above it, so every interpolated value is slightly high
    and the measured ratio comes out systematically low. On a realistic overlap
    that is 0.22%.

    Both axes have to be logged, not just R. Fresnel decay is a power law, so
    ``log R`` is straight in ``log Q`` and curved in ``Q`` -- logging R alone
    leaves 0.043% behind, while log-log is exact for a power law and leaves
    3e-7.

    Points where R is non-positive cannot be logged; they are interpolated
    linearly and are dropped by the positivity filter downstream anyway.

    Args:
        q: Target Q values.
        segment: The segment to interpolate.

    Returns:
        ``(r, dr)`` on the target grid.
    """
    positive = (segment.r > 0) & (segment.q > 0)
    if positive.sum() < 2:
        return np.interp(q, segment.q, segment.r), np.interp(q, segment.q, segment.dr)

    log_q = np.log(segment.q[positive])
    target = np.log(np.clip(q, 1e-30, None))

    r = np.exp(np.interp(target, log_q, np.log(segment.r[positive])))
    # The relative error is the well-behaved quantity across a decade of R, so
    # interpolate that and scale it back up.
    relative = segment.dr[positive] / segment.r[positive]
    dr = r * np.interp(target, log_q, relative)
    return r, dr


def compare_all(segments: list[Segment]) -> list[SegmentOverlap]:
    """Compare each adjacent pair of segments, ordered by Q.

    Args:
        segments: The segments of one measurement, in any order.

    Returns:
        One comparison per adjacent pair.
    """
    ordered = sorted(segments, key=lambda s: float(s.q.min()) if s.q.size else 0.0)
    return [compare(a, b) for a, b in zip(ordered, ordered[1:], strict=False)]
