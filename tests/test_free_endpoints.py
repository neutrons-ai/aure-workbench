"""Constraint endpoints that are fitted rather than anchored to a state.

`from: ocv1, to: ocv2` borrows parameters the steady-state data already
constrains, which is why the interpolating forms cost nothing. Sometimes there
is no bracketing state to borrow from, or where the sample finished *during*
the run is the measurement rather than something to assume -- and then the
endpoint has to be fitted.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from nr_workbench.spec.models import SpecError, load_spec
from nr_workbench.spec.resolve import build_table, discover_measurements

BASE = """\
schema: nrw-model/1
name: m
sample: S1
materials: {D2O: {rho: 6.36}, Cu: {rho: 6.55}, Si: {rho: 2.07}}
stack:
  - {name: D2O, material: D2O, thickness: 0, roughness: 5}
  - {name: Cu, material: Cu, thickness: 500, roughness: 5}
  - {name: Si, material: Si}
probe: {resolution: angular_only}
states:
  - name: ocv1
    run: 100001
    segments: auto
    thetas: [0.45]
    data_dir: samples/S1/data/steady
  - name: ocv2
    run: 100002
    segments: auto
    thetas: [0.45]
    data_dir: samples/S1/data/steady
series:
  - name: tnr
    run: 100003
    reduced_dir: samples/S1/data/tnr/100003
    theta: 0.6
    time_from: filename
parameters:
  - {path: Cu.thickness, range: [400, 600], per: state, in: [ocv1, ocv2]}
constraints:
  - series: tnr
    form: linear_in_time
    from: %(from)s
    to: %(to)s
    paths: [Cu.thickness]
%(extra)s"""


def project_with_data(root: Path) -> None:
    """Two steady runs and a three-slice series."""
    q = np.linspace(0.01, 0.2, 25)
    r = 1e-3 * (0.01 / q) ** 4
    four = "\n".join(
        f"{a:.6e} {b:.6e} {c:.6e} {d:.6e}"
        for a, b, c, d in zip(q, r, 0.05 * r, 0.02 * q, strict=True)
    )
    # Reduced tNR slices carry the same four columns as a steady-state file.
    slices = four

    steady = root / "samples" / "S1" / "data" / "steady"
    steady.mkdir(parents=True, exist_ok=True)
    for run in (100001, 100002):
        (steady / f"REFL_{run}_1_{run}_partial.txt").write_text(four)

    tnr = root / "samples" / "S1" / "data" / "tnr" / "100003"
    tnr.mkdir(parents=True, exist_ok=True)
    for seconds in (0, 240, 480):
        (tnr / f"r100003_t{seconds:06d}.txt").write_text(slices)


def resolve(root: Path, *, start: str, end: str, extra: str = ""):
    """Build the table for one endpoint configuration."""
    spec_path = root / "spec.yaml"
    spec_path.write_text(
        BASE % {"from": start, "to": end, "extra": extra}, encoding="utf-8"
    )
    spec = load_spec(spec_path)
    return build_table(spec, discover_measurements(spec, root))


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "nrw.toml").write_text("", encoding="utf-8")
    project_with_data(tmp_path)
    return tmp_path


def keys(table) -> list[str]:
    return [p.key for p in table.free]


def test_anchored_endpoints_add_no_parameters(root: Path) -> None:
    """The baseline: the series is described by the two states."""
    table = resolve(root, start="ocv1", end="ocv2")

    assert "Cu.thickness@tnr:start" not in keys(table)
    assert "Cu.thickness@tnr:end" not in keys(table)
    assert table.n_free == 2, keys(table)


def test_a_free_end_adds_one_parameter_per_path(root: Path) -> None:
    """Known start, fitted end -- the common case after an OCV measurement."""
    table = resolve(root, start="ocv1", end="free")

    assert "Cu.thickness@tnr:end" in keys(table)
    assert "Cu.thickness@tnr:start" not in keys(table)
    assert table.n_free == 3


def test_both_free_adds_two_per_path(root: Path) -> None:
    """For a series with no bracketing measurement at all."""
    table = resolve(root, start="free", end="free")

    assert "Cu.thickness@tnr:start" in keys(table)
    assert "Cu.thickness@tnr:end" in keys(table)
    assert table.n_free == 4


def test_a_free_endpoint_borrows_the_paths_declared_range(root: Path) -> None:
    """A Cu thickness plausible for the steady states is plausible here.

    Repeating the range would be a second place for it to be wrong.
    """
    table = resolve(root, start="ocv1", end="free")
    endpoint = next(p for p in table.free if p.key == "Cu.thickness@tnr:end")

    assert endpoint.bounds == (400.0, 600.0)


def test_an_explicit_endpoint_range_wins(root: Path) -> None:
    """For a series whose endpoints are not plausibly the steady-state range."""
    table = resolve(
        root, start="free", end="free", extra="    endpoint_range: [100, 900]\n"
    )
    endpoint = next(p for p in table.free if p.key == "Cu.thickness@tnr:end")

    assert endpoint.bounds == (100.0, 900.0)


def test_a_free_endpoint_with_no_range_anywhere_is_an_error(tmp_path: Path) -> None:
    """An unbounded endpoint drags the whole trajectory somewhere unphysical."""
    (tmp_path / "nrw.toml").write_text("", encoding="utf-8")
    project_with_data(tmp_path)
    spec_path = tmp_path / "spec.yaml"
    spec_path.write_text(
        BASE.replace(
            "  - {path: Cu.thickness, range: [400, 600], per: state, in: [ocv1, ocv2]}",
            "  - {path: Cu.roughness, range: [1, 20], per: state, in: [ocv1, ocv2]}",
        )
        % {"from": "free", "to": "free", "extra": ""},
        encoding="utf-8",
    )
    spec = load_spec(spec_path)

    with pytest.raises(SpecError, match="endpoint_range"):
        build_table(spec, discover_measurements(spec, tmp_path))


def test_the_slice_expressions_read_from_the_fitted_endpoint(root: Path) -> None:
    """The generated code must actually use it, not just create it."""
    table = resolve(root, start="ocv1", end="free")
    sources = [e.source for e in table.expressions]

    assert any("Cu.thickness@tnr:end" in source for source in sources)
    assert any("Cu.thickness@ocv1" in source for source in sources)


def test_the_first_and_last_slice_sit_at_the_endpoints(root: Path) -> None:
    """f = 0 at the first slice and 1 at the last, whatever the endpoints are."""
    table = resolve(root, start="free", end="free")
    by_key = {e.key: e.source for e in table.expressions}

    first = by_key["Cu.thickness@tnr#0"]
    last = by_key["Cu.thickness@tnr#2"]

    assert "tnr:start" in first and "tnr:end" not in first
    assert "tnr:end" in last


def test_the_explanation_says_an_endpoint_is_fitted(root: Path) -> None:
    """Claiming "no new free parameters" would be wrong and is the easy bug."""
    from nr_workbench.spec.explain import explain

    table = resolve(root, start="ocv1", end="free")
    document = explain(table)

    assert "**fitted**" in document
    assert "No new free parameters" not in document
    assert "p[tnr:end]" in document
    assert "1 per path" in document


def test_the_explanation_still_says_anchored_costs_nothing(root: Path) -> None:
    from nr_workbench.spec.explain import explain

    document = explain(resolve(root, start="ocv1", end="ocv2"))

    assert "No new free parameters" in document


@pytest.mark.integration
def test_a_free_endpoint_builds_and_is_fittable(root: Path) -> None:
    """It has to reach refl1d as a real varying parameter."""
    pytest.importorskip("refl1d")
    from nr_workbench.codegen.generator import generate
    from nr_workbench.fitting.runner import load_problem

    table = resolve(root, start="ocv1", end="free")
    script = root / "samples" / "S1" / "models" / "m.py"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(generate(table), encoding="utf-8")

    problem = load_problem(script, root=root).problem

    assert len(problem.getp()) == table.n_free
    assert np.isfinite(problem.chisq())
