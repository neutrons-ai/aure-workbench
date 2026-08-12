"""Which side the beam enters from, and catching a stack that says otherwise.

The costliest error this package has seen, twice: an inverted stack fits,
converges, and reports a chi-squared in the hundreds with nothing naming the
cause. Two samples each burned their first two fits on it -- 105 and 168 on one,
139 and 162 on the other -- because the spec called the stack "ambient first,
substrate last" while refl1d takes the LAST entry as the incident medium.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from nr_workbench.spec.geometry import read_geometry
from nr_workbench.spec.models import ModelSpec

pytestmark = pytest.mark.integration

CELL = [
    {"name": "D2O", "material": "D2O", "thickness": 0, "roughness": 10},
    {"name": "Cu", "material": "Cu", "thickness": 400, "roughness": 8},
    {"name": "Si", "material": "Si"},
]
MATERIALS = {"D2O": {"rho": 6.36}, "Cu": {"rho": 6.55}, "Si": {"rho": 2.07}}


def spec_with(stack, **probe) -> ModelSpec:
    return ModelSpec.model_validate(
        {
            "schema": "nrw-model/1",
            "name": "m",
            "materials": MATERIALS,
            "stack": stack,
            "probe": {"resolution": "angular_only", **probe},
            "states": [
                {"name": "s1", "run": 1, "segments": [{"file": "a.txt", "theta": 0.45}]}
            ],
        }
    )


# --------------------------------------------------------------------------
# Reading the geometry off the order
# --------------------------------------------------------------------------


def test_a_substrate_last_stack_is_back_reflection() -> None:
    """The solid/liquid cell this beamline mostly runs."""
    geometry = read_geometry(spec_with(CELL))

    assert geometry.incident == "Si"
    assert geometry.backing == "D2O"
    assert geometry.back_reflection is True


def test_a_fluid_last_stack_is_front_reflection() -> None:
    geometry = read_geometry(spec_with(list(reversed(CELL))))

    assert geometry.incident == "D2O"
    assert geometry.back_reflection is False


def test_a_stack_naming_neither_end_says_so() -> None:
    """Better to decline than to guess at an unrecognised material."""
    unknown = [
        {"name": "A", "material": "A"},
        {"name": "B", "material": "B"},
    ]
    geometry = read_geometry(
        ModelSpec.model_validate(
            {
                "schema": "nrw-model/1",
                "name": "m",
                "materials": {"A": {"rho": 1.0}, "B": {"rho": 2.0}},
                "stack": unknown,
                "probe": {"resolution": "angular_only"},
                "states": [
                    {
                        "name": "s1",
                        "run": 1,
                        "segments": [{"file": "a.txt", "theta": 0.45}],
                    }
                ],
            }
        )
    )

    assert geometry.back_reflection is None


# --------------------------------------------------------------------------
# The flag as an assertion
# --------------------------------------------------------------------------


def write_data(root: Path, *, qc: float) -> Path:
    """A curve with a total-reflection plateau ending near ``qc``."""
    directory = root / "samples" / "S1" / "data" / "steady"
    directory.mkdir(parents=True, exist_ok=True)
    q = np.linspace(0.005, 0.08, 200)
    r = np.where(q < qc, 0.85, 0.85 * (qc / q) ** 4)
    path = directory / "REFL_1_1_1_partial.txt"
    np.savetxt(path, np.column_stack([q, r, r * 0.05, q * 0.02]))
    return path


def project_with(
    tmp_path: Path, stack, *, qc: float, **probe
) -> tuple[Path, ModelSpec]:
    (tmp_path / "nrw.toml").write_text("", encoding="utf-8")
    write_data(tmp_path, qc=qc)
    spec = ModelSpec.model_validate(
        {
            "schema": "nrw-model/1",
            "name": "m",
            "materials": MATERIALS,
            "stack": stack,
            "probe": {"resolution": "angular_only", **probe},
            "states": [
                {
                    "name": "s1",
                    "run": 1,
                    "segments": [
                        {
                            "file": "samples/S1/data/steady/REFL_1_1_1_partial.txt",
                            "theta": 0.45,
                        }
                    ],
                }
            ],
            "parameters": [
                {"path": "Cu.thickness", "range": [100, 600], "per": "state"}
            ],
        }
    )
    return tmp_path, spec


# Si -> Cu is a contrast of 4.48, so Qc = 0.0150. D2O -> Cu is 0.19, Qc = 0.0031.
BACK_QC = 0.0150
FRONT_QC = 0.0031


def test_an_undeclared_spec_is_not_failed_for_disagreeing_with_nothing(
    tmp_path: Path,
) -> None:
    """`False` and "not declared" have to be tellable apart, or every existing
    back-reflection spec starts failing."""
    from nr_workbench.spec.validate import validate_spec

    root, spec = project_with(tmp_path, CELL, qc=BACK_QC)

    assert validate_spec(spec, root).ok


def test_declaring_the_opposite_of_the_order_is_an_error(tmp_path: Path) -> None:
    from nr_workbench.spec.validate import validate_spec

    root, spec = project_with(tmp_path, CELL, qc=BACK_QC, back_reflection=False)

    report = validate_spec(spec, root)

    assert not report.ok
    assert any("back_reflection" in e and "Si last" in e for e in report.errors)


def test_declaring_what_the_order_says_is_fine(tmp_path: Path) -> None:
    from nr_workbench.spec.validate import validate_spec

    root, spec = project_with(tmp_path, CELL, qc=BACK_QC, back_reflection=True)

    assert validate_spec(spec, root).ok


def test_the_geometry_is_always_stated(tmp_path: Path) -> None:
    """A geometry nobody mentioned is the state the mistake hides in."""
    from nr_workbench.spec.validate import validate_spec

    root, spec = project_with(tmp_path, CELL, qc=BACK_QC)

    assert any("beam enters Si" in line for line in validate_spec(spec, root).info)


# --------------------------------------------------------------------------
# The data as the arbiter
# --------------------------------------------------------------------------


def test_an_inverted_stack_is_caught_by_the_critical_edge(tmp_path: Path) -> None:
    """The check that would have saved four fits.

    The stack claims the beam enters through D2O, which puts the edge at
    Qc = 0.0031. The measurement shows it at 0.0150, which is the Si side.
    Nothing is declared -- the data alone says the order is wrong.
    """
    from nr_workbench.spec.validate import validate_spec

    root, spec = project_with(tmp_path, list(reversed(CELL)), qc=BACK_QC)

    report = validate_spec(spec, root)

    assert not report.ok
    assert any("data contradict the stack order" in e for e in report.errors)


def test_no_plateau_is_never_a_verdict(tmp_path: Path) -> None:
    """Absence has too many other causes to act on.

    A segment whose Q starts above any edge shows no plateau and never
    should -- a fit of the 1.2 and 3.5 degree segments alone is the normal
    case -- so "reverse your stack" must not be said on the strength of not
    seeing one. Only a plateau that is there counts.
    """
    from nr_workbench.spec.geometry import critical_edge_side
    from nr_workbench.spec.resolve import build_table, discover_measurements
    from nr_workbench.spec.validate import validate_spec

    root, spec = project_with(tmp_path, list(reversed(CELL)), qc=FRONT_QC)
    table = build_table(spec, discover_measurements(spec, root))

    assert critical_edge_side(spec, table, root) is None
    assert validate_spec(spec, root).ok
