"""A layer absent from one state: its own stack against the faked equivalent.

Before a state could declare its own stack, an oxide reduced away under
potential was written by keeping the layer and making it copper: thickness
zero, SLD pinned at the (fixed) copper SLD, its now contrast-free roughness
pinned. That is the same structure -- a zero-thickness slab with no contrast to
its neighbour reflects nothing -- so a spec whose second state simply declares
a stack without the oxide must give the same problem: the same chi-squared at
any parameter vector, the same R(Q), and a script whose own `_check_links()`
holds.

Both specs fit the vendored OCV runs 218386 and 218393.
"""

from __future__ import annotations

import runpy
import shutil
import warnings
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

np = pytest.importorskip("numpy")
pytest.importorskip("refl1d")

REFERENCE = Path(__file__).parent / "data" / "reference" / "steady"

HEAD = """\
schema: nrw-model/1
name: {name}
sample: Sample6
materials:
  THF: {{rho: 6.2}}
  CuOx: {{rho: 5.0, irho: 0.0}}
  Cu: {{rho: 6.3}}
  Ti: {{rho: -3.0}}
  Si: {{rho: 2.07}}
stack:
  - {{name: THF,  material: THF,  thickness: 0,   roughness: 25}}
  - {{name: CuOx, material: CuOx, thickness: 60,  roughness: 20}}
  - {{name: Cu,   material: Cu,   thickness: 500, roughness: 5}}
  - {{name: Ti,   material: Ti,   thickness: 40,  roughness: 10}}
  - {{name: Si,   material: Si}}
probe: {{resolution: angular_only, dq_is_fwhm: true}}
"""

STATES = """\
states:
  - name: ocv1
    run: 218386
    segments: auto
    thetas: [0.45, 1.2, 3.5]
    data_dir: samples/Sample6/data/steady
  - name: ocv2
    run: 218393
    segments: auto
    thetas: [0.45, 1.2, 3.5]
    data_dir: samples/Sample6/data/steady
"""

#: The parameters both specs share, word for word. Cu's SLD is not fitted:
#: the faked oxide is pinned at it, which only describes the same structure
#: while it holds still.
SHARED = """\
  - {path: THF.rho,        range: [4, 7],     per: model}
  - {path: THF.roughness,  range: [3, 35],    per: state}
  - {path: Cu.thickness,   range: [400, 600], per: state}
  - {path: Cu.roughness,   range: [1, 25],    per: state}
  - {path: Ti.thickness,   range: [25, 60],   per: model}
  - {path: Ti.rho,         range: [-4, 0],    per: model}
  - {path: Ti.roughness,   range: [1, 22],    per: state}
  - {path: probe.intensity, value: 1.0, pm: 0.1, per: state}
"""

#: The second state declares its own stack, without the oxide. The oxide's
#: parameters need no `in:`: they reach only the state built with it.
PER_STATE = (
    HEAD.format(name="per-state")
    + STATES
    + """\
    stack:
      - {name: THF, material: THF, thickness: 0,   roughness: 25}
      - {name: Cu,  material: Cu,  thickness: 500, roughness: 5}
      - {name: Ti,  material: Ti,  thickness: 40,  roughness: 10}
      - {name: Si,  material: Si}
parameters:
"""
    + SHARED
    + """\
  - {path: CuOx.thickness, range: [10, 125], per: state}
  - {path: CuOx.rho,       range: [1, 6.5],  per: state}
  - {path: CuOx.roughness, range: [3, 33],   per: state}
"""
)

#: One stack for both, the oxide faked away in the second state.
FAKED = (
    HEAD.format(name="faked")
    + STATES
    + """\
parameters:
"""
    + SHARED
    + """\
  - {path: CuOx.thickness, range: [10, 125], per: state, in: [ocv1]}
  - {path: CuOx.rho,       range: [1, 6.5],  per: state, in: [ocv1]}
  - {path: CuOx.roughness, range: [3, 33],   per: state, in: [ocv1]}
  - {path: CuOx.thickness, value: 0, fixed: true, per: state, in: [ocv2]}
  - {path: CuOx.rho,       fixed: 6.3,           per: state, in: [ocv2]}
  - {path: CuOx.roughness, value: 5, fixed: true, per: state, in: [ocv2]}
"""
)


@pytest.fixture(scope="module")
def problems(tmp_path_factory: pytest.TempPathFactory):
    """Both specs generated in one project; their problems and scripts."""
    from click.testing import CliRunner

    from nr_workbench.cli import main

    root = tmp_path_factory.mktemp("equivalence") / "proj"
    result = CliRunner().invoke(main, ["init", str(root), "--sample", "Sample6"])
    assert result.exit_code == 0, result.output
    shutil.copytree(
        REFERENCE, root / "samples" / "Sample6" / "data" / "steady", dirs_exist_ok=True
    )

    models = root / "samples" / "Sample6" / "models"
    models.mkdir(parents=True, exist_ok=True)
    built = {}
    for name, text in (("per-state", PER_STATE), ("faked", FAKED)):
        spec = models / f"{name}.yaml"
        spec.write_text(text, encoding="utf-8")
        generated = CliRunner().invoke(main, ["model", "generate", str(spec)])
        assert generated.exit_code == 0, generated.output
        script = spec.with_suffix(".py")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            problem = runpy.run_path(str(script), run_name=f"__nrw_{name}__")["problem"]
        built[name] = (problem, script)
    return built


def _set_both(per_state, faked, draw: np.ndarray) -> None:
    """Drive both problems from one vector, matched by parameter label."""
    per_state.setp(draw)
    position = {label: i for i, label in enumerate(per_state.labels())}
    faked.setp(np.array([draw[position[label]] for label in faked.labels()]))


def test_both_specs_fit_the_same_parameters(problems) -> None:
    per_state, _ = problems["per-state"]
    faked, _ = problems["faked"]

    assert sorted(per_state.labels()) == sorted(faked.labels())
    assert len(per_state.labels()) == 16


def test_the_per_state_stack_gives_the_faked_chi_squared(problems) -> None:
    """Five draws from inside the bounds, chi-squared within 1e-9 relative."""
    per_state, _ = problems["per-state"]
    faked, _ = problems["faked"]
    low, high = np.asarray(per_state.bounds())  # (2, n): lower and upper

    rng = np.random.default_rng(23)
    worst = 0.0
    for _ in range(5):
        draw = low + (high - low) * rng.uniform(0.3, 0.7, size=len(low))
        _set_both(per_state, faked, draw)
        a, b = float(per_state.chisq()), float(faked.chisq())
        worst = max(worst, abs(a - b) / max(1.0, abs(a)))

    assert worst < 1e-9, f"chi-squared differs by {worst:.3e} relative"


def test_the_per_state_stack_gives_the_faked_reflectivity(problems) -> None:
    per_state, _ = problems["per-state"]
    faked, _ = problems["faked"]
    low, high = np.asarray(per_state.bounds())  # (2, n): lower and upper
    _set_both(per_state, faked, (low + high) / 2)

    for ours, theirs in zip(per_state.models, faked.models, strict=True):
        q_ours, r_ours = ours.reflectivity()
        q_theirs, r_theirs = theirs.reflectivity()
        np.testing.assert_allclose(q_ours, q_theirs)
        np.testing.assert_allclose(r_ours, r_theirs, rtol=1e-9, atol=0)


def test_the_per_state_script_checks_its_own_links(problems) -> None:
    import subprocess
    import sys

    _, script = problems["per-state"]
    result = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, timeout=600
    )

    assert result.returncode == 0, result.stderr[-2000:]
    assert "free parameters: 16" in result.stdout


def test_the_second_state_is_built_without_the_oxide(problems) -> None:
    """The comparison above means something only if the structures differ."""
    per_state, script = problems["per-state"]
    names = [[layer.name for layer in model.sample] for model in per_state.models]

    assert names[0] == ["THF", "CuOx", "Cu", "Ti", "Si"]
    assert names[-1] == ["THF", "Cu", "Ti", "Si"]
    assert "def create_sample_ocv2():" in script.read_text(encoding="utf-8")
