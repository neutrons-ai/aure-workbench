"""Tests for the nrw-model/1 schema, resolution, and constraint forms.

Resolution is pure Python, so most of this runs without refl1d.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from nr_workbench.spec.constraints import FORMS, ConstraintError, FormContext
from nr_workbench.spec.models import ModelSpec, ParameterPath, SpecError, load_spec
from nr_workbench.spec.resolve import Measurement, build_table

MINIMAL: dict[str, Any] = {
    "schema": "nrw-model/1",
    "name": "demo",
    "materials": {"Air": {"rho": 0.0}, "Film": {"rho": 4.0}, "Si": {"rho": 2.07}},
    "stack": [
        {"name": "Air", "thickness": 0, "roughness": 5},
        {"name": "Film", "thickness": 100, "roughness": 5},
        {"name": "Si"},
    ],
    "states": [
        {"name": "s1", "run": 1, "segments": [{"file": "a.txt", "theta": 0.45}]}
    ],
}


def spec_from(**overrides: Any) -> ModelSpec:
    """Build a spec from the minimal template plus overrides."""
    payload = {**MINIMAL, **overrides}
    return ModelSpec.model_validate(payload)


def measurements_for(
    spec: ModelSpec, counts: dict[str, int]
) -> dict[str, list[Measurement]]:
    """Fabricate measurements, bypassing the filesystem."""
    return {
        group: [
            Measurement(group, i, f"{group}_{i}.txt", 0.45, time=float(i * 100))
            for i in range(n)
        ]
        for group, n in counts.items()
    }


# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "owner", "attr", "state"),
    [
        ("Cu.thickness", "Cu", "thickness", None),
        ("CuOx.rho", "CuOx", "rho", None),
        ("probe.intensity", "probe", "intensity", None),
        ("Ti.roughness@ocv1", "Ti", "roughness", "ocv1"),
    ],
)
def test_parameter_path_parses(
    text: str, owner: str, attr: str, state: str | None
) -> None:
    path = ParameterPath.parse(text)

    assert (path.owner, path.attr, path.state) == (owner, attr, state)


@pytest.mark.parametrize(
    "text", ["Cu", "Cu.", ".thickness", "Cu thickness", "Cu.bogus"]
)
def test_parameter_path_rejects_nonsense(text: str) -> None:
    with pytest.raises(SpecError):
        ParameterPath.parse(text)


def test_roughness_is_the_friendly_name_for_interface() -> None:
    """The spec says `roughness`; refl1d calls it `interface`."""
    assert ParameterPath.parse("Cu.roughness").attr == "roughness"
    with pytest.raises(SpecError):
        ParameterPath.parse("Cu.interface")


# --------------------------------------------------------------------------
# Schema validation
# --------------------------------------------------------------------------


def test_unknown_key_is_rejected() -> None:
    """A typo must be an error, not silence."""
    with pytest.raises(Exception, match="rougness|extra"):
        spec_from(stack=[{"name": "Air", "rougness": 5}, {"name": "Si"}])


def test_parameter_without_bounds_is_rejected() -> None:
    """Silently freezing a parameter at its starting value is the worst outcome."""
    with pytest.raises(Exception, match="range|pm|fixed"):
        spec_from(parameters=[{"path": "Film.thickness", "per": "state"}])


def test_range_and_pm_together_are_rejected() -> None:
    with pytest.raises(Exception, match="not both"):
        spec_from(parameters=[{"path": "Film.thickness", "range": [1, 2], "pm": 5}])


def test_inverted_range_is_rejected() -> None:
    with pytest.raises(Exception, match="empty or inverted"):
        spec_from(parameters=[{"path": "Film.thickness", "range": [200, 100]}])


def test_duplicate_layer_names_are_rejected() -> None:
    with pytest.raises(Exception, match="duplicate layer"):
        spec_from(stack=[{"name": "Air"}, {"name": "Air"}, {"name": "Si"}])


def test_a_model_needs_a_state_or_series() -> None:
    with pytest.raises(Exception, match="at least one state or series"):
        spec_from(states=[])


def test_load_spec_reports_the_file_on_a_bad_document(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("schema: nrw-model/1\nname: x\n", encoding="utf-8")

    with pytest.raises(SpecError, match="bad.yaml"):
        load_spec(bad)


def test_round_trips_through_yaml() -> None:
    spec = spec_from()

    reloaded = ModelSpec.model_validate(
        yaml.safe_load(yaml.safe_dump(spec.to_yaml_dict()))
    )

    assert reloaded.name == spec.name
    assert reloaded.layer_names == spec.layer_names


# --------------------------------------------------------------------------
# Resolution and grouping
# --------------------------------------------------------------------------


def test_per_state_shares_one_parameter_across_segments() -> None:
    """The default that removes ~180 lines of hand-written aliasing."""
    spec = spec_from(
        parameters=[{"path": "Film.thickness", "range": [50, 200], "per": "state"}]
    )

    table = build_table(spec, measurements_for(spec, {"s1": 3}))

    assert table.n_free == 1
    assert {slot.ref for slot in table.slots} == {"Film.thickness@s1"}
    assert len(table.slots) == 3


def test_per_model_shares_across_every_group() -> None:
    spec = spec_from(
        states=[
            {"name": "a", "segments": [{"file": "a.txt", "theta": 0.45}]},
            {"name": "b", "segments": [{"file": "b.txt", "theta": 0.45}]},
        ],
        parameters=[{"path": "Film.rho", "range": [2, 6], "per": "model"}],
    )

    table = build_table(spec, measurements_for(spec, {"a": 2, "b": 2}))

    assert table.n_free == 1
    assert {slot.ref for slot in table.slots} == {"Film.rho@model"}


def test_per_measurement_creates_one_each() -> None:
    spec = spec_from(
        parameters=[
            {"path": "Film.thickness", "range": [50, 200], "per": "measurement"}
        ]
    )

    table = build_table(spec, measurements_for(spec, {"s1": 3}))

    assert table.n_free == 3


def test_measurement_targeting_overrides_the_group() -> None:
    """The pattern the reference needs: segments share, except the high-angle one.

    The hand-written script says it outright -- "The third run has a different
    intensity, so we don't share that parameter with the first two runs".
    """
    spec = spec_from(
        parameters=[
            {"path": "probe.intensity", "value": 1.0, "pm": 0.1, "per": "state"},
            {
                "path": "probe.intensity",
                "range": [0.5, 1.1],
                "per": "measurement",
                "in": ["s1#2"],
                "name": "special",
            },
        ]
    )

    table = build_table(spec, measurements_for(spec, {"s1": 3}))

    refs = [slot.ref for slot in sorted(table.slots, key=lambda s: s.measurement.index)]
    assert refs == ["probe.intensity@s1", "probe.intensity@s1", "probe.intensity@s1#2"]
    assert table.n_free == 2


def test_two_declarations_at_the_same_specificity_are_rejected() -> None:
    spec = spec_from(
        parameters=[
            {"path": "Film.thickness", "range": [50, 200], "per": "state"},
            {"path": "Film.thickness", "range": [10, 20], "per": "measurement"},
        ]
    )

    with pytest.raises(SpecError, match="same specificity"):
        build_table(spec, measurements_for(spec, {"s1": 2}))


def test_out_of_range_measurement_index_is_rejected() -> None:
    spec = spec_from(
        parameters=[
            {
                "path": "Film.thickness",
                "range": [50, 200],
                "per": "measurement",
                "in": ["s1#9"],
            }
        ]
    )

    with pytest.raises(SpecError, match="out of range"):
        build_table(spec, measurements_for(spec, {"s1": 2}))


def test_unknown_layer_gets_a_did_you_mean() -> None:
    spec = spec_from(parameters=[{"path": "Flim.thickness", "range": [1, 2]}])

    with pytest.raises(SpecError, match="Did you mean 'Film'"):
        build_table(spec, measurements_for(spec, {"s1": 1}))


def test_starting_value_comes_from_the_stack_not_the_range_midpoint() -> None:
    """The stack declares the guess; `range` declares where it may go."""
    spec = spec_from(parameters=[{"path": "Film.thickness", "range": [50, 200]}])

    table = build_table(spec, measurements_for(spec, {"s1": 1}))

    assert table.free[0].value == 100.0


def test_range_midpoint_is_used_when_the_stack_value_is_out_of_bounds() -> None:
    """bumps would reject a starting value outside its own bounds."""
    spec = spec_from(parameters=[{"path": "Film.thickness", "range": [300, 500]}])

    table = build_table(spec, measurements_for(spec, {"s1": 1}))

    assert table.free[0].value == 400.0


# --------------------------------------------------------------------------
# Constraints
# --------------------------------------------------------------------------


def series_spec(form: str, **constraint: Any) -> ModelSpec:
    """A two-state-plus-series spec carrying one constraint."""
    payload = {
        **MINIMAL,
        "states": [
            {"name": "a", "segments": [{"file": "a.txt", "theta": 0.45}]},
            {"name": "b", "segments": [{"file": "b.txt", "theta": 0.45}]},
        ],
        "series": [{"name": "t", "reduced_dir": "d", "theta": 0.6}],
        "parameters": [
            {
                "path": "Film.thickness",
                "range": [50, 200],
                "per": "state",
                "in": ["a", "b"],
            }
        ],
        "constraints": [
            {"series": "t", "form": form, "paths": ["Film.thickness"], **constraint}
        ],
    }
    return ModelSpec.model_validate(payload)


def test_linear_in_index_interpolates_between_the_endpoints() -> None:
    spec = series_spec("linear_in_index", **{"from": "a", "to": "b"})

    table = build_table(spec, measurements_for(spec, {"a": 1, "b": 1, "t": 5}))

    sources = [e.source for e in table.expressions]
    assert sources[0] == "P['Film.thickness@a']"
    assert sources[-1] == "P['Film.thickness@b']"
    assert "* 0.5" in sources[2]
    # The endpoints stay the only free parameters; the slices follow.
    assert table.n_free == 2


def test_linear_in_time_differs_from_index_when_spacing_is_uneven() -> None:
    """Not a cosmetic distinction: eis intervals run ~3x longer than holds."""
    spec = series_spec("linear_in_time", **{"from": "a", "to": "b"})
    uneven = measurements_for(spec, {"a": 1, "b": 1, "t": 3})
    uneven["t"] = [
        Measurement("t", 0, "0.txt", 0.6, time=0.0),
        Measurement("t", 1, "1.txt", 0.6, time=900.0),
        Measurement("t", 2, "2.txt", 0.6, time=1000.0),
    ]

    table = build_table(spec, uneven)

    # By index the middle slice would sit at 0.5; by time it is at 0.9.
    assert "0.9" in table.expressions[1].source


def test_logistic_introduces_a_midpoint_and_a_width() -> None:
    """The payoff of the amplitude analysis: t_half becomes fitted physics."""
    spec = series_spec(
        "logistic", **{"from": "a", "to": "b", "t_half": [0, 2000], "width": [10, 500]}
    )

    table = build_table(spec, measurements_for(spec, {"a": 1, "b": 1, "t": 5}))

    extras = [p.key for p in table.free if ":" in p.key]
    assert any(k.endswith(":t_half") for k in extras)
    assert any(k.endswith(":width") for k in extras)
    assert "pmath.exp" in table.expressions[0].source
    assert table.n_free == 4  # two endpoints plus t_half and width


def test_exponential_introduces_a_time_constant() -> None:
    spec = series_spec("exponential", **{"from": "a", "to": "b", "tau": [10, 5000]})

    table = build_table(spec, measurements_for(spec, {"a": 1, "b": 1, "t": 4}))

    assert any(p.key.endswith(":tau") for p in table.free)
    assert "pmath.exp" in table.expressions[0].source


def test_free_costs_one_parameter_per_slice_and_says_so() -> None:
    spec = series_spec("free")

    table = build_table(spec, measurements_for(spec, {"a": 1, "b": 1, "t": 6}))

    assert table.n_free == 2 + 6
    assert any("absorb noise" in w for w in table.warnings)


def test_fixed_costs_nothing() -> None:
    spec = series_spec("fixed", **{"from": "a"})

    table = build_table(spec, measurements_for(spec, {"a": 1, "b": 1, "t": 6}))

    assert table.n_free == 2
    assert all(e.source == "P['Film.thickness@a']" for e in table.expressions)


def test_a_constraint_anchored_on_a_constant_is_rejected() -> None:
    """Interpolating between two constants freezes every slice, invisibly.

    It looks like a working fit and is not one, so it has to be an error.
    """
    payload = {
        **MINIMAL,
        "states": [
            {"name": "a", "segments": [{"file": "a.txt", "theta": 0.45}]},
            {"name": "b", "segments": [{"file": "b.txt", "theta": 0.45}]},
        ],
        "series": [{"name": "t", "reduced_dir": "d"}],
        "parameters": [],
        "constraints": [
            {
                "series": "t",
                "form": "linear_in_index",
                "from": "a",
                "to": "b",
                "paths": ["Film.thickness"],
            }
        ],
    }
    spec = ModelSpec.model_validate(payload)

    with pytest.raises(SpecError, match="not a free parameter"):
        build_table(spec, measurements_for(spec, {"a": 1, "b": 1, "t": 3}))


def test_a_path_cannot_be_both_free_and_constrained() -> None:
    """The likeliest user error; whichever ran last would silently win."""
    payload = {
        **MINIMAL,
        "states": [
            {"name": "a", "segments": [{"file": "a.txt", "theta": 0.45}]},
            {"name": "b", "segments": [{"file": "b.txt", "theta": 0.45}]},
        ],
        "series": [{"name": "t", "reduced_dir": "d"}],
        "parameters": [
            {"path": "Film.thickness", "range": [50, 200], "per": "state"},
        ],
        "constraints": [
            {
                "series": "t",
                "form": "linear_in_index",
                "from": "a",
                "to": "b",
                "paths": ["Film.thickness"],
            }
        ],
    }
    spec = ModelSpec.model_validate(payload)

    with pytest.raises(SpecError, match="assigned twice"):
        build_table(spec, measurements_for(spec, {"a": 1, "b": 1, "t": 3}))


def test_glob_expands_against_the_stack() -> None:
    payload = {
        **MINIMAL,
        "states": [
            {"name": "a", "segments": [{"file": "a.txt", "theta": 0.45}]},
            {"name": "b", "segments": [{"file": "b.txt", "theta": 0.45}]},
        ],
        "series": [{"name": "t", "reduced_dir": "d"}],
        "parameters": [
            {
                "path": "Air.roughness",
                "range": [1, 20],
                "per": "state",
                "in": ["a", "b"],
            },
            {
                "path": "Film.roughness",
                "range": [1, 20],
                "per": "state",
                "in": ["a", "b"],
            },
            {
                "path": "Si.roughness",
                "range": [1, 20],
                "per": "state",
                "in": ["a", "b"],
            },
        ],
        "constraints": [
            {
                "series": "t",
                "form": "linear_in_index",
                "from": "a",
                "to": "b",
                "paths": ["*.roughness"],
            }
        ],
    }
    spec = ModelSpec.model_validate(payload)

    table = build_table(spec, measurements_for(spec, {"a": 1, "b": 1, "t": 4}))

    constrained = {e.key.split("@")[0] for e in table.expressions}
    assert constrained == {"Air.roughness", "Film.roughness", "Si.roughness"}


def test_glob_matching_nothing_is_rejected() -> None:
    spec = series_spec("linear_in_index", **{"from": "a", "to": "b"})
    spec.constraints[0].paths = ["Nope*.roughness"]

    with pytest.raises(SpecError, match="matched no layer"):
        build_table(spec, measurements_for(spec, {"a": 1, "b": 1, "t": 3}))


def test_unknown_form_is_rejected_with_the_list() -> None:
    with pytest.raises(Exception, match="unknown constraint form"):
        series_spec("wishful_thinking", **{"from": "a", "to": "b"})


@pytest.mark.parametrize("form", sorted(FORMS))
def test_every_form_has_a_summary(form: str) -> None:
    """`nrw model forms` must be able to describe all of them."""
    assert FORMS[form].summary


def test_interpolating_forms_require_both_endpoints() -> None:
    with pytest.raises(Exception, match="needs.*from.*to|`from` and `to`"):
        series_spec("linear_in_index", **{"from": "a"})


def test_form_context_fractions() -> None:
    context = FormContext(start_key="s", end_key="e", times=[0.0, 900.0, 1000.0], n=3)

    assert context.fraction_by_index(1) == pytest.approx(0.5)
    assert context.fraction_by_time(1) == pytest.approx(0.9)


def test_free_form_refuses_to_produce_an_expression() -> None:
    """It is handled by the resolver, so calling it here is a bug."""
    with pytest.raises(ConstraintError):
        FORMS["free"].expression(0, FormContext(start_key=None, end_key=None, n=1))


def test_theta_offset_is_rejected_on_a_combined_state(tmp_path) -> None:
    """A combined file has no single incident angle to offset.

    The reduction has already stitched it across every angle setting, so the
    parameter is meaningless -- and refl1d accepts it and fits it to something,
    which is the worst of the three possible behaviours. AuRE draws the same
    line: its nuisance keys are partials-only.
    """
    from nr_workbench.spec.models import load_spec
    from nr_workbench.spec.validate import validate_spec

    data = tmp_path / "samples" / "S1" / "data" / "steady"
    data.mkdir(parents=True)
    (data / "REFL_100001_combined_data_auto.txt").write_text(
        "0.01 1.0 0.1 0.001\n0.02 0.5 0.05 0.002\n"
    )
    spec_path = tmp_path / "spec.yaml"
    spec_path.write_text(
        "schema: nrw-model/1\nname: m\nsample: S1\n"
        "materials: {Si: {rho: 2.07}, Film: {rho: 4.0}}\n"
        "stack:\n"
        "  - {name: Film, material: Film, thickness: 100, roughness: 5}\n"
        "  - {name: Si, material: Si}\n"
        "probe: {resolution: angular_only}\n"
        "states:\n"
        "  - name: s1\n    run: 100001\n    kind: combined\n    segments: auto\n"
        "    thetas: [0.45]\n    data_dir: samples/S1/data/steady\n"
        "parameters:\n"
        "  - {path: Film.thickness, range: [50, 200], per: model}\n"
        "  - {path: probe.theta_offset, range: [-0.02, 0.02], per: state}\n",
        encoding="utf-8",
    )

    report = validate_spec(load_spec(spec_path), tmp_path)

    assert not report.ok
    assert any("no single incident angle" in error for error in report.errors)


def test_sample_broadening_is_accepted_on_a_per_angle_state(tmp_path) -> None:
    """It is only combined states that cannot have one."""
    import numpy as np

    from nr_workbench.spec.models import load_spec
    from nr_workbench.spec.validate import validate_spec

    data = tmp_path / "samples" / "S1" / "data" / "steady"
    data.mkdir(parents=True)
    q = np.linspace(0.01, 0.2, 20)
    r = 1e-3 * (0.01 / q) ** 4
    body = "\n".join(
        f"{a:.6e} {b:.6e} {c:.6e} {d:.6e}"
        for a, b, c, d in zip(q, r, 0.05 * r, 0.02 * q, strict=True)
    )
    (data / "REFL_100001_1_100001_partial.txt").write_text(body)

    spec_path = tmp_path / "spec.yaml"
    spec_path.write_text(
        "schema: nrw-model/1\nname: m\nsample: S1\n"
        "materials: {Si: {rho: 2.07}, Film: {rho: 4.0}}\n"
        "stack:\n"
        "  - {name: Film, material: Film, thickness: 100, roughness: 5}\n"
        "  - {name: Si, material: Si}\n"
        "probe: {resolution: angular_only}\n"
        "states:\n"
        "  - name: s1\n    run: 100001\n    segments: auto\n"
        "    thetas: [0.45]\n    data_dir: samples/S1/data/steady\n"
        "parameters:\n"
        "  - {path: Film.thickness, range: [50, 200], per: model}\n"
        "  - {path: probe.sample_broadening, range: [0.0, 0.05], per: state}\n"
        "  - {path: probe.theta_offset, range: [-0.02, 0.02], per: state}\n",
        encoding="utf-8",
    )

    report = validate_spec(load_spec(spec_path), tmp_path)

    assert report.ok, report.errors


def test_every_double_assignment_is_reported_at_once(tmp_path) -> None:
    """The mistake is made once, in one habit, and hits every parameter.

    Declaring structural parameters `per: state` with no `in:` scopes them to
    every group including the series. On a real five-layer model that was eight
    collisions -- and reporting the first and stopping turns one edit into
    eight validate-fix cycles.
    """
    import numpy as np

    from nr_workbench.spec.models import load_spec
    from nr_workbench.spec.validate import validate_spec

    steady = tmp_path / "samples" / "S1" / "data" / "steady"
    steady.mkdir(parents=True)
    tnr = tmp_path / "samples" / "S1" / "data" / "tnr" / "100003"
    tnr.mkdir(parents=True)

    q = np.linspace(0.01, 0.2, 20)
    r = 1e-3 * (0.01 / q) ** 4
    body = "\n".join(
        f"{a:.6e} {b:.6e} {c:.6e} {d:.6e}"
        for a, b, c, d in zip(q, r, 0.05 * r, 0.02 * q, strict=True)
    )
    for run in (100001, 100002):
        (steady / f"REFL_{run}_1_{run}_partial.txt").write_text(body)
    for seconds in (0, 240):
        (tnr / f"r100003_t{seconds:06d}.txt").write_text(body)

    spec_path = tmp_path / "spec.yaml"
    spec_path.write_text(
        "schema: nrw-model/1\nname: m\nsample: S1\n"
        "materials: {D2O: {rho: 6.36}, Cu: {rho: 6.55}, Si: {rho: 2.07}}\n"
        "stack:\n"
        "  - {name: D2O, material: D2O, thickness: 0, roughness: 5}\n"
        "  - {name: Cu, material: Cu, thickness: 500, roughness: 5}\n"
        "  - {name: Si, material: Si}\n"
        "probe: {resolution: angular_only}\n"
        "states:\n"
        "  - {name: ocv1, run: 100001, segments: auto, thetas: [0.45],\n"
        "     data_dir: samples/S1/data/steady}\n"
        "  - {name: ocv2, run: 100002, segments: auto, thetas: [0.45],\n"
        "     data_dir: samples/S1/data/steady}\n"
        "series:\n"
        "  - {name: tnr, run: 100003, reduced_dir: samples/S1/data/tnr/100003,\n"
        "     theta: 0.6, time_from: filename}\n"
        "parameters:\n"
        # three declarations, none scoped -- the real mistake
        "  - {path: Cu.thickness, range: [400, 600], per: state}\n"
        "  - {path: Cu.roughness, range: [1, 20], per: state}\n"
        "  - {path: D2O.roughness, range: [1, 20], per: state}\n"
        "constraints:\n"
        "  - series: tnr\n    form: linear_in_time\n    from: ocv1\n    to: ocv2\n"
        "    paths: [Cu.thickness, Cu.roughness, D2O.roughness]\n",
        encoding="utf-8",
    )

    report = validate_spec(load_spec(spec_path), tmp_path)

    assert not report.ok
    message = "\n".join(report.errors)
    assert "3 paths assigned twice" in message
    for path in ("Cu.thickness", "Cu.roughness", "D2O.roughness"):
        assert path in message, f"{path} was not reported"
    # and it names the fix that is actually right
    assert "in: [ocv1, ocv2]" in message


# --------------------------------------------------------------------------
# probe.dq_scale
# --------------------------------------------------------------------------


def test_dq_scale_defaults_to_one() -> None:
    from nr_workbench.spec.models import Probe

    assert Probe().dq_scale == 1.0


@pytest.mark.parametrize("value", [0, -1.0])
def test_a_non_positive_dq_scale_is_rejected(value: float) -> None:
    """It multiplies a width; zero makes every point infinitely sharp."""
    from nr_workbench.spec.models import Probe, SpecError

    with pytest.raises((SpecError, Exception), match="must be positive|dq_scale"):
        Probe(dq_scale=value)


@pytest.mark.parametrize(
    "attr", ["dq_scale", "dq_is_fwhm", "back_reflection", "resolution"]
)
def test_a_probe_setting_that_cannot_be_fitted_says_why(attr: str) -> None:
    """These are settable under `probe:` and not fittable, and being told only
    that the name is invalid reads as a typo rather than a category error."""
    from nr_workbench.spec.models import ParameterPath, SpecError

    with pytest.raises(SpecError) as caught:
        ParameterPath.parse(f"probe.{attr}")

    assert "not valid for probe" in str(caught.value)
    assert "under `probe:`" in str(caught.value), "the message must say where to set it"


def test_fitting_a_resolution_is_pointed_at_sample_broadening() -> None:
    """The fittable route exists; the error has to name it."""
    from nr_workbench.spec.models import ParameterPath, SpecError

    with pytest.raises(SpecError) as caught:
        ParameterPath.parse("probe.dq_scale")

    message = str(caught.value)
    assert "sample_broadening" in message
    assert "per: measurement" in message


# --------------------------------------------------------------------------
# trim
# --------------------------------------------------------------------------


def test_a_trim_needs_a_reason() -> None:
    """A cut nobody explained reads as an error six months later."""
    from nr_workbench.spec.models import SpecError, Trim

    with pytest.raises((SpecError, Exception), match="reason"):
        Trim(lambda_min=3.5)


def test_a_trim_needs_at_least_one_bound() -> None:
    from nr_workbench.spec.models import SpecError, Trim

    with pytest.raises((SpecError, Exception), match="at least one"):
        Trim(reason="cutting nothing at all")


def test_an_inverted_range_is_rejected() -> None:
    """As written it keeps nothing, which is never what was meant."""
    from nr_workbench.spec.models import SpecError, Trim

    with pytest.raises((SpecError, Exception), match="must be below"):
        Trim(q_min=0.2, q_max=0.02, reason="inverted")


def test_only_the_bounds_that_were_set_are_passed_on() -> None:
    from nr_workbench.spec.models import Trim

    assert Trim(lambda_min=3.5, reason="band edge").as_kwargs() == {"lambda_min": 3.5}


def test_a_trim_naming_an_unknown_state_is_rejected() -> None:
    """The same treatment `parameters` gets: a typo must not silently do nothing."""
    from nr_workbench.spec.models import SpecError
    from nr_workbench.spec.resolve import Measurement, trim_for

    spec = spec_from(trim=[{"in": ["nosuchstate"], "q_max": 0.2, "reason": "typo"}])

    with pytest.raises(SpecError, match="unknown state/series"):
        trim_for(spec, Measurement(group="s1", index=0, file="f", theta=0.45))


# --------------------------------------------------------------------------
# per: angle
#
# Aperture-limited divergence belongs to the incident angle, not the sample or
# the run. On three states x three angles that is three parameters, where
# `per: measurement` is nine of the same physical quantity and `per: state`
# cannot express it at all.
# --------------------------------------------------------------------------


def angled(**counts_by_theta: list[float]) -> dict[str, list[Any]]:
    """Measurements for several groups, each with the given thetas."""
    from nr_workbench.spec.resolve import Measurement

    return {
        group: [
            Measurement(group=group, index=i, file=f"{group}{i}.txt", theta=theta)
            for i, theta in enumerate(thetas)
        ]
        for group, thetas in counts_by_theta.items()
    }


def test_one_parameter_per_angle_shared_across_states() -> None:
    from nr_workbench.spec.resolve import build_table

    spec = spec_from(
        states=[
            {"name": "s1", "run": 1, "segments": [{"file": "a.txt", "theta": 0.45}]},
            {"name": "s2", "run": 2, "segments": [{"file": "b.txt", "theta": 0.45}]},
        ],
        parameters=[
            {"path": "probe.sample_broadening", "range": [0, 0.15], "per": "angle"}
        ],
    )

    table = build_table(
        spec, angled(s1=[0.37, 1.2002, 3.5001], s2=[0.3698, 1.2001, 3.5001])
    )

    broadening = [p for p in table.free if "sample_broadening" in p.key]
    assert len(broadening) == 3, "three angles, not six measurements"


def test_thetas_read_from_different_files_group_together() -> None:
    """Each theta comes from its own header, so the same setting is recorded
    slightly differently per run. Grouping has to see through that."""
    from nr_workbench.spec.resolve import angle_groups

    labels = angle_groups(angled(s1=[0.37, 1.2002], s2=[0.3698, 1.2001]))

    assert labels["s1#0"] == labels["s2#0"]
    assert labels["s1#1"] == labels["s2#1"]
    assert labels["s1#0"] != labels["s1#1"]


def test_genuinely_different_angles_stay_apart() -> None:
    from nr_workbench.spec.resolve import angle_groups

    labels = angle_groups(angled(s1=[0.45, 1.2, 3.5]))

    assert len({labels[k] for k in labels}) == 3


def test_the_label_does_not_move_when_a_state_is_added() -> None:
    """The label becomes a parameter key, recorded in the .par file and the
    posterior. A key that shifts when another run joins makes two fits of one
    model look like fits of two -- and the cluster minimum shifts, because a
    marginally lower theta can arrive later."""
    from nr_workbench.spec.resolve import angle_groups

    before = angle_groups(angled(s1=[0.37]))
    after = angle_groups(angled(s1=[0.37], s2=[0.3698]))

    assert before["s1#0"] == after["s1#0"] == "0.37deg"


def test_angles_too_close_to_name_apart_keep_full_precision() -> None:
    """Rather than silently merging two settings into one parameter."""
    from nr_workbench.spec.resolve import angle_groups

    labels = angle_groups(angled(s1=[1.20, 1.23]))

    assert len({labels[k] for k in labels}) == 2
