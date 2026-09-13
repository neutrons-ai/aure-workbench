"""Data-quality checks and the AuRE adapter.

The adapter tests are contract tests: they assert that the AuRE functions this
package calls still exist with the shapes we unpack. AuRE declares no stable
API, so this is the difference between an upstream rename failing in CI and
failing halfway through someone's fit.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from nr_workbench.data.overlap import (
    MIN_OVERLAP_POINTS,
    Segment,
    compare,
    compare_all,
    load_segment,
)


def curve(
    q_min: float, q_max: float, n: int = 60, *, scale: float = 1.0, noise: float = 0.0
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """A smooth decaying curve over a Q range, optionally rescaled."""
    rng = np.random.default_rng(7)
    q = np.linspace(q_min, q_max, n)
    r = scale * 1e-3 * (0.01 / q) ** 4
    dr = 0.02 * r
    if noise:
        r = r * (1.0 + noise * rng.standard_normal(n))
    return q, r, dr


def segment(label: str, *args: object, **kwargs: object) -> Segment:
    """Build a Segment from :func:`curve`."""
    q, r, dr = curve(*args, **kwargs)  # type: ignore[arg-type]
    return Segment(label=label, q=q, r=r, dr=dr)


# --------------------------------------------------------------------------
# Segment overlap
# --------------------------------------------------------------------------


def test_identical_segments_have_unit_ratio() -> None:
    """Two segments of the same curve must agree exactly."""
    lower = segment("a", 0.01, 0.10)
    upper = segment("b", 0.05, 0.20)

    result = compare(lower, upper)

    assert result.n_points > MIN_OVERLAP_POINTS
    assert result.ratio == pytest.approx(1.0, abs=1e-6)
    assert result.consistent


def test_a_known_scale_error_is_recovered() -> None:
    """A 20% normalisation error must read as a 20% ratio.

    This is the whole point of the check: the number it reports has to be the
    number you would multiply the segment by to fix it.
    """
    lower = segment("a", 0.01, 0.10, scale=1.2)
    upper = segment("b", 0.05, 0.20, scale=1.0)

    result = compare(lower, upper)

    assert result.ratio == pytest.approx(1.2, rel=1e-6)
    assert result.scale_percent == pytest.approx(20.0, rel=1e-4)
    assert not result.consistent


def test_noise_alone_does_not_trip_the_check() -> None:
    """Counting noise must not be reported as a normalisation error."""
    lower = segment("a", 0.01, 0.10, noise=0.02)
    upper = segment("b", 0.05, 0.20, noise=0.02)

    result = compare(lower, upper)

    assert result.consistent, f"noise read as a {result.scale_percent:.1f}% error"


def test_segments_that_do_not_overlap_report_nothing() -> None:
    """Disjoint Q ranges have no ratio, and must not invent one."""
    result = compare(segment("a", 0.01, 0.04), segment("b", 0.10, 0.20))

    assert result.ratio is None
    assert result.n_points == 0
    assert result.consistent, "no measurement is not the same as a bad measurement"


def test_negative_reflectivity_points_are_excluded() -> None:
    """Reduced data carries negative R in the noise floor; a ratio there is
    meaningless and must not poison the weighted mean."""
    q, r, dr = curve(0.01, 0.10)
    r[-5:] = -abs(r[-5:])
    lower = Segment("a", q, r, dr)
    upper = segment("b", 0.05, 0.20)

    result = compare(lower, upper)

    assert result.ratio == pytest.approx(1.0, abs=1e-6)


def test_compare_all_orders_segments_by_q() -> None:
    """Segments given out of order must still be paired by adjacency."""
    high = segment("high", 0.10, 0.30)
    low = segment("low", 0.01, 0.06)
    mid = segment("mid", 0.04, 0.15)

    pairs = [(c.lower, c.upper) for c in compare_all([high, low, mid])]

    assert pairs == [("low", "mid"), ("mid", "high")]


def test_load_segment_rejects_a_two_column_file(tmp_path: Path) -> None:
    """A file without dR cannot be weighted, so it is refused."""
    path = tmp_path / "seg.txt"
    path.write_text("0.01 1.0\n0.02 0.5\n", encoding="utf-8")

    with pytest.raises(ValueError, match="at least Q, R and dR"):
        load_segment(path, "x")


def test_load_segment_sorts_by_q(tmp_path: Path) -> None:
    """Overlap logic assumes ascending Q; the file need not be."""
    path = tmp_path / "seg.txt"
    path.write_text("0.03 1.0 0.1\n0.01 3.0 0.3\n0.02 2.0 0.2\n", encoding="utf-8")

    result = load_segment(path, "x")

    assert list(result.q) == [0.01, 0.02, 0.03]
    assert list(result.r) == [3.0, 2.0, 1.0]


# --------------------------------------------------------------------------
# The AuRE adapter
# --------------------------------------------------------------------------


def test_availability_check_does_not_import_aure() -> None:
    """`nrw doctor` calls this, so it must not pay the 1.5-3 s import."""
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from nr_workbench.aure_adapter import is_available; "
            "is_available(); "
            "assert 'aure' not in sys.modules, 'is_available imported aure'; "
            "print('clean')",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "clean" in result.stdout


@pytest.mark.integration
def test_aure_contract_still_holds() -> None:
    """Every AuRE function this package calls must still exist.

    AuRE is pinned by commit and has no stability policy. When this fails, the
    fix is in `aure_adapter` and nowhere else -- that is what the module is for.
    """
    pytest.importorskip("aure")
    from nr_workbench.aure_adapter import contract

    assert contract() == {}


@pytest.mark.integration
def test_features_unpack_aures_flat_payload() -> None:
    """AuRE returns one flat mapping, and the adapter unpacks it by key.

    Pinning the key names here is the point: a rename upstream would otherwise
    show up as silently empty estimates rather than an error.
    """
    pytest.importorskip("aure")
    from nr_workbench.aure_adapter import extract_features

    # Enough Q range and structure for the FFT to find something.
    q = np.linspace(0.01, 0.3, 600)
    r = 1e-3 * (0.01 / q) ** 4 * (1.0 + 0.3 * np.cos(q * 400.0))
    r = np.clip(r, 1e-12, None)
    features = extract_features(q, r, 0.02 * r)

    assert features.n_points > 0
    assert features.q_range[0] == pytest.approx(0.01, rel=1e-6)
    assert features.thickness.value is not None
    assert isinstance(features.n_fringes, int)


@pytest.mark.integration
def test_an_estimate_wider_than_itself_is_not_usable() -> None:
    """AuRE reports thickness 444 +/- 555 A on real data; that constrains nothing."""
    from nr_workbench.aure_adapter import Estimate

    assert not Estimate(value=444.0, uncertainty=555.0).usable
    assert Estimate(value=444.0, uncertainty=20.0).usable
    assert Estimate(value=444.0).usable
    assert not Estimate().usable


@pytest.mark.integration
def test_validate_flags_unnormalised_data() -> None:
    """R > 1 is the signature of a segment awaiting its intensity scale."""
    pytest.importorskip("aure")
    from nr_workbench.aure_adapter import validate

    q = np.linspace(0.01, 0.2, 50)
    r = np.full_like(q, 1.5)
    result = validate(q, r, 0.05 * r)

    assert not result.valid
    assert any("R > 1" in issue for issue in result.issues)


@pytest.mark.integration
def test_resolved_commit_identifies_the_pin() -> None:
    """We pin a SHA on AuRE's main, so the commit is the only identity."""
    pytest.importorskip("aure")
    from nr_workbench.aure_adapter import resolved_commit

    commit = resolved_commit()

    assert commit is None or len(commit) == 40


def test_interpolation_is_log_log_not_linear() -> None:
    """Interpolating R against Q linearly biases the ratio low.

    Reflectivity is a power law, so a chord between two points of that convex
    curve sits above it and every interpolated value comes out slightly high.
    On a realistic overlap the bias is 0.22% -- a fifth of the 1% scale this
    check reports at, and always in the same direction, so it does not average
    away. log-log is exact for a power law.

    Pinned because reverting to `np.interp` looks like a harmless
    simplification and silently reintroduces a systematic error.
    """
    lower = segment("a", 0.01, 0.10)
    upper = segment("b", 0.05, 0.20)

    log_log = compare(lower, upper).ratio
    assert log_log == pytest.approx(1.0, abs=1e-9)

    inside = (lower.q >= upper.q.min()) & (lower.q <= lower.q.max())
    q = lower.q[inside]
    linear = np.interp(q, upper.q, upper.r)
    ratio = lower.r[inside] / linear
    weight = 1.0 / (ratio * np.sqrt(2) * 0.02) ** 2
    naive = float(np.sum(weight * ratio) / np.sum(weight))

    assert abs(naive - 1.0) > 1e-3, "expected the naive method to be biased"
    assert abs(log_log - 1.0) < abs(naive - 1.0) / 100
