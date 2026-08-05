"""Packaging tests: the skills and templates must ship in a built wheel.

Skills and templates are *data* -- no ``__init__.py``, so ``packages.find``
does not collect them. If the ``[tool.setuptools.package-data]`` globs are
wrong they vanish from the wheel, and `nrw init` on an installed
nr-workbench produces an empty project.

This exact bug shipped in AuRE: its package-data covered only ``aure.web``, so
no ``SKILL.md`` reached the wheel and the published Docker image ran with an
empty skill registry and no error. Editable installs hid it, which is why a
test that merely imports the package cannot catch it. These build a real wheel
and look inside.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# Never copied into the pristine build tree. `*.egg-info` matters most: a
# SOURCES.txt left by an earlier build is reused by setuptools and re-includes
# files the current configuration no longer packages, so a stale local build
# artifact can mask a genuine packaging regression.
_BUILD_EXCLUDES = shutil.ignore_patterns(
    "*.egg-info",
    ".git",
    ".venv",
    "venv",
    "build",
    "dist",
    "htmlcov",
    "__pycache__",
    "*.pyc",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
)

EXPECTED_SKILLS = {
    "nr-workbench-project",
    "neutron-reflectometry",
    "tnr-change-assessment",
}


@pytest.fixture(scope="module")
def wheel_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build a wheel from a pristine copy of the repo and return its path.

    Skips when a wheel cannot be built at all (no network for build isolation,
    read-only checkout). A build that *succeeds* but omits files is a real
    failure and is asserted on below.
    """
    if not (REPO_ROOT / "pyproject.toml").is_file():
        pytest.skip("not running from a source checkout")

    pristine = tmp_path_factory.mktemp("src") / "nr-workbench"
    shutil.copytree(REPO_ROOT, pristine, ignore=_BUILD_EXCLUDES, symlinks=True)

    outdir = tmp_path_factory.mktemp("wheel")
    base = [
        sys.executable,
        "-m",
        "pip",
        "wheel",
        "--no-deps",
        "--no-cache-dir",
        "--wheel-dir",
        str(outdir),
    ]

    # Without build isolation first: no network, much faster. Falls back to an
    # isolated build when setuptools is absent from the active venv.
    result = None
    for command in (
        [*base, "--no-build-isolation", str(pristine)],
        [*base, str(pristine)],
    ):
        result = subprocess.run(command, capture_output=True, text=True, timeout=900)
        if result.returncode == 0:
            break
    if result is None or result.returncode != 0:
        stderr = result.stderr[-2000:] if result else "no attempt ran"
        pytest.skip(f"could not build a wheel in this environment:\n{stderr}")

    wheels = list(outdir.glob("nr_workbench-*.whl"))
    if not wheels:
        pytest.skip("pip reported success but produced no nr_workbench wheel")
    return wheels[0]


@pytest.fixture(scope="module")
def wheel_namelist(wheel_path: Path) -> list[str]:
    """The names contained in the built wheel."""
    with zipfile.ZipFile(wheel_path) as archive:
        return archive.namelist()


def test_wheel_contains_every_skill_md(wheel_namelist: list[str]) -> None:
    packaged = {
        name.split("/")[3]
        for name in wheel_namelist
        if name.startswith("nr_workbench/skills/") and name.endswith("/SKILL.md")
    }
    missing = EXPECTED_SKILLS - packaged

    assert not missing, (
        f"SKILL.md missing from the wheel for: {sorted(missing)}. Check the "
        "'skills/**/*' glob in [tool.setuptools.package-data]. Without it, "
        "`nrw init` on an installed nr-workbench installs no skills."
    )


def test_wheel_contains_skill_references(wheel_namelist: list[str]) -> None:
    """references/ material must travel with its skill, not just the SKILL.md."""
    on_disk = sorted(
        path.relative_to(REPO_ROOT / "src").as_posix()
        for path in (REPO_ROOT / "src" / "nr_workbench" / "skills").glob(
            "*/*/references/*"
        )
        if path.is_file()
    )
    if not on_disk:
        pytest.skip("no skill reference files in this checkout")

    missing = [path for path in on_disk if path not in set(wheel_namelist)]

    assert not missing, f"skill reference files missing from the wheel: {missing}"


def test_wheel_contains_every_template(wheel_namelist: list[str]) -> None:
    """Templates drive `nrw init`; a missing one is a hole in the scaffold."""
    on_disk = sorted(
        path.relative_to(REPO_ROOT / "src").as_posix()
        for path in (REPO_ROOT / "src" / "nr_workbench" / "templates").rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    )
    missing = [path for path in on_disk if path not in set(wheel_namelist)]

    assert not missing, (
        f"templates missing from the wheel: {missing}. Check the 'templates/**/*' "
        "glob in [tool.setuptools.package-data]."
    )


def test_wheel_contains_py_typed(wheel_namelist: list[str]) -> None:
    assert "nr_workbench/py.typed" in wheel_namelist


def test_wheel_declares_only_the_one_expected_direct_reference(
    wheel_path: Path, wheel_namelist: list[str]
) -> None:
    """Guard the one deliberate exception.

    `aure @ git+...` is a known, accepted cost: aure is not on PyPI (no publish
    workflow, and its own `export` extra carries a direct reference that blocks
    upload), so nr-workbench installs from git too. Any *additional* direct
    reference would be an accident, and each one is another dependency that
    cannot be resolved from an index.

    Asserted against the built METADATA rather than pyproject.toml, because
    that is what a consumer's resolver actually reads.
    """
    metadata_names = [n for n in wheel_namelist if n.endswith(".dist-info/METADATA")]
    assert metadata_names, "wheel has no METADATA"

    with zipfile.ZipFile(wheel_path) as archive:
        metadata = archive.read(metadata_names[0]).decode("utf-8")

    direct = [
        line
        for line in metadata.splitlines()
        if line.startswith("Requires-Dist:") and "@ " in line
    ]

    assert len(direct) == 1, f"expected exactly one direct reference, got: {direct}"
    assert "aure@ git+" in direct[0].replace(" @ ", "@ "), direct[0]
