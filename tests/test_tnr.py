"""Tests for the tNR metrics, the verdict, and the CLI.

The characterization tests prove the numerics are unchanged; these prove the
numerics are *right*, by injecting a known amplitude trajectory and template
into the fixture and asserting they are recovered.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

np = pytest.importorskip("numpy")

from click.testing import CliRunner  # noqa: E402

from nr_workbench.cli import main  # noqa: E402
from nr_workbench.tnr.report import (  # noqa: E402
    build_verdict,
    classify_template,
    classify_trajectory,
)
from tests import tnr_fixture  # noqa: E402


@pytest.fixture(scope="module")
def run_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A synthetic run on disk."""
    directory = tmp_path_factory.mktemp("tnr") / "data"
    tnr_fixture.build(directory)
    return directory


@pytest.fixture(scope="module")
def loaded(run_dir: Path):
    """The fixture run, loaded with its reference resolved."""
    from nr_workbench.tnr.run import load

    return load(run_dir)


# --------------------------------------------------------------------------
# Loading and the reference
# --------------------------------------------------------------------------


def test_load_orders_intervals_by_the_reduction_json(loaded) -> None:
    """Filenames sort lexically, which would put t001000 before t000240."""
    assert loaded.n_intervals == tnr_fixture.N_REFERENCE + 2 * tnr_fixture.N_PAIRS
    assert np.all(np.diff(loaded.times) > 0)
    assert loaded.labels[0] == "hold_initial_0"


def test_reference_block_is_the_leading_holds(loaded) -> None:
    assert loaded.ref_idx[0] == 0
    assert all(loaded.types[i] == "hold" for i in loaded.ref_idx)


def test_coadding_the_reference_beats_a_single_slice(loaded) -> None:
    """The reason a coadd is used: it stops the reference contributing noise.

    A single 30 s hold has ~14% relative error; the coadd of the leading block
    should be several times better, at which point it adds essentially nothing
    of its own to every comparison made against it.
    """
    assert loaded.median_ref_rel_err < tnr_fixture.HOLD_REL_ERR / 2.5


# --------------------------------------------------------------------------
# The amplitude recovers what was injected
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def amplitude(loaded):
    """The amplitude result for the fixture run."""
    from nr_workbench.tnr.metrics.amplitude import analyze_amplitude

    return analyze_amplitude(
        loaded.times,
        loaded.q,
        loaded.R,
        loaded.dR,
        loaded.valid,
        loaded.ref_idx,
        loaded.late_idx,
    )


def test_amplitude_recovers_the_injected_trajectory(amplitude) -> None:
    """a(t) must track the sigmoid that was put into the data.

    Compared by correlation rather than value: `a` is normalised so the late
    block projects to 1, and the fixture's late block is not the asymptote, so
    the scale differs by a constant. The shape is what the metric claims to
    recover.
    """
    expected = tnr_fixture.expected_amplitudes()
    finite = np.isfinite(amplitude.a)

    correlation = float(np.corrcoef(amplitude.a[finite], expected[finite])[0, 1])

    assert correlation > 0.97, (
        f"a(t) does not track the injected trajectory (r={correlation:.3f})"
    )


def test_amplitude_is_near_zero_over_the_reference_block(amplitude, loaded) -> None:
    """a = 0 means "indistinguishable from the initial state" by construction."""
    in_ref = amplitude.a[loaded.ref_idx]

    assert abs(float(np.mean(in_ref))) < 0.15


def test_chi2_res_is_near_one_for_a_single_template(amplitude) -> None:
    """chi2_res ~ 1 says one template suffices -- and confirms dR is right.

    The fixture injects exactly one Q shape, so anything much above 1 would
    mean the projection or the variance is wrong.
    """
    median = float(np.nanmedian(amplitude.chi2_res))

    assert 0.8 < median < 1.25, f"chi2_res median {median:.3f} is not near 1"


def test_amplitude_errors_scale_with_counting_time(amplitude, loaded) -> None:
    """The whole point of a linear statistic.

    The eis intervals count ~2.8x longer than the holds, so their sigma_a must
    be smaller by roughly sqrt(85/30) -- while a(t) itself stays on one curve.
    A quadratic statistic like chi-squared cannot do this.
    """
    types = np.array(loaded.types)
    finite = np.isfinite(amplitude.sigma_a)
    hold = float(np.median(amplitude.sigma_a[(types == "hold") & finite]))
    eis = float(np.median(amplitude.sigma_a[(types == "eis") & finite]))

    expected_ratio = np.sqrt(tnr_fixture.EIS_SECONDS / tnr_fixture.HOLD_SECONDS)
    assert eis < hold
    assert 0.6 * expected_ratio < hold / eis < 1.6 * expected_ratio


def test_hold_and_eis_lie_on_one_curve(amplitude, loaded) -> None:
    """If the two types separated, the counting-time artifact would be back.

    Compares each eis point against a smooth fit through the holds alone: they
    should be scattered about it, not offset from it.
    """
    types = np.array(loaded.types)
    finite = np.isfinite(amplitude.a)
    hold = (types == "hold") & finite
    eis = (types == "eis") & finite

    fit = np.polyfit(loaded.times[hold], amplitude.a[hold], 3)
    residual = amplitude.a[eis] - np.polyval(fit, loaded.times[eis])
    pulls = residual / amplitude.sigma_a[eis]

    assert abs(float(np.mean(pulls))) < 1.5, (
        "eis intervals are offset from the hold curve"
    )


# --------------------------------------------------------------------------
# Interpretation
# --------------------------------------------------------------------------


def test_template_classification_identifies_a_thickness_change(
    loaded, amplitude
) -> None:
    """The fixture injects an oscillatory template, i.e. a fringe shift.

    This is the field the model layer reads to decide which parameter to free,
    so getting it backwards would send a fit off in the wrong direction.
    """
    result = classify_template(loaded.q, amplitude.template, amplitude.t_valid)

    assert result["classification"] == "oscillatory"
    assert result["implied_change"] == "thickness"
    assert result["implied_thickness_A"] > 0


def test_template_classification_identifies_a_contrast_change() -> None:
    """A one-sign template is an overall reflectivity change: free an SLD."""
    q = np.linspace(0.01, 0.05, 60)
    template = 0.2 * np.ones_like(q)

    result = classify_template(q, template, np.ones_like(q, dtype=bool))

    assert result["classification"] == "one-sign"
    assert result["implied_change"] == "sld_contrast"


@pytest.mark.parametrize(
    ("name", "shape", "expected"),
    [
        ("linear", lambda t: np.linspace(0, 1, t.size), "monotonic"),
        ("sigmoid", lambda t: 1 / (1 + np.exp(-(t - 720) / 160)), "sigmoidal"),
        (
            "sigmoid_overshoot",
            lambda t: 1.25 / (1 + np.exp(-(t - 720) / 160)) - 0.09 * (t / 1600) ** 2,
            "sigmoidal",
        ),
        (
            "rise_then_fall",
            lambda t: np.concatenate(
                [
                    np.linspace(0, 1, t.size // 2),
                    np.linspace(1, 0.1, t.size - t.size // 2),
                ]
            ),
            "non-monotonic",
        ),
        ("late_onset", lambda t: np.clip((t - 1100) / 500, 0, None), "monotonic"),
    ],
)
def test_trajectory_classification(name: str, shape, expected: str) -> None:
    """The shape of a(t) is what picks a constraint form, so it must be right.

    `sigmoid_overshoot` is the case that broke the first implementation: a
    chord-residual test calls it non-monotonic because the chord ends below the
    plateau.
    """
    times = np.linspace(0, 1600, 32)
    rng = np.random.default_rng(7)
    values = shape(times) + 0.03 * rng.standard_normal(times.size)
    finite = np.ones(times.size, dtype=bool)

    assert (
        classify_trajectory(times, values, finite, np.full(times.size, 0.03))
        == expected
    )


def test_an_insignificant_drift_is_flat_only_when_sigma_is_known() -> None:
    """The error bars are what separate a real trend from a tempting one.

    A clean monotonic ramp whose whole span sits inside the uncertainty is not
    a trajectory -- but it looks exactly like one to a shape test. With sigma
    it is correctly called flat; without, it reads as monotonic and would
    invite fitting a time dependence that the data does not support.
    """
    times = np.linspace(0, 1600, 32)
    values = np.linspace(0.0, 0.10, times.size)
    finite = np.ones(times.size, dtype=bool)
    sigma = np.full(times.size, 0.30)

    assert classify_trajectory(times, values, finite, sigma) == "flat"
    assert classify_trajectory(times, values, finite) == "monotonic"


def test_verdict_stops_at_a_flat_variogram() -> None:
    """The reading order says stop, so the verdict must not talk over it."""
    payload = {
        "variogram": {"flat": True},
        "amplitude": {"max_significance": 1.2, "single_template_sufficient": True},
        "template": {},
    }

    assert "no detectable change" in build_verdict(payload)


def test_verdict_reports_an_insignificant_amplitude() -> None:
    payload = {
        "variogram": {"flat": False},
        "amplitude": {"max_significance": 1.2, "single_template_sufficient": True},
        "template": {},
    }

    assert "no significant change" in build_verdict(payload)


def test_verdict_flags_a_flat_variogram_against_a_significant_amplitude() -> None:
    """When the two disagree, the disagreement is the finding.

    Silently believing the amplitude would hide the most likely cause: a
    reference block chosen over a period when the sample was already moving.
    """
    payload = {
        "variogram": {"flat": True},
        "amplitude": {
            "max_significance": 9.0,
            "single_template_sufficient": True,
            "trajectory": "monotonic",
            "plateau": False,
        },
        "template": {"implied_change": "thickness"},
    }

    verdict = build_verdict(payload)

    assert "ambiguous" in verdict
    assert "reference block" in verdict


def test_verdict_warns_when_one_template_is_not_enough() -> None:
    payload = {
        "variogram": {"flat": False},
        "amplitude": {
            "max_significance": 12.0,
            "single_template_sufficient": False,
            "trajectory": "monotonic",
            "plateau": False,
        },
        "template": {"implied_change": "thickness"},
    }

    verdict = build_verdict(payload)

    assert "rotates in Q" in verdict
    assert "PCA" in verdict


# --------------------------------------------------------------------------
# The CLI
# --------------------------------------------------------------------------


def test_assess_writes_the_assessment_json(run_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "assessment"

    result = CliRunner().invoke(
        main, ["tnr", "assess", str(run_dir), "--out", str(out), "--label", "r999001"]
    )

    assert result.exit_code == 0, result.output
    payload = json.loads((out / "r999001_assessment.json").read_text(encoding="utf-8"))
    assert payload["schema"] == "nrw-tnr-assessment/1"
    assert payload["run"] == tnr_fixture.RUN_NUMBER
    assert payload["amplitude"]["trajectory"] == "sigmoidal"
    assert payload["template"]["implied_change"] == "thickness"
    assert "logistic" in payload["verdict"]


def test_assess_no_plots_skips_the_pngs(run_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "fast"

    result = CliRunner().invoke(
        main, ["tnr", "assess", str(run_dir), "--out", str(out), "--no-plots"]
    )

    assert result.exit_code == 0, result.output
    assert not list(out.glob("*.png"))
    assert (out / "assessment.json").is_file()


def test_assess_writes_a_tool_result_manifest(run_dir: Path, tmp_path: Path) -> None:
    """Interop: an orchestrator reads ndip-tool-result/1 from every tool."""
    manifest = tmp_path / "result.json"

    result = CliRunner().invoke(
        main,
        [
            "tnr",
            "assess",
            str(run_dir),
            "--out",
            str(tmp_path / "o"),
            "--no-plots",
            "--result-out",
            str(manifest),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["schema"] == "ndip-tool-result/1"
    assert payload["tool"] == "nrw-tnr-assess"
    assert payload["info"]["implied_change"] == "thickness"


def test_manifest_lists_the_intervals(run_dir: Path) -> None:
    result = CliRunner().invoke(main, ["tnr", "manifest", str(run_dir), "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["n_intervals"] == tnr_fixture.N_REFERENCE + 2 * tnr_fixture.N_PAIRS
    assert payload["interval_types"] == {
        "hold": tnr_fixture.N_REFERENCE + tnr_fixture.N_PAIRS,
        "eis": tnr_fixture.N_PAIRS,
    }


def test_single_metric_command_emits_its_block(run_dir: Path, tmp_path: Path) -> None:
    result = CliRunner().invoke(
        main,
        [
            "tnr",
            "amplitude",
            str(run_dir),
            "--out",
            str(tmp_path / "amp"),
            "--no-plots",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["trajectory"] == "sigmoidal"


def test_assess_fails_clearly_on_a_directory_with_no_reduction_json(
    tmp_path: Path,
) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()

    result = CliRunner().invoke(main, ["tnr", "assess", str(empty)])

    assert result.exit_code != 0
    assert "reduction.json" in result.output
