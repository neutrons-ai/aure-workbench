"""`nrw assess`: the checks that run without a language model.

AuRE's own offline evaluator is three chi-squared bands and reads no
parameters, no bounds and no posterior. These tests pin the things it cannot
see, because those are the reason not to wrap it.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
from click.testing import CliRunner

from nr_workbench.cli import main
from nr_workbench.fitting.assess import (
    Assessment,
    Finding,
    as_markdown,
    bic,
    check,
    per_model_chisq,
    read_bounds,
    read_par,
)

from .test_lifecycle import write_partials


def write_fit(
    directory: Path,
    *,
    par: dict[str, float],
    bounds: dict[str, tuple[float, float] | None],
    err: dict[str, dict] | None = None,
    out: str | None = None,
) -> None:
    """Write the bumps outputs an assessment reads."""
    fit = directory / "fit"
    fit.mkdir(parents=True, exist_ok=True)
    (fit / "m.par").write_text(
        "".join(f"{name} {value!r}\n" for name, value in par.items()), encoding="utf-8"
    )
    (fit / "m.json").write_text(
        json.dumps(
            {
                "references": [
                    {
                        "name": name,
                        "fixed": limits is None,
                        "bounds": list(limits) if limits else None,
                    }
                    for name, limits in bounds.items()
                ]
            }
        ),
        encoding="utf-8",
    )
    if err is not None:
        (fit / "m-err.json").write_text(json.dumps(err), encoding="utf-8")
    if out is not None:
        (fit / "m.out").write_text(out, encoding="utf-8")


def manifest(chisq: float = 1.2, n_free: int = 2, points: int = 100) -> dict:
    """A manifest shaped like the real one."""
    return {
        "info": {
            "chisq": chisq,
            "n_free": n_free,
            "n_points": points - n_free,
            "models": [{"name": "a", "n_points": points}],
        },
        "provenance": {"fit_id": "20260807-163359Z-0103d9c7"},
    }


# --------------------------------------------------------------------------
# Reading what the fitter wrote
# --------------------------------------------------------------------------


def test_bounds_come_from_the_serialised_problem(tmp_path: Path) -> None:
    """The spec expresses ranges indirectly -- `pm: 0.3` around a value, an
    endpoint that becomes two parameters. The problem JSON has already
    resolved all of it into the names the fitter used."""
    write_fit(
        tmp_path,
        par={"a thickness": 50.0},
        bounds={"a thickness": (40.0, 80.0), "a rho fixed": None},
    )

    assert read_bounds(tmp_path) == {"a thickness": (40.0, 80.0)}


def test_par_names_are_split_on_the_last_field(tmp_path: Path) -> None:
    """Parameter names contain spaces: `run218386 probe intensity 1.0999`."""
    write_fit(tmp_path, par={"run218386 probe intensity": 1.1}, bounds={})

    assert read_par(tmp_path) == {"run218386 probe intensity": 1.1}


def test_per_model_chisq_is_read_from_the_run_log(tmp_path: Path) -> None:
    write_fit(
        tmp_path,
        par={},
        bounds={},
        out=(
            "-- Model 0 tnr#0\n[chisq=1.441(28), nllf=183.76]\n"
            "-- Model 1 tnr#1\n[chisq=4.002(31), nllf=190.10]\n"
            "[overall chisq=1.285(24)]\n"
        ),
    )

    assert per_model_chisq(tmp_path) == {"tnr#0": 1.441, "tnr#1": 4.002}


# --------------------------------------------------------------------------
# What the checks find
# --------------------------------------------------------------------------


def test_a_railed_parameter_is_reported_as_a_bound_not_a_measurement(
    tmp_path: Path,
) -> None:
    write_fit(
        tmp_path,
        par={"a thickness": 199.999},
        bounds={"a thickness": (100.0, 200.0)},
    )

    result = check(tmp_path, manifest())

    # `no-uncertainty` rides along because this fixture has no -err.json.
    assert [f.kind for f in result.findings] == ["bound", "no-uncertainty"]
    assert result.findings[0].edge == "upper"
    assert result.findings[0].limit == 200.0
    assert "not a measurement" in result.findings[0].message


def test_a_parameter_well_inside_its_range_is_not_flagged(tmp_path: Path) -> None:
    write_fit(
        tmp_path, par={"a thickness": 150.0}, bounds={"a thickness": (100.0, 200.0)}
    )

    assert [f.kind for f in check(tmp_path, manifest()).findings] == [
        "no-uncertainty"
    ], "the range is fine; only the missing posterior is worth saying"


def test_a_posterior_touching_its_bound_is_caught_when_the_point_is_not(
    tmp_path: Path,
) -> None:
    """The check AuRE cannot make: it tests the point estimate against the
    bound and never reads `uncertainties`, so a parameter whose p95 edge sits
    on its floor looks fine."""
    write_fit(
        tmp_path,
        par={"CuOx roughness": 5.11},
        bounds={"CuOx roughness": (5.0, 11.0)},
        err={
            "CuOx roughness": {"best": 5.11, "p68": [5.18, 6.83], "p95": [5.03, 8.38]}
        },
    )

    kinds = [f.kind for f in check(tmp_path, manifest()).findings]

    assert "posterior-bound" in kinds
    assert "bound" not in kinds, "the point estimate genuinely is not on the bound"


def test_an_unconstrained_posterior_is_reported(tmp_path: Path) -> None:
    """A p95 spanning most of its prior means the fit handed your range back."""
    write_fit(
        tmp_path,
        par={"Ti rho": 0.0},
        bounds={"Ti rho": (-2.0, 1.0)},
        err={"Ti rho": {"best": 0.0, "p68": [-1.0, 1.0], "p95": [-1.9, 0.95]}},
    )

    findings = check(tmp_path, manifest()).findings

    assert [f.kind for f in findings] == ["unconstrained"]
    assert "%" in findings[0].message


def test_a_best_fit_outside_its_own_interval_is_reported(tmp_path: Path) -> None:
    """Legitimate for a skewed posterior, and worth saying so the reader
    quotes the median rather than the point."""
    write_fit(
        tmp_path,
        par={"a rho": 5.0},
        bounds={"a rho": (0.0, 100.0)},
        err={"a rho": {"best": 5.0, "p68": [5.2, 6.8], "p95": [5.1, 8.0]}},
    )

    assert [f.kind for f in check(tmp_path, manifest()).findings] == ["skewed"]


def test_a_railed_parameter_is_reported_once_not_three_times(tmp_path: Path) -> None:
    """The posterior checks earn their place by catching what the point
    estimate misses. Repeating them buries the other parameters."""
    write_fit(
        tmp_path,
        par={"a thickness": 199.999},
        bounds={"a thickness": (100.0, 200.0)},
        err={
            "a thickness": {
                "best": 199.999,
                "p68": [199.98, 199.999],
                "p95": [199.9, 200.0],
            }
        },
    )

    assert [f.kind for f in check(tmp_path, manifest()).findings] == ["bound"]


def test_a_missing_uncertainty_is_absence_of_information_not_certainty(
    tmp_path: Path,
) -> None:
    write_fit(tmp_path, par={"a": 1.0}, bounds={"a": (0.0, 10.0)})

    findings = check(tmp_path, manifest()).findings

    assert [f.kind for f in findings] == ["no-uncertainty"]
    assert "not the same as small uncertainties" in findings[0].message


def test_an_uneven_co_refinement_is_reported(tmp_path: Path) -> None:
    """One overall chi-squared hides a model fitting three times worse."""
    write_fit(
        tmp_path,
        par={},
        bounds={},
        out="-- Model 0 a\n[chisq=1.0]\n-- Model 1 b\n[chisq=4.0]\n",
    )

    kinds = [f.kind for f in check(tmp_path, manifest()).findings]

    assert "uneven-fit" in kinds


# --------------------------------------------------------------------------
# Numbers
# --------------------------------------------------------------------------


def test_bic_matches_aures_formula() -> None:
    """Comparable across the two tools only if the form is the same."""
    assert bic(2.0, 100, 5) == pytest.approx(100 * math.log(2.0) + 5 * math.log(100))


def test_n_points_sums_the_models_rather_than_using_dof(tmp_path: Path) -> None:
    """`info.n_points` is bumps' `dof`, so BIC computed from it is wrong by
    k log n -- and for a 15-slice co-refinement that is not a rounding error.
    """
    write_fit(tmp_path, par={}, bounds={})
    payload = {
        "info": {
            "chisq": 2.0,
            "n_free": 8,
            "n_points": 3907,
            "models": [{"name": f"s{i}", "n_points": 261} for i in range(15)],
        },
        "provenance": {"fit_id": "f"},
    }

    result = check(tmp_path, payload)

    assert result.n_points == 3915
    assert result.bic == pytest.approx(bic(2.0, 3915, 8))


# --------------------------------------------------------------------------
# Rendering and the command
# --------------------------------------------------------------------------


def test_the_markdown_says_plainly_when_nothing_was_flagged() -> None:
    text = as_markdown(Assessment(fit_id="f", chisq=1.2, n_free=3, n_points=100))

    assert "No parameter sits on a bound" in text


def test_a_language_model_verdict_is_labelled_as_one() -> None:
    """It has not seen the data. A reader must be able to tell its opinion
    from the arithmetic above it."""
    text = as_markdown(
        Assessment(
            fit_id="f",
            chisq=1.2,
            judgement={"quality_assessment": "good", "issues": ["thin oxide"]},
        )
    )

    assert "thin oxide" in text
    assert "It has not seen the data" in text


def test_findings_render_with_their_severity() -> None:
    text = as_markdown(
        Assessment(
            fit_id="f",
            findings=[Finding(kind="bound", severity="warn", message="on the floor")],
        )
    )

    assert "**bound**" in text
    assert "on the floor" in text


@pytest.fixture
def fitted(tmp_path: Path, monkeypatch) -> tuple[Path, str]:
    """A project with one real fit."""
    pytest.importorskip("refl1d")
    root = tmp_path / "proj"
    assert (
        CliRunner().invoke(main, ["init", str(root), "--sample", "S1"]).exit_code == 0
    )
    write_partials(root / "samples" / "S1" / "data" / "steady", 100001)
    monkeypatch.chdir(root)
    runner = CliRunner()
    runner.invoke(main, ["model", "new", "S1", "--name", "m"])
    runner.invoke(main, ["model", "generate", "samples/S1/models/m.yaml"])
    result = runner.invoke(
        main,
        [
            "fit",
            "run",
            "samples/S1/models/m.py",
            "--method",
            "amoeba",
            "--steps",
            "20",
            "--seed",
            "1",
            "--parallel",
            "1",
        ],
    )
    assert result.exit_code == 0, result.output
    rows = json.loads(runner.invoke(main, ["ls", "--json"]).stdout)
    return root, rows[0]["fit_id"]


def test_assess_writes_into_the_fits_note(
    fitted: tuple[Path, str], monkeypatch
) -> None:
    """An assessment printed to a terminal is gone by the next command. In the
    note it is in the bundle, on the fit page, and in front of whoever asks."""
    root, fit_id = fitted

    result = CliRunner().invoke(main, ["assess", fit_id, "--no-llm"])

    assert result.exit_code == 0, result.output
    notes = (root / "samples" / "S1" / "results" / fit_id / "NOTES.md").read_text()
    assert "## Assessment" in notes
    assert "chi-squared" in notes


def test_assess_without_an_endpoint_says_what_was_not_checked(
    fitted: tuple[Path, str], monkeypatch
) -> None:
    """Silence would read as "nothing to report" rather than "not asked"."""
    root, fit_id = fitted
    monkeypatch.setattr(
        "nr_workbench.aure_adapter.llm_available", lambda: False, raising=False
    )

    result = CliRunner().invoke(main, ["assess", fit_id, "--json"])

    payload = json.loads(result.stdout)
    assert any("No language-model endpoint" in p for p in payload["problems"])


def test_assess_json_carries_every_finding(
    fitted: tuple[Path, str], monkeypatch
) -> None:
    root, fit_id = fitted

    result = CliRunner().invoke(main, ["assess", fit_id, "--no-llm", "--json"])

    payload = json.loads(result.stdout)
    assert payload["schema"] == "nrw-fit-assessment/1"
    assert payload["fit_id"] == fit_id
    assert isinstance(payload["findings"], list)
