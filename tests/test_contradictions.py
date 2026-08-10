"""Where a spec disagrees with what the data already said.

Two of these rules were the most-repeated Red Flag in the skill set with
nothing comparing the two sides. The cases are drawn from the real Cu/THF
analysis, including one the checker got *wrong* on first writing --- the
promoted fit --- which is kept as a permanent regression case.
"""

from __future__ import annotations

from typing import Any

import pytest

from nr_workbench.contradictions import check
from nr_workbench.spec.models import load_spec

SPEC = """\
schema: nrw-model/1
name: t
sample: S1
materials:
  dTHF: {rho: 6.35}
  CuOx: {rho: 5.3}
  Cu: {rho: 6.55}
  Si: {rho: 2.07}
stack:
  - {name: dTHF, thickness: 0, roughness: 5}
  - {name: CuOx, thickness: 60, roughness: 8}
  - {name: Cu, thickness: 500, roughness: 5}
  - {name: Si}
probe: {resolution: angular_only, dq_is_fwhm: true}
states:
  - {name: s1, run: 100001, segments: auto, thetas: [0.45]}
parameters:
@PARAMETERS@
@CONSTRAINTS@
"""

DEFAULT_PARAMETERS = """\
  - {path: CuOx.thickness, range: [40, 80], per: state}
  - {path: CuOx.roughness, range: [5, 11], per: state}
  - {path: Cu.roughness, range: [5, 11], per: state}
"""


def spec_of(
    parameters: str = DEFAULT_PARAMETERS, constraints: str = "", tmp_path: Any = None
) -> Any:
    """Build and load a spec from the template."""
    # `.replace`, not `.format`: the YAML uses flow-style braces
    # (`{name: dTHF, ...}`) which `str.format` reads as fields.
    text = SPEC.replace("@PARAMETERS@", parameters).replace(
        "@CONSTRAINTS@", constraints
    )
    path = tmp_path / "m.yaml"
    path.write_text(text, encoding="utf-8")
    return load_spec(path)


def assessment(
    trajectory: str = "monotonic",
    implied: str | None = "thickness",
    flat: bool = False,
) -> dict[str, Any]:
    """An `nrw-tnr-assessment/1` payload with the fields the checker reads."""
    return {
        "amplitude": {"trajectory": trajectory},
        "template": {"implied_change": implied},
        "variogram": {"flat": flat},
    }


SERIES = """\
series:
  - {name: tnr, run: 100003, reduced_dir: data/tnr/100003, theta: 0.6}
"""


# --------------------------------------------------------------------------
# The form and the trajectory
# --------------------------------------------------------------------------


def test_a_sigmoidal_form_on_a_monotonic_trajectory_is_flagged(tmp_path) -> None:
    constraints = (
        SERIES
        + "constraints:\n"
        + "  - {series: tnr, form: logistic, paths: [CuOx.thickness],"
        " from: s1, to: s1, t_half: [0, 100], width: [1, 50]}\n"
    )
    spec = spec_of(constraints=constraints, tmp_path=tmp_path)

    found = check(spec, tnr=assessment(trajectory="monotonic")).contradictions

    flagged = [c for c in found if c.kind == "form-contradicts-trajectory"]
    assert len(flagged) == 1
    assert "sigmoidal" in flagged[0].message and "monotonic" in flagged[0].message


def test_a_monotonic_form_on_a_reversing_trajectory_is_flagged(tmp_path) -> None:
    """A single monotonic form cannot describe a change that reverses."""
    constraints = (
        SERIES
        + "constraints:\n"
        + "  - {series: tnr, form: linear_in_time, paths: [CuOx.thickness],"
        " from: s1, to: s1}\n"
    )
    spec = spec_of(constraints=constraints, tmp_path=tmp_path)

    found = check(spec, tnr=assessment(trajectory="non-monotonic")).contradictions

    assert [c.kind for c in found if "trajectory" in c.kind] == [
        "form-contradicts-trajectory"
    ]


def test_a_matching_form_is_not_flagged(tmp_path) -> None:
    constraints = (
        SERIES
        + "constraints:\n"
        + "  - {series: tnr, form: linear_in_time, paths: [CuOx.thickness],"
        " from: s1, to: s1}\n"
    )
    spec = spec_of(constraints=constraints, tmp_path=tmp_path)

    found = check(spec, tnr=assessment(trajectory="monotonic")).contradictions

    assert [c for c in found if "trajectory" in c.kind] == []


# --------------------------------------------------------------------------
# The freed parameter and the template
# --------------------------------------------------------------------------


def test_varying_only_an_sld_when_the_template_implies_thickness(tmp_path) -> None:
    """An oscillatory template has nodes, and a uniform SLD change cannot
    produce them."""
    constraints = (
        SERIES
        + "constraints:\n"
        + "  - {series: tnr, form: linear_in_time, paths: [CuOx.rho],"
        " from: s1, to: s1}\n"
    )
    parameters = (
        DEFAULT_PARAMETERS + "  - {path: CuOx.rho, range: [3.5, 6.5], per: state}\n"
    )
    spec = spec_of(parameters=parameters, constraints=constraints, tmp_path=tmp_path)

    found = check(spec, tnr=assessment(implied="thickness")).contradictions

    assert [c.kind for c in found if "template" in c.kind] == [
        "freed-parameter-contradicts-template"
    ]


def test_a_thickness_constraint_beside_an_sld_one_is_not_flagged(tmp_path) -> None:
    """The regression case. The real promoted tNR fit carries
    `linear_in_time` on `Cu.thickness` *and* another on `CuOx.rho`; checking
    constraints one at a time flagged the rho one for ignoring a template the
    thickness one honours, so the correct, published fit was the thing the
    checker complained about.
    """
    constraints = (
        SERIES
        + "constraints:\n"
        + "  - {series: tnr, form: linear_in_time, paths: [Cu.thickness],"
        " from: s1, to: s1}\n"
        + "  - {series: tnr, form: linear_in_time, paths: [CuOx.rho],"
        " from: s1, to: s1}\n"
    )
    parameters = (
        DEFAULT_PARAMETERS
        + "  - {path: CuOx.rho, range: [3.5, 6.5], per: state}\n"
        + "  - {path: Cu.thickness, range: [470, 540], per: state}\n"
    )
    spec = spec_of(parameters=parameters, constraints=constraints, tmp_path=tmp_path)

    found = check(spec, tnr=assessment(implied="thickness")).contradictions

    assert [c for c in found if "template" in c.kind] == []


def test_a_constraint_through_a_flat_run_is_flagged(tmp_path) -> None:
    """Fitting a trend to noise produces one."""
    constraints = (
        SERIES
        + "constraints:\n"
        + "  - {series: tnr, form: linear_in_time, paths: [CuOx.thickness],"
        " from: s1, to: s1}\n"
    )
    spec = spec_of(constraints=constraints, tmp_path=tmp_path)

    found = check(spec, tnr=assessment(flat=True)).contradictions

    assert [c.kind for c in found if "flat" in c.kind] == ["constraint-on-a-flat-run"]


def test_no_assessment_means_no_opinion(tmp_path) -> None:
    """A checker that fires on incompleteness fires on every project
    mid-beamtime, and then nobody reads it."""
    constraints = (
        SERIES
        + "constraints:\n"
        + "  - {series: tnr, form: logistic, paths: [CuOx.rho],"
        " from: s1, to: s1, t_half: [0, 100], width: [1, 50]}\n"
    )
    parameters = (
        DEFAULT_PARAMETERS + "  - {path: CuOx.rho, range: [3.5, 6.5], per: state}\n"
    )
    spec = spec_of(parameters=parameters, constraints=constraints, tmp_path=tmp_path)

    assert check(spec, tnr=None).contradictions == []


# --------------------------------------------------------------------------
# Roughness coherence, on the ranges rather than the results
# --------------------------------------------------------------------------


def test_ranges_that_let_a_layer_vanish_are_flagged(tmp_path) -> None:
    """The real failure: an oxide whose two interfaces summed to 1.55x its own
    thickness, so its nominal SLD was attained nowhere in the profile. Checked
    on the declared ranges because "a range permitting it guarantees the fit
    can go there, and it will"."""
    parameters = """\
  - {path: CuOx.thickness, range: [10, 80], per: state}
  - {path: CuOx.roughness, range: [5, 35], per: state}
  - {path: Cu.roughness, range: [5, 30], per: state}
"""
    spec = spec_of(parameters=parameters, tmp_path=tmp_path)

    found = check(spec).contradictions

    flagged = [c for c in found if c.kind == "roughness-swallows-layer"]
    assert [c.subject for c in flagged] == ["CuOx"]
    # The evidence names both sides so a reader can check the arithmetic.
    assert "35" in flagged[0].evidence and "30" in flagged[0].evidence
    assert "10" in flagged[0].evidence
    assert "65" in flagged[0].message, "the sum is stated where it is read"


def test_coherent_ranges_are_not_flagged(tmp_path) -> None:
    parameters = """\
  - {path: CuOx.thickness, range: [40, 80], per: state}
  - {path: CuOx.roughness, range: [5, 11], per: state}
  - {path: Cu.roughness, range: [5, 11], per: state}
"""
    spec = spec_of(parameters=parameters, tmp_path=tmp_path)

    assert [c for c in check(spec).contradictions if "roughness" in c.kind] == []


def test_an_undeclared_roughness_is_read_from_the_stack(tmp_path) -> None:
    """The root cause of the real failure was a roughness never declared as a
    parameter at all, left at whatever the scaffold wrote."""
    parameters = "  - {path: CuOx.thickness, range: [10, 80], per: state}\n"
    text = (
        SPEC.replace("@PARAMETERS@", parameters)
        .replace("@CONSTRAINTS@", "")
        .replace(
            "{name: dTHF, thickness: 0, roughness: 5}",
            "{name: dTHF, thickness: 0, roughness: 20}",
        )
    )
    path = tmp_path / "m.yaml"
    path.write_text(text, encoding="utf-8")

    found = check(load_spec(path)).contradictions

    assert any(c.kind.startswith("roughness-") for c in found)


# --------------------------------------------------------------------------
# Fitted SLD against the range the spec itself declares
# --------------------------------------------------------------------------


def test_a_fitted_sld_outside_its_declared_range_is_flagged(tmp_path) -> None:
    parameters = (
        DEFAULT_PARAMETERS + "  - {path: CuOx.rho, range: [3.5, 6.5], per: state}\n"
    )
    spec = spec_of(parameters=parameters, tmp_path=tmp_path)

    found = check(spec, fitted={"s1 CuOx rho": 9.2}).contradictions

    assert [c.kind for c in found if "sld" in c.kind] == ["sld-outside-material-range"]


def test_a_value_just_inside_the_slack_is_not_flagged(tmp_path) -> None:
    """Fitting wanders; a real mismatch is a whole unit, not a rounding."""
    parameters = (
        DEFAULT_PARAMETERS + "  - {path: CuOx.rho, range: [3.5, 6.5], per: state}\n"
    )
    spec = spec_of(parameters=parameters, tmp_path=tmp_path)

    found = check(spec, fitted={"s1 CuOx rho": 6.6}).contradictions

    assert [c for c in found if "sld" in c.kind] == []


def test_nothing_is_claimed_about_an_undeclared_layer(tmp_path) -> None:
    """If nobody said what the layer is made of, the checker has no opinion
    about where its SLD may sit."""
    spec = spec_of(tmp_path=tmp_path)

    found = check(spec, fitted={"s1 Unknown rho": 99.0}).contradictions

    assert [c for c in found if "sld" in c.kind] == []


def test_no_fit_yet_means_no_sld_opinion(tmp_path) -> None:
    parameters = (
        DEFAULT_PARAMETERS + "  - {path: CuOx.rho, range: [3.5, 6.5], per: state}\n"
    )
    spec = spec_of(parameters=parameters, tmp_path=tmp_path)

    assert [c for c in check(spec).contradictions if "sld" in c.kind] == []


def test_the_report_reports_its_worst(tmp_path) -> None:
    parameters = """\
  - {path: CuOx.thickness, range: [10, 80], per: state}
  - {path: CuOx.roughness, range: [5, 35], per: state}
  - {path: Cu.roughness, range: [5, 30], per: state}
"""
    report = check(spec_of(parameters=parameters, tmp_path=tmp_path))

    assert report.worst == "warn"
    assert report.as_dict()["schema"] == "nrw-contradictions/1"


@pytest.mark.parametrize("value", [None, "unknown", ""])
def test_an_unknown_implied_change_is_no_opinion(tmp_path, value) -> None:
    constraints = (
        SERIES
        + "constraints:\n"
        + "  - {series: tnr, form: linear_in_time, paths: [CuOx.rho],"
        " from: s1, to: s1}\n"
    )
    parameters = (
        DEFAULT_PARAMETERS + "  - {path: CuOx.rho, range: [3.5, 6.5], per: state}\n"
    )
    spec = spec_of(parameters=parameters, constraints=constraints, tmp_path=tmp_path)

    found = check(spec, tnr=assessment(implied=value)).contradictions

    assert [c for c in found if "template" in c.kind] == []
