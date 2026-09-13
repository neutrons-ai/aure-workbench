"""Composing an AuRE setup from a sample's files and its prose.

The load-bearing assertion here is :func:`test_composed_setup_loads_in_aure`:
AuRE rejects unknown top-level keys and validates a state's files against the
resolved instrument, so a document that merely *looks* right is not evidence.
Every other test in this file is about getting a specific thing right; that one
is about the whole thing being accepted by the code that will actually run it.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from nr_workbench.aure_setup import (
    SetupError,
    compose,
    describe_sample,
    reads_as_back_reflection,
    state_files,
)
from nr_workbench.project.scan import SteadyMeasurement, scan_sample

REFERENCE = Path(__file__).parent / "data" / "reference" / "steady"

DESCRIBED = """# Cu on Ti

## Description

50 nm copper on 5 nm titanium on a silicon wafer.

## Details

Measured in d8-THF, through the silicon substrate.
"""


def _sample_with_data(project: Path, notes: str = DESCRIBED, run: int = 218386) -> None:
    """Copy the reference partials into Sample1 and write its notes."""
    steady = project / "samples" / "Sample1" / "data" / "steady"
    steady.mkdir(parents=True, exist_ok=True)
    for path in sorted(REFERENCE.glob(f"REFL_{run}_*_partial.txt")):
        shutil.copy(path, steady / path.name)
    (project / "samples" / "Sample1" / "sample.md").write_text(notes, encoding="utf-8")


# --------------------------------------------------------------------------
# Reading the prose
# --------------------------------------------------------------------------


def test_describe_sample_joins_description_and_details() -> None:
    """AuRE gets one sentence; both sections contribute to it."""
    described = describe_sample(DESCRIBED)

    assert "50 nm copper" in described
    assert "d8-THF" in described


def test_describe_sample_reads_an_untouched_template_as_empty() -> None:
    """The template's own prompt comments must not become the description.

    This is the whole reason `nrw aure new` can refuse: an unfilled section has
    to be distinguishable from a filled one, or the refusal never fires and
    AuRE is handed a paragraph of instructions to itself.
    """
    from nr_workbench.commands.sample import plan_sample_files
    from nr_workbench.project.render import RenderContext

    planned = plan_sample_files(RenderContext(project_name="p"), "Sample1")
    stub = next(p for p in planned if p.relpath.endswith("sample.md"))

    assert describe_sample(stub.content.decode("utf-8")) == ""


@pytest.mark.parametrize(
    "phrase",
    [
        "measured through the substrate",
        # The phrasing that a plain substring list missed: naming the
        # substrate material is the more natural sentence, and it is the one
        # the reference notes actually use.
        "measured through the silicon substrate",
        "the beam comes in through the wafer",
        "neutrons enter from the substrate side",
        "this is a back reflection geometry",
        "back-reflection, as usual for the cell",
        "beam enters the back of the sample",
    ],
)
def test_back_reflection_is_read_from_the_notes(phrase: str) -> None:
    """Each phrase a scientist actually writes for the same geometry."""
    assert reads_as_back_reflection(phrase) is True


@pytest.mark.parametrize(
    "phrase",
    [
        "30 nm polystyrene on silicon, in air",
        # "through the <thing>" is only about geometry when <thing> is a
        # substrate; a film is part of the sample description.
        "the solvent diffuses through the polymer film",
    ],
)
def test_front_reflection_notes_do_not_set_back_reflection(phrase: str) -> None:
    """A film in air must not be flipped, and the pattern must not over-reach."""
    assert reads_as_back_reflection(phrase) is False


# --------------------------------------------------------------------------
# Choosing the files
# --------------------------------------------------------------------------


def test_state_files_prefers_partials_in_segment_order() -> None:
    """Segment order is Q order; shuffled input must not survive."""
    measurement = SteadyMeasurement(
        run=218386,
        combined="samples/S/data/steady/REFL_218386_combined_data_auto.txt",
        partials={3: "third.txt", 1: "first.txt", 2: "second.txt"},
    )

    files, kind = state_files(measurement)

    assert files == ["first.txt", "second.txt", "third.txt"]
    assert kind == "partials"


def test_state_files_never_mixes_partials_with_the_combined_file() -> None:
    """AuRE refuses a mixed state; the mix must not be built in the first place.

    A run reduced both ways is the normal case, not an edge case, so this is
    the default path rather than a guard.
    """
    measurement = SteadyMeasurement(
        run=218386,
        combined="combined.txt",
        partials={1: "first.txt"},
    )

    files, _ = state_files(measurement)

    assert "combined.txt" not in files


def test_state_files_falls_back_to_the_combined_file() -> None:
    """A run reduced only as a summed dataset is still fittable."""
    measurement = SteadyMeasurement(run=218386, combined="combined.txt")

    files, kind = state_files(measurement)

    assert (files, kind) == (["combined.txt"], "combined")


def test_state_files_rejects_a_run_with_nothing_on_disk() -> None:
    """A register entry is not data."""
    with pytest.raises(SetupError, match="no reduced files"):
        state_files(SteadyMeasurement(run=218386))


# --------------------------------------------------------------------------
# Composing
# --------------------------------------------------------------------------


def test_compose_puts_every_segment_in_one_state(project: Path) -> None:
    """The three Q segments of one curve are one physical sample, so one state."""
    _sample_with_data(project)
    scan = scan_sample(project, "Sample1")

    composed = compose(
        sample="Sample1", scan=scan, notes=DESCRIBED, root=project, run=218386
    )

    states = composed.document["states"]
    assert len(states) == 1
    assert len(states[0]["data_files"]) == 3


def test_compose_sets_back_reflection_from_the_notes(project: Path) -> None:
    """'through the silicon substrate' is the whole signal."""
    _sample_with_data(project)
    scan = scan_sample(project, "Sample1")

    composed = compose(
        sample="Sample1", scan=scan, notes=DESCRIBED, root=project, run=218386
    )

    assert composed.document["states"][0]["back_reflection"] is True


def test_compose_warns_when_back_reflection_was_not_stated(project: Path) -> None:
    """Silence is not evidence of front reflection, so it is reported."""
    notes = "# S\n\n## Description\n\n30 nm polystyrene on silicon in air.\n"
    _sample_with_data(project, notes=notes)
    scan = scan_sample(project, "Sample1")

    composed = compose(
        sample="Sample1", scan=scan, notes=notes, root=project, run=218386
    )

    assert "back_reflection" not in composed.document["states"][0]
    assert any("back_reflection" in w for w in composed.warnings)


def test_compose_refuses_an_unfilled_description(project: Path) -> None:
    """The one thing AuRE cannot derive, so the one thing worth refusing over."""
    _sample_with_data(project, notes="# S\n\n## Description\n\n<!-- a comment -->\n")
    scan = scan_sample(project, "Sample1")

    with pytest.raises(SetupError, match="## Description"):
        compose(
            sample="Sample1", scan=scan, notes="# S\n\n## Description\n", root=project
        )


def test_compose_carries_the_hypothesis_from_the_notes(project: Path) -> None:
    """What the scientist already suspects is worth more than a blank slate."""
    notes = DESCRIBED + "\n## Fits to perform\n\nThere may be a CuOx skin.\n"
    _sample_with_data(project, notes=notes)
    scan = scan_sample(project, "Sample1")

    composed = compose(
        sample="Sample1", scan=scan, notes=notes, root=project, run=218386
    )

    assert "CuOx" in composed.document["hypothesis"]


def test_compose_requires_a_run_when_the_sample_has_several(project: Path) -> None:
    """Two conditions is a co-refinement, which is not a first fit."""
    _sample_with_data(project, run=218386)
    _sample_with_data(project, run=218393)
    scan = scan_sample(project, "Sample1")

    with pytest.raises(SetupError, match="--run"):
        compose(sample="Sample1", scan=scan, notes=DESCRIBED, root=project)


def test_compose_writes_no_absolute_paths(project: Path) -> None:
    """The defect this project exists to remove must not re-enter through AuRE."""
    _sample_with_data(project)
    scan = scan_sample(project, "Sample1")

    composed = compose(
        sample="Sample1", scan=scan, notes=DESCRIBED, root=project, run=218386
    )

    rendered = repr(composed.document)
    assert str(project) not in rendered
    assert "/Users/" not in rendered


# --------------------------------------------------------------------------
# The one that matters
# --------------------------------------------------------------------------


@pytest.mark.integration
def test_composed_setup_loads_in_aure(project: Path) -> None:
    """AuRE itself must accept the document, not just our reading of its schema.

    It rejects unknown top-level keys, resolves every data file, and validates
    the state against the instrument its filenames imply -- so this covers the
    combined/partial mixing rule and the shared-set-id rule at the same time.
    """
    pytest.importorskip("aure")
    import yaml

    from nr_workbench.aure_adapter import validate_setup
    from nr_workbench.aure_setup import setup_dir

    _sample_with_data(project)
    scan = scan_sample(project, "Sample1")
    composed = compose(
        sample="Sample1", scan=scan, notes=DESCRIBED, root=project, run=218386
    )

    target = setup_dir(project, "Sample1", "Sample1-218386") / "setup.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(composed.document, sort_keys=False), "utf-8")

    loaded = validate_setup(target)

    assert len(loaded["states"]) == 1
    assert len(loaded["states"][0]["data_files"]) == 3


def test_choose_run_says_how_to_get_data_when_there_is_none(project: Path) -> None:
    """First contact with an empty sample; the message is the whole value."""
    from nr_workbench.aure_setup import choose_run

    scan = scan_sample(project, "Sample1")

    with pytest.raises(SetupError, match="nrw sample scan"):
        choose_run(scan, None)


def test_choose_run_lists_what_it_found_for_a_bad_run(project: Path) -> None:
    """A --run typo should not require going and looking."""
    from nr_workbench.aure_setup import choose_run

    _sample_with_data(project)
    scan = scan_sample(project, "Sample1")

    with pytest.raises(SetupError, match="218386"):
        choose_run(scan, 999999)


def test_validate_setup_reports_aure_being_absent_as_such(
    project: Path, monkeypatch
) -> None:
    """ "AuRE is not installed" is our problem; "your data file is missing" is
    not, and the two must not share an exception -- one warrants a bug report.
    """
    import builtins

    from nr_workbench.aure_adapter import AureUnavailableError

    real_import = builtins.__import__

    def _blocked(name, *args, **kwargs):
        if name.startswith("aure"):
            raise ImportError(f"No module named {name!r}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _blocked)
    target = project / "setup.yaml"
    target.write_text("states: []\n", encoding="utf-8")

    from nr_workbench.aure_adapter import validate_setup

    with pytest.raises(AureUnavailableError, match="aure"):
        validate_setup(target)
