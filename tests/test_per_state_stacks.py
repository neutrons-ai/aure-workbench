"""A state that declares its own stack: the schema, and what resolves from it.

The case this exists for is a layer present in one state and absent from
another -- a native oxide reduced away under potential. Sharing is by layer
name, so what has to hold is that a layer reaches exactly the states built
with it, starts from each state's own stack, and is refused anywhere a state
built without it is named.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from nr_workbench.spec.models import ModelSpec, SpecError
from nr_workbench.spec.resolve import Measurement, build_table

#: Copper with its native oxide at OCV, and the oxide gone under potential.
#: The reduced state's copper is thicker: reducing oxide makes copper.
OXIDE: dict[str, Any] = {
    "schema": "nrw-model/1",
    "name": "oxide",
    "materials": {
        "D2O": {"rho": 6.3},
        "CuOx": {"rho": 5.8},
        "Cu": {"rho": 6.5},
        "Ti": {"rho": -1.9},
        "Si": {"rho": 2.07},
    },
    "stack": [
        {"name": "D2O", "thickness": 0, "roughness": 5},
        {"name": "CuOx", "thickness": 50, "roughness": 10},
        {"name": "Cu", "thickness": 350, "roughness": 5},
        {"name": "Ti", "thickness": 30, "roughness": 5},
        {"name": "Si"},
    ],
    "states": [
        {"name": "ocv", "segments": [{"file": "ocv.txt", "theta": 0.45}]},
        {
            "name": "reduced",
            "segments": [{"file": "reduced.txt", "theta": 0.45}],
            "stack": [
                {"name": "D2O", "thickness": 0, "roughness": 5},
                {"name": "Cu", "thickness": 358, "roughness": 5},
                {"name": "Ti", "thickness": 30, "roughness": 5},
                {"name": "Si"},
            ],
        },
    ],
}


def oxide(**overrides: Any) -> dict[str, Any]:
    """A deep copy of :data:`OXIDE` with top-level keys replaced."""
    return {**copy.deepcopy(OXIDE), **overrides}


def table_for(payload: dict[str, Any], counts: dict[str, int] | None = None):
    """Resolve *payload* against fabricated measurements, one per group by default."""
    spec = ModelSpec.model_validate(payload)
    counts = counts or dict.fromkeys(spec.group_names, 1)
    measurements = {
        group: [
            Measurement(group, i, f"{group}{i}.txt", 0.45, time=float(i * 100))
            for i in range(n)
        ]
        for group, n in counts.items()
    }
    return build_table(spec, measurements)


def keys(table) -> set[str]:
    """Every free-parameter key in a table."""
    return {parameter.key for parameter in table.free}


# --------------------------------------------------------------------------
# Item 1: the schema
# --------------------------------------------------------------------------


def test_stack_for_gives_a_state_its_own_and_everything_else_the_top_level() -> None:
    payload = oxide(series=[{"name": "tnr", "reduced_dir": "d"}])
    spec = ModelSpec.model_validate(payload)

    assert [layer.name for layer in spec.stack_for("ocv")] == spec.layer_names
    assert [layer.name for layer in spec.stack_for("reduced")] == [
        "D2O",
        "Cu",
        "Ti",
        "Si",
    ]
    assert spec.stack_for("tnr") is spec.stack


def test_stack_for_refuses_a_name_that_is_neither_a_state_nor_a_series() -> None:
    spec = ModelSpec.model_validate(OXIDE)

    with pytest.raises(SpecError, match="no state or series named 'ocv2'"):
        spec.stack_for("ocv2")


def test_layer_looks_in_the_stack_of_the_group_named() -> None:
    spec = ModelSpec.model_validate(OXIDE)

    assert spec.layer("CuOx") is not None
    assert spec.layer("CuOx", "ocv") is not None
    assert spec.layer("CuOx", "reduced") is None
    assert spec.layer("Cu", "reduced").thickness == 358


def test_stacks_lists_each_distinct_stack_with_the_groups_built_from_it() -> None:
    payload = oxide()
    # A third state declaring a copy of the reduced stack is built from it.
    payload["states"].append(
        {
            "name": "reduced2",
            "segments": [{"file": "r2.txt", "theta": 0.45}],
            "stack": copy.deepcopy(payload["states"][1]["stack"]),
        }
    )
    spec = ModelSpec.model_validate(payload)

    stacks = spec.stacks()

    assert [groups for _, groups in stacks] == [["ocv"], ["reduced", "reduced2"]]
    assert stacks[0][0] is spec.stack


def test_a_state_declaring_a_copy_of_the_top_level_stack_shares_it() -> None:
    payload = oxide()
    payload["states"][1]["stack"] = copy.deepcopy(payload["stack"])
    spec = ModelSpec.model_validate(payload)

    assert [groups for _, groups in spec.stacks()] == [["ocv", "reduced"]]


def test_the_top_level_stack_is_listed_even_when_no_group_uses_it() -> None:
    payload = oxide()
    payload["states"][0]["stack"] = copy.deepcopy(payload["states"][1]["stack"])
    spec = ModelSpec.model_validate(payload)

    assert [groups for _, groups in spec.stacks()] == [[], ["ocv", "reduced"]]
    assert "CuOx" not in spec.layers_in_use()


def test_layers_in_use_is_the_union_in_order_of_first_appearance() -> None:
    payload = oxide()
    payload["states"][1]["stack"].insert(
        1, {"name": "SEI", "thickness": 20, "roughness": 5}
    )
    payload["materials"]["SEI"] = {"rho": 2.0}
    spec = ModelSpec.model_validate(payload)

    assert spec.layers_in_use() == ["D2O", "CuOx", "Cu", "Ti", "Si", "SEI"]


def test_a_state_stack_needs_two_ends_and_the_error_names_the_state() -> None:
    payload = oxide()
    payload["states"][1]["stack"] = [{"name": "Si"}]

    with pytest.raises(Exception, match="state 'reduced'.*at least an ambient"):
        ModelSpec.model_validate(payload)


def test_a_state_stack_naming_a_layer_twice_is_refused_naming_the_state() -> None:
    payload = oxide()
    payload["states"][1]["stack"].insert(1, {"name": "Cu", "thickness": 5})

    with pytest.raises(Exception, match=r"duplicate layer.*state 'reduced'.*\['Cu'\]"):
        ModelSpec.model_validate(payload)


def test_a_series_may_not_declare_a_stack() -> None:
    payload = oxide(
        series=[{"name": "tnr", "reduced_dir": "d", "stack": OXIDE["stack"]}]
    )

    with pytest.raises(Exception, match="series 'tnr'.*always uses the top-level"):
        ModelSpec.model_validate(payload)


def test_a_state_stack_round_trips_and_an_absent_one_is_not_written() -> None:
    spec = ModelSpec.model_validate(OXIDE)

    written = spec.to_yaml_dict()

    assert "stack" not in written["states"][0]
    assert [layer["name"] for layer in written["states"][1]["stack"]] == [
        "D2O",
        "Cu",
        "Ti",
        "Si",
    ]
    assert ModelSpec.model_validate(written) == spec


def test_the_bundled_schema_admits_a_state_stack() -> None:
    from nr_workbench.spec.schema import build_schema

    state = build_schema()["$defs"]["State"]["properties"]

    assert "stack" in state


# --------------------------------------------------------------------------
# Item 2: resolution
# --------------------------------------------------------------------------


def test_a_layer_path_with_no_in_reaches_only_the_states_built_with_it() -> None:
    payload = oxide(
        parameters=[
            {"path": "CuOx.thickness", "range": [10, 100], "per": "state"},
            {"path": "CuOx.rho", "range": [4, 6.5], "per": "model"},
            {"path": "Cu.thickness", "range": [300, 400], "per": "state"},
        ]
    )

    table = table_for(payload)

    assert keys(table) == {
        "CuOx.thickness@ocv",
        "CuOx.rho@model",
        "Cu.thickness@ocv",
        "Cu.thickness@reduced",
    }
    reduced = {s.path.render() for s in table.slots if s.measurement.group == "reduced"}
    assert reduced == {"Cu.thickness"}


@pytest.mark.parametrize("per", ["model", "state", "angle", "measurement"])
def test_the_presence_rule_holds_for_every_scope(per: str) -> None:
    payload = oxide(
        parameters=[{"path": "CuOx.roughness", "range": [3, 30], "per": per}]
    )

    table = table_for(payload)

    assert {s.measurement.group for s in table.slots} == {"ocv"}


def test_a_probe_path_still_reaches_every_state() -> None:
    payload = oxide(parameters=[{"path": "probe.intensity", "pm": 0.1, "per": "state"}])

    assert keys(table_for(payload)) == {
        "probe.intensity@ocv",
        "probe.intensity@reduced",
    }


def test_in_naming_a_state_built_without_the_layer_is_an_error() -> None:
    payload = oxide(
        parameters=[
            {
                "path": "CuOx.thickness",
                "range": [10, 100],
                "per": "state",
                "in": ["ocv", "reduced"],
            }
        ]
    )

    with pytest.raises(SpecError, match="state 'reduced', whose stack has no layer"):
        table_for(payload)


def test_in_naming_one_measurement_of_such_a_state_is_an_error_too() -> None:
    payload = oxide(
        parameters=[
            {
                "path": "CuOx.thickness",
                "range": [10, 100],
                "per": "measurement",
                "in": ["reduced#0"],
            }
        ]
    )

    with pytest.raises(SpecError, match="whose stack has no layer 'CuOx'"):
        table_for(payload)


def test_starting_values_come_from_each_states_own_stack() -> None:
    payload = oxide(
        parameters=[{"path": "Cu.thickness", "range": [300, 400], "per": "state"}]
    )

    table = table_for(payload)

    assert table.free_by_key("Cu.thickness@ocv").value == 350
    assert table.free_by_key("Cu.thickness@reduced").value == 358


@pytest.mark.parametrize("per", ["model", "angle"])
def test_a_shared_parameter_whose_stacks_disagree_on_its_start_is_an_error(
    per: str,
) -> None:
    payload = oxide(
        parameters=[{"path": "Cu.thickness", "range": [300, 400], "per": per}]
    )

    with pytest.raises(SpecError, match=r"350 in ocv; 358 in reduced.*`value:`"):
        table_for(payload)


def test_a_value_settles_a_shared_parameter_across_disagreeing_stacks() -> None:
    payload = oxide(
        parameters=[
            {"path": "Cu.thickness", "value": 354, "range": [300, 400], "per": "model"}
        ]
    )

    table = table_for(payload)

    assert table.free_by_key("Cu.thickness@model").value == 354
    groups = {s.measurement.group for s in table.slots if s.ref == "Cu.thickness@model"}
    assert groups == {"ocv", "reduced"}


def test_a_fixed_number_needs_no_start_but_fixed_true_does() -> None:
    pinned = oxide(parameters=[{"path": "Cu.thickness", "fixed": 354, "per": "model"}])
    assert table_for(pinned).free_by_key("Cu.thickness@model").value == 354

    held = oxide(parameters=[{"path": "Cu.thickness", "fixed": True, "per": "model"}])
    with pytest.raises(SpecError, match="different values"):
        table_for(held)


def test_stacks_that_agree_on_a_start_share_a_parameter_without_a_value() -> None:
    payload = oxide(parameters=[{"path": "Ti.thickness", "pm": 2, "per": "model"}])

    table = table_for(payload)

    assert table.free_by_key("Ti.thickness@model").value == 30


def test_a_layer_only_a_state_stack_has_is_a_valid_path() -> None:
    payload = oxide()
    payload["states"][1]["stack"].insert(
        1, {"name": "SEI", "thickness": 20, "roughness": 5}
    )
    payload["materials"]["SEI"] = {"rho": 2.0}
    payload["parameters"] = [
        {"path": "SEI.thickness", "range": [5, 50], "per": "state"}
    ]

    table = table_for(payload)

    assert keys(table) == {"SEI.thickness@reduced"}
    assert table.free_by_key("SEI.thickness@reduced").value == 20


def test_a_layer_in_no_stack_is_still_refused() -> None:
    payload = oxide(parameters=[{"path": "SEI.thickness", "range": [5, 50]}])

    with pytest.raises(SpecError, match="not in any state's stack"):
        table_for(payload)


def test_a_constraint_anchored_at_a_state_without_the_layer_says_so() -> None:
    payload = oxide(
        series=[{"name": "tnr", "reduced_dir": "d"}],
        parameters=[
            {
                "path": "CuOx.thickness",
                "range": [10, 100],
                "per": "state",
                "in": ["ocv"],
            }
        ],
        constraints=[
            {
                "series": "tnr",
                "form": "linear_in_time",
                "from": "ocv",
                "to": "reduced",
                "paths": ["CuOx.thickness"],
            }
        ],
    )

    with pytest.raises(
        SpecError, match="anchors at state 'reduced', whose stack has no layer 'CuOx'"
    ):
        table_for(payload, {"ocv": 1, "reduced": 1, "tnr": 3})


def test_a_constraint_on_a_layer_the_series_lacks_names_the_series() -> None:
    payload = oxide()
    payload["states"][1]["stack"].insert(
        1, {"name": "SEI", "thickness": 20, "roughness": 5}
    )
    payload["materials"]["SEI"] = {"rho": 2.0}
    payload["series"] = [{"name": "tnr", "reduced_dir": "d"}]
    payload["constraints"] = [
        {"series": "tnr", "form": "fixed", "from": "reduced", "paths": ["SEI.rho"]}
    ]

    with pytest.raises(SpecError, match="not in the stack 'tnr' is built from"):
        table_for(payload, {"ocv": 1, "reduced": 1, "tnr": 3})


def test_the_double_assignment_hint_scopes_each_path_to_states_with_its_layer() -> None:
    """Advising `in: [ocv, reduced]` for CuOx would be advice the spec refuses."""
    payload = oxide(
        series=[{"name": "tnr", "reduced_dir": "d"}],
        parameters=[
            {"path": "CuOx.thickness", "range": [10, 100], "per": "state"},
            {"path": "Cu.thickness", "range": [300, 400], "per": "state"},
        ],
        constraints=[
            {
                "series": "tnr",
                "form": "linear_in_time",
                "from": "ocv",
                "to": "ocv",
                "paths": ["CuOx.thickness", "Cu.thickness"],
            }
        ],
    )

    with pytest.raises(SpecError) as caught:
        table_for(payload, {"ocv": 1, "reduced": 1, "tnr": 3})

    message = str(caught.value)
    assert "{path: CuOx.thickness, ..., per: state, in: [ocv]}" in message
    assert "{path: Cu.thickness, ..., per: state, in: [ocv, reduced]}" in message


# --------------------------------------------------------------------------
# Item 4: validation, per stack
# --------------------------------------------------------------------------

#: Si -> Cu is a contrast of 4.4, so a back-reflection plateau ends near 0.015.
BACK_QC = 0.0150

#: The cell measured through the wafer, oxide on the copper.
CELL = [
    {"name": "D2O", "thickness": 0, "roughness": 10},
    {"name": "CuOx", "thickness": 40, "roughness": 8},
    {"name": "Cu", "thickness": 400, "roughness": 8},
    {"name": "Si"},
]
#: The same cell with the oxide reduced away.
BARE = [layer for layer in CELL if layer["name"] != "CuOx"]


def _curve(path, *, qc: float) -> None:
    """A curve with a total-reflection plateau ending near ``qc``."""
    import numpy as np

    path.parent.mkdir(parents=True, exist_ok=True)
    q = np.linspace(0.005, 0.08, 200)
    r = np.where(q < qc, 0.85, 0.85 * (qc / q) ** 4)
    np.savetxt(path, np.column_stack([q, r, r * 0.05, q * 0.02]))


def cell_project(
    tmp_path, *, ocv=None, reduced=BARE, probe=None, parameters=None, edges=None
):
    """A project with an OCV and a reduced state, each with its own curve.

    Args:
        ocv: The OCV state's own stack, or ``None`` to inherit the top level.
        reduced: The reduced state's own stack, or ``None`` to inherit.
        edges: Where each state's plateau ends, by state; the Si one by default.
    """
    (tmp_path / "nrw.toml").write_text("", encoding="utf-8")
    states = []
    for name, stack in (("ocv", ocv), ("reduced", reduced)):
        file = f"data/{name}.txt"
        _curve(tmp_path / file, qc=(edges or {}).get(name, BACK_QC))
        state: dict[str, Any] = {
            "name": name,
            "segments": [{"file": file, "theta": 0.45}],
        }
        if stack is not None:
            state["stack"] = copy.deepcopy(stack)
        states.append(state)
    payload = {
        "schema": "nrw-model/1",
        "name": "cell",
        "materials": {
            "D2O": {"rho": 6.36},
            "CuOx": {"rho": 5.8},
            "Cu": {"rho": 6.55},
            "Si": {"rho": 2.07},
        },
        "stack": copy.deepcopy(CELL),
        "probe": {"resolution": "angular_only", **(probe or {})},
        "states": states,
        "parameters": parameters
        if parameters is not None
        else [{"path": "Cu.thickness", "range": [100, 600], "per": "state"}],
    }
    return tmp_path, ModelSpec.model_validate(payload)


def test_validate_says_which_states_declare_a_stack_and_which_inherit(tmp_path) -> None:
    from nr_workbench.spec.validate import validate_spec

    report = validate_spec(*reversed(cell_project(tmp_path)))

    assert report.ok, report.errors
    assert "stacks: reduced declares its own; ocv inherits the top-level stack" in (
        report.info
    )


def test_validate_says_nothing_about_stacks_when_there_is_one(tmp_path) -> None:
    from nr_workbench.spec.validate import validate_spec

    report = validate_spec(*reversed(cell_project(tmp_path, reduced=None)))

    assert not any(line.startswith("stacks:") for line in report.info)
    assert sum(line.startswith("beam enters") for line in report.info) == 1


def test_a_top_level_stack_no_group_is_built_from_is_a_warning(tmp_path) -> None:
    from nr_workbench.spec.validate import validate_spec

    report = validate_spec(*reversed(cell_project(tmp_path, ocv=BARE)))

    assert report.ok, report.errors
    assert any(
        "no state or series is built from the top-level" in w for w in report.warnings
    )
    assert (
        "stacks: ocv, reduced declare their own; none inherits the top-level stack"
        in (report.info)
    )


def test_a_state_stack_naming_an_unknown_material_is_an_error(tmp_path) -> None:
    from nr_workbench.spec.validate import validate_spec

    reduced = [*copy.deepcopy(BARE[:1]), {"name": "Cu2O", "thickness": 10}, *BARE[1:]]
    report = validate_spec(*reversed(cell_project(tmp_path, reduced=reduced)))

    assert any(
        "layer 'Cu2O' in the stack of reduced uses material 'Cu2O'" in e
        for e in report.errors
    )


def test_held_at_defaults_is_reported_per_stack(tmp_path) -> None:
    from nr_workbench.spec.validate import HELD_AT_DEFAULTS, validate_spec

    report = validate_spec(*reversed(cell_project(tmp_path)))

    held = [line for line in report.info if line.startswith(HELD_AT_DEFAULTS)]
    assert len(held) == 2
    ocv, reduced = held
    assert ocv.endswith("(in the stack of ocv)") and "CuOx.thickness=40" in ocv
    assert reduced.endswith("(in the stack of reduced)") and "CuOx" not in reduced


def test_an_attribute_fitted_in_one_stack_is_held_in_another(tmp_path) -> None:
    """Touched is per stack: a Cu thickness fitted only at OCV is still held
    in the reduced state's stack, at that stack's own value."""
    from nr_workbench.spec.validate import HELD_AT_DEFAULTS, validate_spec

    parameters = [
        {"path": "Cu.thickness", "range": [100, 600], "per": "state", "in": ["ocv"]}
    ]
    reduced = copy.deepcopy(BARE)
    reduced[1]["thickness"] = 410
    root, spec = cell_project(tmp_path, reduced=reduced, parameters=parameters)
    report = validate_spec(spec, root)

    ocv, bare = [line for line in report.info if line.startswith(HELD_AT_DEFAULTS)]
    assert "Cu.thickness" not in ocv
    assert "Cu.thickness=410" in bare


def test_two_stacks_entering_from_opposite_sides_is_an_error(tmp_path) -> None:
    from nr_workbench.spec.validate import validate_spec

    root, spec = cell_project(tmp_path, reduced=list(reversed(BARE)))
    report = validate_spec(spec, root)

    assert any(
        "stacks disagree about which side the beam enters" in e
        and "the stack of ocv puts Si last" in e
        and "the stack of reduced puts D2O last" in e
        for e in report.errors
    )


def test_a_state_stack_is_checked_against_its_own_states_data(tmp_path) -> None:
    """The reduced state's curve shows the Si plateau; its own stack, written
    upside down, predicts none there. The OCV curve shows no plateau at all,
    so only the reduced state's own data can say so."""
    from nr_workbench.spec.validate import validate_spec

    root, spec = cell_project(
        tmp_path, reduced=list(reversed(BARE)), edges={"ocv": 0.0031}
    )
    report = validate_spec(spec, root)

    assert any(
        "data contradict the stack order (the stack of reduced)" in e
        for e in report.errors
    )
    assert sum(line.startswith("beam enters") for line in report.info) == 2


def test_a_declared_geometry_is_checked_against_every_stack(tmp_path) -> None:
    from nr_workbench.spec.validate import validate_spec

    root, spec = cell_project(
        tmp_path, reduced=list(reversed(BARE)), probe={"back_reflection": True}
    )
    report = validate_spec(spec, root)

    assert any(
        "probe.back_reflection is true, but the stack (the stack of reduced)" in e
        for e in report.errors
    )
    assert not any("the stack (the stack of ocv)" in e for e in report.errors)


# --------------------------------------------------------------------------
# Item 5: preview and model.md
# --------------------------------------------------------------------------


def _write_spec(root, spec: ModelSpec):
    """Write *spec* where `nrw model` commands find it."""
    import yaml

    path = root / "models" / "cell.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(spec.to_yaml_dict(), sort_keys=False), "utf-8")
    return path


def _preview(path, *flags: str) -> str:
    from click.testing import CliRunner

    from nr_workbench.cli import main

    result = CliRunner().invoke(main, ["model", "preview", str(path), *flags])
    assert result.exit_code == 0, result.output
    return result.output


def test_preview_json_carries_every_stack(tmp_path) -> None:
    import json

    root, spec = cell_project(tmp_path)
    path = _write_spec(root, spec)

    payload = json.loads(_preview(path, "--json"))

    assert payload["stacks"] == [
        {"layers": ["D2O", "CuOx", "Cu", "Si"], "groups": ["ocv"], "top_level": True},
        {"layers": ["D2O", "Cu", "Si"], "groups": ["reduced"], "top_level": False},
    ]


def test_preview_json_carries_stacks_for_a_one_stack_spec_too(tmp_path) -> None:
    import json

    root, spec = cell_project(tmp_path, reduced=None)
    payload = json.loads(_preview(_write_spec(root, spec), "--json"))

    assert payload["stacks"] == [
        {
            "layers": ["D2O", "CuOx", "Cu", "Si"],
            "groups": ["ocv", "reduced"],
            "top_level": True,
        }
    ]


def test_preview_lists_the_stacks_only_when_a_state_declares_one(tmp_path) -> None:
    (tmp_path / "per").mkdir()
    (tmp_path / "one").mkdir()
    root, spec = cell_project(tmp_path / "per")
    shown = _preview(_write_spec(root, spec))
    assert "stacks (incident medium last):" in shown
    assert "D2O|CuOx|Cu|Si  ocv" in shown
    assert "D2O|Cu|Si       reduced" in shown

    root, spec = cell_project(tmp_path / "one", reduced=None)
    assert "stacks" not in _preview(_write_spec(root, spec))


def _explained(root, spec) -> str:
    from nr_workbench.spec.explain import explain
    from nr_workbench.spec.resolve import discover_measurements

    return explain(build_table(spec, discover_measurements(spec, root)))


def test_model_md_shows_each_stack_and_whom_it_builds(tmp_path) -> None:
    document = _explained(*cell_project(tmp_path))

    assert "### The top-level stack, for `ocv`" in document
    assert "### The stack declared by `reduced`" in document
    declared = document.split("### The stack declared by `reduced`", 1)[1]
    table = declared.split("\n\n## ", 1)[0]
    rows = [line for line in table.splitlines() if line.startswith("| `")]
    assert [row.split("`")[1] for row in rows] == ["D2O", "Cu", "Si"]
    assert rows[1].endswith("| 400 | 8 |")


def test_model_md_has_one_table_when_no_state_declares_a_stack(tmp_path) -> None:
    document = _explained(*cell_project(tmp_path, reduced=None))

    assert "### The top-level stack" not in document
    assert document.count("| Layer | Material |") == 1


def test_model_md_notes_a_thin_layer_only_a_state_stack_has(tmp_path) -> None:
    reduced = [*copy.deepcopy(BARE[:1]), {"name": "SEI", "thickness": 12}, *BARE[1:]]
    root, spec = cell_project(tmp_path, reduced=reduced)
    spec.materials["SEI"] = spec.materials["CuOx"].model_copy()

    document = _explained(root, spec)

    assert "`SEI` (12 Å)" in document
    # Held at its starting values: nothing fits it.
    held = document.split("### Held at their starting values", 1)[1]
    assert "`SEI`" in held.split("##", 1)[0]
