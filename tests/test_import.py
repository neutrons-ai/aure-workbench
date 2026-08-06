"""Importing an existing beamtime directory.

The adoption argument rests on this being safe to run against a directory
someone is working in: no copies of large data, no moves, no overwrites, and
nothing written at all until asked twice.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nr_workbench.project.importer import (
    Action,
    apply_import,
    detect_layout,
    plan_import,
)
from nr_workbench.project.importer import _link_target as link_target
from nr_workbench.project.importer import _run_from as run_from

REFL1D_SCRIPT = """\
import numpy as np
from refl1d.names import *
from refl1d.probe import make_probe

# ... 300 lines of model definition ...

problem = FitProblem(experiments)
"""


def beamtime(tmp_path: Path, layout: str = "per-sample") -> Path:
    """Build a small beamtime directory in one of the real shapes."""
    source = tmp_path / "beamtime"
    if layout == "per-sample":
        base = source / "Sample4"
        (base / "Rawdata").mkdir(parents=True)
        (base / "Rawdata" / "REFL_226628_1_226628_partial.txt").write_text("1 2 3 4\n")
        (base / "Rawdata" / "REFL_226628_2_226629_partial.txt").write_text("1 2 3 4\n")
        (base / "measurements.md").write_text("# Sample 4\n")
        (base / "Models").mkdir()
        (base / "Models" / "ionomer.py").write_text(REFL1D_SCRIPT)
    else:
        (source / "data" / "steady").mkdir(parents=True)
        (source / "data" / "steady" / "REFL_223921_combined_data_auto.txt").write_text(
            "1 2 3 4\n"
        )
        (source / "models").mkdir()
        (source / "models" / "cu.py").write_text(REFL1D_SCRIPT)
    return source


@pytest.fixture
def imported(project: Path, tmp_path: Path) -> tuple[Path, Path]:
    """A scaffolded project plus a per-sample beamtime to import."""
    return project, beamtime(tmp_path)


# --------------------------------------------------------------------------
# Layout detection
# --------------------------------------------------------------------------


def test_detects_a_per_sample_layout(tmp_path: Path) -> None:
    assert detect_layout(beamtime(tmp_path, "per-sample")) == "per-sample"


def test_detects_an_experiment_level_layout(tmp_path: Path) -> None:
    assert detect_layout(beamtime(tmp_path, "experiment-level")) == "experiment-level"


def test_detects_a_mixed_layout(tmp_path: Path) -> None:
    """Two of the four real beamtimes have both shapes at once."""
    source = beamtime(tmp_path, "per-sample")
    (source / "data" / "steady").mkdir(parents=True)

    assert detect_layout(source) == "mixed"


# --------------------------------------------------------------------------
# Planning
# --------------------------------------------------------------------------


def test_nothing_is_written_by_planning(imported: tuple[Path, Path]) -> None:
    """The default has to be safe on a directory someone is using."""
    root, source = imported
    before = set(root.rglob("*"))

    plan_import(source, root=root)

    assert set(root.rglob("*")) == before


def test_data_is_linked_and_scripts_are_copied(imported: tuple[Path, Path]) -> None:
    """Data can be large and is authoritative elsewhere; scripts you will edit."""
    root, source = imported

    plan = plan_import(source, root=root)
    by_action = {p.destination: p.action for p in plan.planned}

    assert (
        by_action["samples/Sample4/data/steady/REFL_226628_1_226628_partial.txt"]
        is Action.LINK
    )
    assert by_action["samples/Sample4/models/ionomer.py"] is Action.COPY
    assert by_action["samples/Sample4/sample.md"] is Action.COPY


def test_the_sample_comes_from_the_path_not_the_run_number(
    imported: tuple[Path, Path],
) -> None:
    """Inferring sample membership from run numbers puts files in wrong places."""
    root, source = imported

    plan = plan_import(source, root=root)

    assert plan.samples == {"Sample4"}


def test_a_refl1d_script_is_recognised_by_content(
    project: Path, tmp_path: Path
) -> None:
    """The deciding token is on the last line of the file.

    refl1d scripts end with `problem = FitProblem(...)` and import via
    `from refl1d.names import *`, so sniffing the first few kilobytes finds the
    imports and misses the thing that identifies the file. In the real oct2025
    script `FitProblem` is on line 344 of 344.
    """
    source = tmp_path / "bt" / "Sample6"
    source.mkdir(parents=True)
    padded = "# padding\n" * 5000 + REFL1D_SCRIPT
    (source / "loose-model.py").write_text(padded)

    plan = plan_import(tmp_path / "bt", root=project)

    assert any(
        p.destination == "samples/Sample6/models/loose-model.py" for p in plan.planned
    )


def test_previous_fit_outputs_are_left_behind(project: Path, tmp_path: Path) -> None:
    """Importing them would fake provenance nobody recorded.

    A results directory here carries input hashes, the environment and the
    command. A .dat dumped by a 2025 bumps run has none of that, and putting it
    where `nrw whence` promises an answer would make the tool lie.
    """
    source = tmp_path / "bt"
    (source / "results" / "old-fit").mkdir(parents=True)
    (source / "results" / "old-fit" / "model-1-refl.dat").write_text("x\n")
    (source / "results" / "old-fit" / "model.par").write_text("x\n")

    plan = plan_import(source, root=project)

    assert len(plan.derived) == 2
    assert not plan.planned
    assert not plan.unclassified, "these were recognised, not merely unclassified"


def test_an_existing_destination_is_skipped_never_overwritten(
    imported: tuple[Path, Path],
) -> None:
    """`sample.md` is written by the scaffold and may already be filled in."""
    root, source = imported
    target = root / "samples" / "Sample4" / "sample.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("my own notes\n", encoding="utf-8")

    plan = plan_import(source, root=root)
    entry = next(p for p in plan.planned if p.destination.endswith("Sample4/sample.md"))

    assert entry.action is Action.SKIP
    assert "already exists" in entry.reason

    apply_import(plan, root)
    assert target.read_text() == "my own notes\n"


# --------------------------------------------------------------------------
# Applying
# --------------------------------------------------------------------------


def test_apply_links_data_and_leaves_the_source_alone(
    imported: tuple[Path, Path],
) -> None:
    """One copy of every byte, and the original tree still works."""
    root, source = imported
    original = source / "Sample4" / "Rawdata" / "REFL_226628_1_226628_partial.txt"
    before = original.read_text()

    apply_import(plan_import(source, root=root), root)
    linked = root / "samples/Sample4/data/steady/REFL_226628_1_226628_partial.txt"

    assert linked.is_symlink()
    assert linked.read_text() == before
    assert original.exists(), "the source must not be moved"


def test_apply_copies_scripts_as_real_files(imported: tuple[Path, Path]) -> None:
    """A script you are going to edit must not be a link into another repo."""
    root, source = imported

    apply_import(plan_import(source, root=root), root)
    script = root / "samples/Sample4/models/ionomer.py"

    assert script.is_file()
    assert not script.is_symlink()


def test_apply_is_idempotent(imported: tuple[Path, Path]) -> None:
    """Re-running after adding data must not disturb what is already there."""
    root, source = imported
    first = apply_import(plan_import(source, root=root), root)

    second = apply_import(plan_import(source, root=root), root)

    assert first
    assert second == []


def test_a_nearby_source_gets_a_relative_link(imported: tuple[Path, Path]) -> None:
    """When the two trees sit together, a relative link survives moving them."""
    root, source = imported

    apply_import(plan_import(source, root=root), root)
    link = root / "samples/Sample4/data/steady/REFL_226628_1_226628_partial.txt"

    assert not Path(link.readlink()).is_absolute()
    assert link.exists()


def test_an_out_of_tree_source_gets_an_absolute_link() -> None:
    """A dozen `..` segments gain nothing when the source is another repo.

    The relative form only helps if both trees move together, which is exactly
    what does not happen when the data lives in a different checkout. Tested on
    the decision rather than the filesystem, because arranging two genuinely
    unrelated trees inside one temp directory is not possible: pytest puts them
    under a shared parent, where relative is in fact the right answer.
    """
    root = Path("/home/someone/beamtime/proj")
    target = root / "samples/S1/data/steady/x.txt"

    far = link_target(Path("/data/other-repo/steady/x.txt"), target, root)
    near = link_target(root / "raw/steady/x.txt", target, root)
    sibling = link_target(Path("/home/someone/beamtime/old-data/x.txt"), target, root)

    assert Path(far).is_absolute(), "another filesystem tree -> absolute"
    assert not Path(near).is_absolute(), "inside the project -> relative"
    assert not Path(sibling).is_absolute(), "moves with the project -> relative"


# --------------------------------------------------------------------------
# Run-number extraction
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("r223995_eis_reduction.json", "223995"),
        ("r218389_sequence_3_eis_3.txt", "218389"),
        ("REFL_226628_1_226629_partial.txt", "226628"),
        ("r223921_t000240.txt", "223921"),
        ("notes.md", None),
        ("model-12345.py", None),
    ],
)
def test_run_numbers_are_found_after_an_r_prefix(
    name: str, expected: str | None
) -> None:
    """`\\b` finds no boundary between `r` and a digit -- both are word chars.

    A `\\b`-anchored six-digit pattern silently matches nothing on
    `r223995_...`, which sent every tNR file into one flat directory instead of
    one per run.
    """
    assert run_from(name) == expected


def test_missing_source_directory_is_an_error(project: Path) -> None:
    with pytest.raises(FileNotFoundError):
        plan_import(project / "nowhere", root=project)
