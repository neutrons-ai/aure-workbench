"""The gate: does a generated script reproduce the hand-written one?

`nrw-model/1` earns its place only if it can express the hardest real model
this group has -- `Cu-THF-218386-full-sequence.py`, 343 lines co-refining two
three-segment OCV states with the fifteen tNR slices measured between them,
with the tNR structural parameters constrained to interpolate linearly between
the OCV endpoints.

Both problems are driven from the *same* parameter vector and their
chi-squared compared. Anything above floating-point noise means the spec does
not mean what the script means, and the abstraction is not yet trustworthy.

The reference and its data are vendored under ``tests/data/reference/`` so this
runs without the experiments-2025 repository.
"""

from __future__ import annotations

import runpy
import shutil
import warnings
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.slow]

np = pytest.importorskip("numpy")
pytest.importorskip("refl1d")

REFERENCE_DIR = Path(__file__).parent / "data" / "reference"
REFERENCE_SCRIPT = REFERENCE_DIR / "cu_thf_218386_full_sequence.py"
SPEC_FILE = REFERENCE_DIR / "cu-thf-218389-full-sequence.yaml"

#: What the hand-written script builds. Asserted separately so a change in
#: either number is reported as itself rather than as a chi-squared mismatch.
EXPECTED_EXPERIMENTS = 21
EXPECTED_FREE = 40

#: Reference parameter index -> generated parameter label.
#:
#: Stated explicitly because the reference reuses labels: "CuOx rho" names both
#: ocv1's and ocv2's, so matching by name is ambiguous. The generated names
#: disambiguate by group, which is the improvement -- but it means the
#: correspondence has to be written down.
CORRESPONDENCE = {
    0: "ocv1 probe intensity",
    1: "ocv1 THF roughness",
    2: "THF rho",
    3: "ocv1 CuOx roughness",
    4: "ocv1 CuOx rho",
    5: "ocv1 CuOx thickness",
    6: "ocv1 Cu roughness",
    7: "Cu rho",
    8: "ocv1 Cu thickness",
    9: "ocv1 Ti roughness",
    10: "ocv1 Ti rho",
    11: "ocv1 Ti thickness",
    12: "Intensity_386_3",
    13: "ocv2 probe intensity",
    14: "ocv2 THF roughness",
    15: "ocv2 CuOx roughness",
    16: "ocv2 CuOx rho",
    17: "ocv2 CuOx thickness",
    18: "ocv2 Cu roughness",
    19: "ocv2 Cu thickness",
    20: "ocv2 Ti roughness",
    21: "ocv2 Ti rho",
    22: "ocv2 Ti thickness",
    23: "tnr probe intensity",
    24: "tnr probe theta_offset",
    **{25 + i: f"tnr#{i} Cu thickness" for i in range(15)},
}


@pytest.fixture(scope="module")
def reference_problem():
    """The hand-written script's FitProblem."""
    if not REFERENCE_SCRIPT.is_file():
        pytest.skip("vendored reference script not present")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return runpy.run_path(str(REFERENCE_SCRIPT), run_name="__nrw_reference__")[
            "problem"
        ]


@pytest.fixture(scope="module")
def generated_problem(tmp_path_factory: pytest.TempPathFactory):
    """The FitProblem built from the generated script."""
    from click.testing import CliRunner

    from nr_workbench.cli import main

    root = tmp_path_factory.mktemp("gate") / "proj"
    result = CliRunner().invoke(main, ["init", str(root), "--sample", "Sample6"])
    assert result.exit_code == 0, result.output

    data = root / "samples" / "Sample6" / "data"
    shutil.copytree(REFERENCE_DIR / "steady", data / "steady", dirs_exist_ok=True)
    shutil.copytree(REFERENCE_DIR / "tnr", data / "tnr" / "218389", dirs_exist_ok=True)

    models = root / "samples" / "Sample6" / "models"
    models.mkdir(parents=True, exist_ok=True)
    spec = models / SPEC_FILE.name
    shutil.copy(SPEC_FILE, spec)

    generated = CliRunner().invoke(main, ["model", "generate", str(spec)])
    assert generated.exit_code == 0, generated.output

    script = spec.with_suffix(".py")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return runpy.run_path(str(script), run_name="__nrw_generated__")[
            "problem"
        ], script


def test_reference_builds_what_we_expect(reference_problem) -> None:
    """Pin the reference, so a change to it is not mistaken for a regression."""
    assert len(list(reference_problem.models)) == EXPECTED_EXPERIMENTS
    assert len(reference_problem.getp()) == EXPECTED_FREE


def test_generated_has_the_same_shape(generated_problem) -> None:
    problem, _ = generated_problem

    assert len(list(problem.models)) == EXPECTED_EXPERIMENTS
    assert len(problem.getp()) == EXPECTED_FREE


def test_correspondence_covers_every_generated_parameter(generated_problem) -> None:
    """A parameter outside the map would make the gate silently partial."""
    problem, _ = generated_problem
    labels = set(problem.labels())

    assert labels == set(CORRESPONDENCE.values())


def test_generated_reproduces_the_reference_numerically(
    reference_problem, generated_problem
) -> None:
    """★ The gate.

    Same parameter vector into both, chi-squared out of both, compared. If this
    holds, the schema demonstrably expresses the hardest real model the group
    has -- including three-segment co-refinement, cross-state sharing, and
    linear interpolation of tNR structure between two OCV endpoints.
    """
    generated, _ = generated_problem
    reference_labels = reference_problem.labels()
    generated_labels = generated.labels()

    position = {label: i for i, label in enumerate(generated_labels)}
    order = [position[CORRESPONDENCE[i]] for i in range(len(reference_labels))]

    bounds = np.array(reference_problem.bounds())
    low, high = (
        (bounds[0], bounds[1]) if bounds.shape[0] == 2 else (bounds[:, 0], bounds[:, 1])
    )

    rng = np.random.default_rng(11)
    worst = 0.0
    for _ in range(5):
        draw = low + (high - low) * rng.uniform(0.3, 0.7, size=len(low))
        reference_problem.setp(draw)

        mapped = np.empty(len(generated_labels))
        for reference_index, generated_index in enumerate(order):
            mapped[generated_index] = draw[reference_index]
        generated.setp(mapped)

        a, b = float(reference_problem.chisq()), float(generated.chisq())
        worst = max(worst, abs(a - b) / max(1.0, abs(a)))

    assert worst < 1e-9, (
        f"generated chi-squared differs from the reference by {worst:.3e} relative. "
        "The spec no longer means what the hand-written script means."
    )


def test_generated_script_self_checks_pass(generated_problem) -> None:
    """The emitted `_check_links()` must hold when the script is run directly."""
    import subprocess
    import sys

    _, script = generated_problem
    result = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, timeout=600
    )

    assert result.returncode == 0, result.stderr[-2000:]
    assert "free parameters: 40" in result.stdout


def test_generated_script_has_no_absolute_paths(generated_problem) -> None:
    """An absolute path is what made the hand-written scripts unshareable.

    The reference literally contains another user's home directory in one of
    its variants; the generated form resolves everything from `nrw.toml`.
    """
    _, script = generated_problem
    source = script.read_text(encoding="utf-8")

    offenders = [
        line
        for line in source.splitlines()
        if ('"/' in line or "'/" in line) and "PROJECT_ROOT" not in line
    ]
    assert offenders == [], offenders


def test_spec_is_far_shorter_than_the_script() -> None:
    """The point of the exercise, asserted so it cannot quietly stop being true."""
    spec_lines = len(
        [
            line
            for line in SPEC_FILE.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
    )
    reference_lines = len(
        [
            line
            for line in REFERENCE_SCRIPT.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
    )

    assert spec_lines < reference_lines / 3, (
        f"spec is {spec_lines} lines against the reference's {reference_lines}"
    )
