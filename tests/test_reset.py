"""Resetting a sample, which has to include the index or it does nothing.

Deleting result directories by hand looks like it works and does not. The index
still records the fits, `nrw ls` reports them BROKEN forever, and an unattended
session -- which reads the index rather than the directory -- keeps numbering
from models it can no longer see. A sample whose output had been deleted still
started its next model at `corefine6`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from nr_workbench.cli import main
from nr_workbench.provenance.index import EVENT_PROMOTE, FitIndex

pytestmark = pytest.mark.integration


def invoke(root: Path, monkeypatch: pytest.MonkeyPatch, *args: str):
    monkeypatch.chdir(root)
    return CliRunner().invoke(main, list(args))


def populate(project: Path, sample: str = "Sample1") -> FitIndex:
    """Two fits with directories, models, and index entries."""
    index = FitIndex(project / ".nrw" / "index.jsonl")
    for fit_id, model in (("20260811-1-aaaaaaaa", "m1"), ("20260811-2-bbbbbbbb", "m2")):
        directory = project / "samples" / sample / "results" / fit_id
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "NOTES.md").write_text("x", encoding="utf-8")
        index.append(
            {"fit_id": fit_id, "sample": sample, "model": model, "status": "ok"}
        )
    models = project / "samples" / sample / "models"
    models.mkdir(parents=True, exist_ok=True)
    for name in ("m1.yaml", "m1.py", "m2.yaml"):
        (models / name).write_text("x", encoding="utf-8")
    return index


def test_reset_clears_results_models_and_index_together(project: Path, monkeypatch):
    index = populate(project)

    result = invoke(project, monkeypatch, "sample", "reset", "Sample1", "--yes")

    assert result.exit_code == 0, result.output
    kept = project / "samples/Sample1/results"
    assert [p for p in kept.glob("*") if p.is_dir()] == []
    assert [p.name for p in kept.glob("*")] == [".gitkeep"], "layout survives"
    models = [
        p.name
        for p in (project / "samples/Sample1/models").glob("*")
        if p.name != ".gitkeep"
    ]
    assert models == []
    assert index.fits(sample="Sample1") == [], "the index is the memory"


def test_dry_run_changes_nothing(project: Path, monkeypatch) -> None:
    index = populate(project)

    result = invoke(project, monkeypatch, "sample", "reset", "Sample1", "--dry-run")

    assert result.exit_code == 0, result.output
    assert len(index.fits(sample="Sample1")) == 2
    assert [p for p in (project / "samples/Sample1/results").glob("*") if p.is_dir()]


def test_data_and_notes_survive(project: Path, monkeypatch) -> None:
    """`data/` is the measurement and `reports/` is what a person wrote."""
    populate(project)
    data = project / "samples/Sample1/data/steady/keep.txt"
    data.parent.mkdir(parents=True, exist_ok=True)
    data.write_text("0.01 1.0 0.1 0.001\n", encoding="utf-8")
    report = project / "samples/Sample1/reports/findings.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("what I concluded", encoding="utf-8")

    invoke(project, monkeypatch, "sample", "reset", "Sample1", "--yes")

    assert data.is_file()
    assert report.read_text(encoding="utf-8") == "what I concluded"


def test_a_promoted_fit_blocks_the_reset(project: Path, monkeypatch) -> None:
    """Promotion is a separate decision on purpose; resetting past one would
    silently unpublish a result something may already cite."""
    index = populate(project)
    index.append(
        {
            "fit_id": "20260811-1-aaaaaaaa",
            "sample": "Sample1",
            "label": "final",
            "reason": "best",
        },
        event=EVENT_PROMOTE,
    )

    result = invoke(project, monkeypatch, "sample", "reset", "Sample1", "--yes")

    assert result.exit_code != 0
    assert "promoted" in result.output
    assert len(index.fits(sample="Sample1")) == 2, "nothing removed"


def test_other_samples_are_untouched(project: Path, monkeypatch) -> None:
    index = populate(project)
    index.append(
        {
            "fit_id": "20260811-9-cccccccc",
            "sample": "Other",
            "model": "z",
            "status": "ok",
        }
    )

    invoke(project, monkeypatch, "sample", "reset", "Sample1", "--yes")

    assert len(index.fits(sample="Other")) == 1


def test_forget_leaves_an_unreadable_line_alone(project: Path) -> None:
    """The index may have been touched by hand; a line we cannot parse is not
    ours to discard."""
    index = populate(project)
    with index.path.open("a", encoding="utf-8") as handle:
        handle.write("{not json\n")

    index.forget("Sample1")

    assert "{not json" in index.path.read_text(encoding="utf-8")


def test_resetting_an_empty_sample_says_so(project: Path, monkeypatch) -> None:
    result = invoke(project, monkeypatch, "sample", "reset", "Sample1", "--yes")

    assert result.exit_code == 0
    assert "nothing to reset" in result.output
