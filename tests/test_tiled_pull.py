"""`nrw tiled pull`: a colleague who starts fresh brings back the catalog, the
reduced files and the fits' models, and runs each fit again here.

One project pushes, and a colleague's, made fresh, pulls; both on the fake
client of `tests/test_tiled_sync.py`.
"""

# The fixtures are imported and requested by name, which ruff reads as
# redefinitions.
# ruff: noqa: F811

from __future__ import annotations

import os
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from nr_workbench.cli import main
from nr_workbench.experiment.model import RunChange, RunKey
from nr_workbench.experiment.store import ParquetCatalogStore
from nr_workbench.provenance.whence import Freshness
from nr_workbench.tiled.analysis import ENVELOPE

from .test_tiled_analysis import (  # noqa: F401 - fixtures
    DATA,
    SCRIPT,
    SPEC,
    STEADY,
    A,
    B,
    experiment,
    fitted,
    pushed,
    starred,
)
from .test_tiled_sync import (  # noqa: F401 - fixtures
    KEY,
    NOW,
    SEGMENT,
    FakeNode,
    FakeTiled,
    catalogued,
    described,
    nrw,
    on_tiled,
    project_env,
    tiled,
)


@pytest.fixture
def colleague(tmp_path_factory, context) -> Path:
    """A project of the same experiment, made fresh: a colleague's."""
    from nr_workbench.commands.init_cmd import plan_project_files
    from nr_workbench.project.scaffold import apply_scaffold

    root = tmp_path_factory.mktemp("colleague")
    apply_scaffold(root, plan_project_files(context))
    return root


def pulled(project: Path, *args: str):
    return nrw("pull", "--root", str(project), "--yes", *args)


def catalog_of(project: Path):
    return ParquetCatalogStore.for_project(project).load()


# --------------------------------------------------------------------------
# --context
# --------------------------------------------------------------------------


def test_a_colleague_pulls_the_catalog_and_it_then_matches_tileds(
    project: Path, colleague: Path, tiled: FakeTiled, experiment: FakeNode
) -> None:
    catalogued(project)
    described(project, summary="Does Li plate on Cu?")
    pushed(project)

    shown = pulled(colleague, "--context")

    assert shown.exit_code == 0, shown.output
    assert "4 new, 0 changed, 0 the same" in shown.output
    catalog = catalog_of(colleague)
    assert catalog.runs[RunKey(234277)].condition == "OCV"
    assert catalog.runs[RunKey(234280)].sample_id == "S1"
    assert catalog.samples["S1"].title == "Cu/Pt"
    assert catalog.experiment.summary == "Does Li plate on Cu?"
    # Its own revisions, and the same records: a push from here has nothing
    # to write.
    again = nrw("push", "--root", str(colleague), "--yes")
    assert "Tiled already holds this catalog." in again.output


def test_a_record_that_differs_is_replaced_by_tileds_once_the_person_says_so(
    project: Path, colleague: Path, tiled: FakeTiled, experiment: FakeNode
) -> None:
    catalogued(project)
    pushed(project)
    ParquetCatalogStore.for_project(colleague).update(
        runs=[RunChange(RunKey(234277), 0, {"sample_id": "S1", "condition": "dry"})],
        now=NOW,
    )

    declined = CliRunner().invoke(
        main, ["tiled", "pull", "--context", "--root", str(colleague)], input="n\n"
    )
    dry = pulled(colleague, "--context", "--dry-run")
    kept = catalog_of(colleague).runs[RunKey(234277)].condition
    shown = pulled(colleague, "--context")

    assert declined.exit_code == 1
    assert (
        "Write Tiled's copy over this project's catalog, and apply it to samples/?"
        in declined.output
    )
    assert "Nothing written (--dry-run)." in dry.output
    assert kept == "dry"
    assert shown.exit_code == 0, shown.output
    assert "~ runs/234277-steady  (condition)" in shown.output
    assert catalog_of(colleague).runs[RunKey(234277)].condition == "OCV"


def test_a_record_only_here_is_left_and_counted(
    project: Path, colleague: Path, tiled: FakeTiled, experiment: FakeNode
) -> None:
    catalogued(project)
    pushed(project)
    ParquetCatalogStore.for_project(colleague).update(
        runs=[RunChange(RunKey(234999), 0, {"sample_id": "S9"})], now=NOW
    )

    shown = pulled(colleague, "--context")

    assert shown.exit_code == 0, shown.output
    assert "1 here alone (left as they are)" in shown.output
    assert catalog_of(colleague).runs[RunKey(234999)].sample_id == "S9"


def test_a_save_made_while_the_person_is_asked_writes_nothing(
    project: Path, colleague: Path, tiled: FakeTiled, experiment: FakeNode, monkeypatch
) -> None:
    catalogued(project)
    pushed(project)
    store = ParquetCatalogStore.for_project(colleague)
    store.update(
        runs=[RunChange(RunKey(234277), 0, {"sample_id": "S1", "condition": "dry"})],
        now=NOW,
    )

    def meanwhile(*args, **kwargs) -> bool:
        # The page saves the run while the person reads the question.
        store.update(runs=[RunChange(RunKey(234277), 1, {"note": "new"})], now=NOW)
        return True

    monkeypatch.setattr(click, "confirm", meanwhile)

    shown = nrw("pull", "--context", "--root", str(colleague))

    assert shown.exit_code == 1
    assert "pull again" in shown.output
    assert catalog_of(colleague).runs[RunKey(234277)].condition == "dry"


def test_records_the_rules_refuse_together_are_said_and_nothing_is_written(
    project: Path, colleague: Path, tiled: FakeTiled, experiment: FakeNode
) -> None:
    """Each alone is a sample the catalog takes; S1 and s1 together are one
    folder on macOS."""
    catalogued(project)
    pushed(project)
    experiment["context/samples"].create_container(
        "s1", metadata={"sample_id": "s1", "title": "another"}
    )

    shown = pulled(colleague, "--context")

    assert shown.exit_code == 1
    assert "! these records together:" in shown.output
    assert "only in case" in shown.output
    assert catalog_of(colleague).samples == {}


def test_a_record_the_catalogs_rules_refuse_is_named_and_nothing_is_written(
    project: Path, colleague: Path, tiled: FakeTiled, experiment: FakeNode
) -> None:
    catalogued(project)
    pushed(project)
    experiment["context/runs/234277-steady"].metadata["condition"] = "OCV | 25 C"

    shown = pulled(colleague, "--context")

    assert shown.exit_code == 1
    assert "! runs/234277-steady: condition contains '|'" in shown.output
    assert "Nothing was written" in shown.output
    assert catalog_of(colleague).runs == {}


# --------------------------------------------------------------------------
# --fits
# --------------------------------------------------------------------------


def check_inputs(directory: Path, root: Path):
    from nr_workbench.provenance.whence import check_inputs

    return check_inputs(directory, root)


def index_of(root: Path):
    from nr_workbench.provenance.index import FitIndex

    return FitIndex(root / ".nrw" / "index.jsonl")


def curation_of(root: Path, fit_id: str):
    from nr_workbench.provenance.curation import replay

    return replay(index_of(root).entries()).of(fit_id)


def test_a_fits_pull_rebuilds_each_fit_as_it_was_recorded(
    project: Path, colleague: Path, tiled: FakeTiled, experiment: FakeNode
) -> None:
    import json

    ours = fitted(project)
    starred(project)
    pushed(project)

    shown = pulled(colleague, "--fits")

    assert shown.exit_code == 0, shown.output
    assert (
        f"+ S1/{A}  starred, dream, chisq 1.2: samples/S1/results/{A}/, "
        f"{STEADY}, samples/S1/models/film.yaml, samples/S1/models/film.py"
    ) in shown.output
    assert f"nrw fit run --from {A}" in shown.output
    theirs = colleague / "samples" / "S1" / "results" / A
    for name in (
        "model.py",
        "spec.yaml",
        "NOTES.md",
        "fit/film.json",
        "fit/film.par",
        "fit/film-err.json",
        "fit/film-1-refl.dat",
        "fit/film-1-slabs.dat",
    ):
        assert (theirs / name).read_bytes() == (ours / name).read_bytes(), name
    for name in ("inputs.json", "env/versions.json"):
        assert json.loads((theirs / name).read_text()) == json.loads(
            (ours / name).read_text()
        ), name
    # The manifest as the record carries it: without its envelope's keys.
    manifest = json.loads((ours / "manifest.json").read_text())
    assert json.loads((theirs / "manifest.json").read_text()) == {
        key: value for key, value in manifest.items() if key not in ENVELOPE
    }
    profile = (theirs / "fit" / "film-1-profile.dat").read_text().splitlines()
    assert profile[0].startswith("#") and len(profile) <= 401
    assert (colleague / STEADY).read_bytes() == DATA
    assert (theirs / "data" / STEADY).read_bytes() == DATA
    assert check_inputs(theirs, colleague)[1] is Freshness.FRESH
    assert not (theirs / "fit" / "film.mc.gz").exists()
    assert index_of(colleague).find(A)["chisq"] == 1.2
    assert curation_of(colleague, A).starred
    models = colleague / "samples" / "S1" / "models"
    assert (models / "film.py").read_bytes() == SCRIPT
    assert (models / "film.yaml").read_text() == SPEC


def test_a_final_fit_is_pulled_with_why_it_is_the_answer(
    project: Path, colleague: Path, tiled: FakeTiled, experiment: FakeNode
) -> None:
    from nr_workbench.project.layout import ProjectLayout
    from nr_workbench.provenance import curation

    fitted(project)
    curation.promote(ProjectLayout(root=project), A, reason="the dry reference")
    pushed(project)

    shown = pulled(colleague, "--fits")

    assert shown.exit_code == 0, shown.output
    (ours,) = [e for e in index_of(project).entries() if e["event"] == "promote"]
    held = curation_of(colleague, A).promotions["final"]
    said = ("reason", "who", "at", "forced")
    assert {k: held[k] for k in said} == {k: ours[k] for k in said}


def test_a_label_another_fit_holds_here_stays_that_fits(
    project: Path, colleague: Path, tiled: FakeTiled, experiment: FakeNode
) -> None:
    from nr_workbench.project.layout import ProjectLayout
    from nr_workbench.provenance import curation

    fitted(project)
    curation.promote(ProjectLayout(root=project), A, reason="theirs")
    pushed(project)
    fitted(colleague, B)
    curation.promote(ProjectLayout(root=colleague), B, reason="mine")

    shown = pulled(colleague, "--fits")

    assert shown.exit_code == 0, shown.output
    assert f"final: {B} holds it here, and keeps it" in shown.output
    assert curation_of(colleague, B).labels == ("final",)
    assert curation_of(colleague, A).labels == ()
    assert index_of(colleague).find(A) is not None


def test_a_fit_the_index_holds_already_is_left_whole(
    project: Path, colleague: Path, tiled: FakeTiled, experiment: FakeNode
) -> None:
    fitted(project)
    starred(project)
    pushed(project)
    pulled(colleague, "--fits")
    notes = colleague / "samples" / "S1" / "results" / A / "NOTES.md"
    notes.write_text("# The colleague's own notes\n")

    again = pulled(colleague, "--fits")

    assert again.exit_code == 0, again.output
    assert f"= S1/{A}  starred, dream, chisq 1.2: here already" in again.output
    assert notes.read_text() == "# The colleague's own notes\n"
    assert [e["fit_id"] for e in index_of(colleague).fits()] == [A]


def test_a_file_there_already_is_left_and_the_rest_are_written(
    project: Path, colleague: Path, tiled: FakeTiled, experiment: FakeNode
) -> None:
    """A pull stopped partway leaves the files it wrote, and no index line:
    the next one writes the rest."""
    ours = fitted(project)
    starred(project)
    pushed(project)
    notes = colleague / "samples" / "S1" / "results" / A / "NOTES.md"
    notes.parent.mkdir(parents=True)
    notes.write_text("# Written before\n")

    shown = pulled(colleague, "--fits")

    assert shown.exit_code == 0, shown.output
    assert notes.read_text() == "# Written before\n"
    assert (notes.parent / "model.py").read_bytes() == (ours / "model.py").read_bytes()
    assert index_of(colleague).find(A) is not None


def test_a_reduced_file_here_already_is_left_and_the_fit_keeps_its_own_copy(
    project: Path, colleague: Path, tiled: FakeTiled, experiment: FakeNode
) -> None:
    """The facility's reduction, applied here first, under the same name: the
    fit reads as stale here, and a push still carries the file it read."""
    fitted(project)
    starred(project)
    pushed(project)
    reduced_again = b"# Reduction 2.11\n0.01 0.9 0.1 0.001\n"
    (colleague / STEADY).parent.mkdir(parents=True)
    (colleague / STEADY).write_bytes(reduced_again)

    shown = pulled(colleague, "--fits")
    push = nrw("push", "--root", str(colleague), "--yes", "--only", "fits")

    assert shown.exit_code == 0, shown.output
    assert f"{STEADY} holds another file here, left" in shown.output
    assert (colleague / STEADY).read_bytes() == reduced_again
    theirs = colleague / "samples" / "S1" / "results" / A
    assert (theirs / "data" / STEADY).read_bytes() == DATA
    assert check_inputs(theirs, colleague)[1] is Freshness.STALE
    assert "0 new, 0 changed, 1 the same" in push.output, push.output
    carried = experiment[f"analysis/S1/{A}"].metadata["data"][STEADY]
    assert carried["text"].encode() == DATA


def test_a_fit_with_no_problem_file_says_to_run_its_script(
    project: Path, colleague: Path, tiled: FakeTiled, experiment: FakeNode
) -> None:
    (fitted(project) / "fit" / "film.json").unlink()
    starred(project)
    pushed(project)

    shown = pulled(colleague, "--fits")

    assert shown.exit_code == 0, shown.output
    assert "      nrw fit run samples/S1/models/film.py\n" in shown.output
    assert "--from" not in shown.output


def test_a_model_of_the_same_name_here_is_kept_and_the_fits_written_beside_it(
    project: Path, colleague: Path, tiled: FakeTiled, experiment: FakeNode
) -> None:
    fitted(project)
    starred(project)
    pushed(project)
    models = colleague / "samples" / "S1" / "models"
    models.mkdir(parents=True)
    (models / "film.py").write_text("# the colleague's own film\n")

    shown = pulled(colleague, "--fits")

    assert shown.exit_code == 0, shown.output
    assert (models / "film.py").read_text() == "# the colleague's own film\n"
    assert (models / "film-2.py").read_bytes() == SCRIPT
    assert "samples/S1/models/film-2.py" in shown.output


def test_a_script_with_no_spec_is_written_alone(
    project: Path, colleague: Path, tiled: FakeTiled, experiment: FakeNode
) -> None:
    directory = fitted(project)
    (directory / "spec.yaml").unlink()
    starred(project)
    pushed(project)

    shown = pulled(colleague, "--fits")

    models = colleague / "samples" / "S1" / "models"
    assert shown.exit_code == 0, shown.output
    assert (models / "film.py").read_bytes() == SCRIPT
    assert not (models / "film.yaml").exists()


def test_a_fits_dry_run_writes_nothing(
    project: Path, colleague: Path, tiled: FakeTiled, experiment: FakeNode
) -> None:
    fitted(project)
    starred(project)
    pushed(project)

    shown = pulled(colleague, "--fits", "--dry-run")

    assert f"+ S1/{A}  starred" in shown.output
    assert "Nothing written (--dry-run)." in shown.output
    assert not (colleague / "samples" / "S1" / "results").exists()
    assert not (colleague / "samples" / "S1" / "models" / "film.py").exists()
    assert index_of(colleague).fits() == []


def test_two_fits_of_one_model_are_named_apart_in_a_dry_run_as_in_a_pull(
    project: Path, colleague: Path, tiled: FakeTiled, experiment: FakeNode
) -> None:
    """The final fit takes the model's own name; the other is written beside it."""
    from nr_workbench.project.layout import ProjectLayout
    from nr_workbench.provenance import curation

    refined = b"# film, refined\nproblem = None\n"
    fitted(project)
    starred(project)
    (fitted(project, B) / "model.py").write_bytes(refined)
    curation.promote(ProjectLayout(root=project), B, reason="the answer")
    pushed(project)

    dry = pulled(colleague, "--fits", "--dry-run")
    real = pulled(colleague, "--fits")

    for shown in (dry, real):
        assert shown.exit_code == 0, shown.output
        lines = shown.output.splitlines()
        (final,) = [line for line in lines if line.startswith(f"  + S1/{B}  final")]
        (other,) = [line for line in lines if line.startswith(f"  + S1/{A}  starred")]
        assert final.endswith("samples/S1/models/film.yaml, samples/S1/models/film.py")
        assert other.endswith(
            "samples/S1/models/film-2.yaml, samples/S1/models/film-2.py"
        )
    models = colleague / "samples" / "S1" / "models"
    assert (models / "film.py").read_bytes() == refined
    assert (models / "film-2.py").read_bytes() == SCRIPT


def test_a_fit_set_aside_is_listed_and_not_pulled(
    project: Path, colleague: Path, tiled: FakeTiled, experiment: FakeNode
) -> None:
    from nr_workbench.project.layout import ProjectLayout
    from nr_workbench.provenance import curation

    fitted(project)
    starred(project)
    pushed(project)
    curation.discard(ProjectLayout(root=project), A, reason="diverged")
    pushed(project)

    shown = pulled(colleague, "--fits")

    assert shown.exit_code == 0, shown.output
    assert f"- S1/{A}  discarded, dream, chisq 1.2: not pulled" in shown.output
    assert not (colleague / "samples" / "S1" / "results" / A).exists()
    assert not (colleague / "samples" / "S1" / "models" / "film.py").exists()


@pytest.mark.parametrize(
    "field", ["absolute data", "data", "problem", "fit file", "curve", "model"]
)
def test_a_record_naming_a_path_outside_its_place_is_refused_whole(
    project: Path,
    colleague: Path,
    tiled: FakeTiled,
    experiment: FakeNode,
    tmp_path: Path,
    field: str,
) -> None:
    fitted(project)
    starred(project)
    pushed(project)
    record = experiment[f"analysis/S1/{A}"].metadata
    outside = tmp_path / "outside.dat"
    value = {
        "absolute data": str(outside),
        "data": os.path.relpath(outside, colleague),
        "problem": "../film.json",
        "fit file": "../film.par",
        "curve": "../../film-1-refl.dat",
        "model": "../../film",
    }[field]
    if field.endswith("data"):
        record["data"] = {value: record["data"][STEADY]}
    elif field == "problem":
        record["problem"] = {**record["problem"], "name": value}
    elif field == "fit file":
        record["fit"] = {value: record["fit"]["film.par"]}
    elif field == "curve":
        record["curves"][0]["refl"]["name"] = value
    else:
        record["model"] = value

    shown = pulled(colleague, "--fits")

    assert shown.exit_code == 0, shown.output
    assert (
        f"- S1/{A}  starred, dream, chisq 1.2: its record names {value!r}, which "
        "would be written outside its place"
    ) in shown.output
    assert not outside.exists()
    assert not (colleague / "samples" / "S1" / "results").exists()
    assert index_of(colleague).fits() == []


@pytest.mark.parametrize("where", ["sample", "fit"])
def test_a_sample_or_fit_named_as_a_path_is_refused(
    project: Path, colleague: Path, tiled: FakeTiled, experiment: FakeNode, where: str
) -> None:
    from .test_tiled_sync import FakeSpec

    fitted(project)
    starred(project)
    pushed(project)
    record = dict(experiment[f"analysis/S1/{A}"].metadata)
    analysis = experiment["analysis"]
    if where == "sample":
        sample = analysis.create_container(
            "..", metadata={"sample_id": ".."}, specs=[FakeSpec("analysis-sample", "1")]
        )
        sample.create_container(
            B, metadata=record, specs=[FakeSpec("analysis-fit", "2")]
        )
        named = ".."
    else:
        named = "../../x"
        experiment["analysis/S1"].create_container(
            named, metadata=record, specs=[FakeSpec("analysis-fit", "2")]
        )

    shown = pulled(colleague, "--fits")

    assert shown.exit_code == 0, shown.output
    assert f"its record names {named!r}" in shown.output
    assert [e["fit_id"] for e in index_of(colleague).fits()] == [A]


def test_a_pulled_fit_line_is_a_plain_fit_line_for_that_fit(
    project: Path, colleague: Path, tiled: FakeTiled, experiment: FakeNode
) -> None:
    """A record's own index line cannot make an event, or speak for another
    fit: here, the colleague's own."""
    fitted(project)
    starred(project)
    pushed(project)
    fitted(colleague, B)
    record = experiment[f"analysis/S1/{A}"].metadata
    record["index"] = {**record["index"], "event": "delete", "fit_id": B}

    shown = pulled(colleague, "--fits")

    assert shown.exit_code == 0, shown.output
    lines = [e for e in index_of(colleague).entries() if e["fit_id"] in (A, B)]
    assert [(e["event"], e["fit_id"]) for e in lines] == [
        ("fit", B),
        ("fit", A),
        ("star", A),
    ]
    assert not curation_of(colleague, B).deleted


def test_a_push_after_a_pull_finds_the_fit_as_tiled_holds_it(
    project: Path, colleague: Path, tiled: FakeTiled, experiment: FakeNode
) -> None:
    """The pulled fit is starred here too, so the colleague's push writes it:
    its record is Tiled's, the reduced files it read still in it."""
    fitted(project)
    starred(project)
    pushed(project)
    pulled(colleague, "--fits")
    before = list(tiled.root.log)

    shown = nrw("push", "--root", str(colleague), "--yes", "--only", "fits")

    assert shown.exit_code == 0, shown.output
    assert "0 new, 0 changed, 1 the same" in shown.output
    assert tiled.root.log == before
    data = experiment[f"analysis/S1/{A}"].metadata["data"][STEADY]
    assert data["text"].encode() == DATA


def test_another_programs_node_in_analysis_is_left_alone(
    project: Path, colleague: Path, tiled: FakeTiled, experiment: FakeNode
) -> None:
    fitted(project)
    starred(project)
    pushed(project)
    experiment["analysis"].create_container("their-notes", metadata={"by": "them"})

    status = nrw("status", "--root", str(colleague))
    shown = pulled(colleague, "--fits")

    assert status.exit_code == 0, status.output
    assert shown.exit_code == 0, shown.output
    assert "/analysis: 1 fit(s)" in shown.output


# --------------------------------------------------------------------------
# All three
# --------------------------------------------------------------------------


def test_context_data_and_fits_are_pulled_in_that_order(
    project: Path,
    colleague: Path,
    tiled: FakeTiled,
    experiment: FakeNode,
    tmp_path_factory,
) -> None:
    catalogued(project)
    fitted(project)
    starred(project)
    pushed(project)
    reduced = FakeNode(content=b"q r")
    reduced.key = SEGMENT
    experiment.children["autoreduction"] = FakeNode({SEGMENT: reduced})
    folder = tmp_path_factory.mktemp("data")

    shown = pulled(colleague, "--fits", "--data", "--context", "--into", str(folder))

    assert shown.exit_code == 0, shown.output
    said = shown.output
    assert said.index("/context, into") < said.index("autoreduction ->")
    assert said.index("autoreduction ->") < said.index("/analysis: 1 fit(s)")
    assert (folder / SEGMENT).read_bytes() == b"q r"


def test_a_colleagues_fresh_project_pulls_the_data_into_its_incoming_folder(
    project: Path, colleague: Path, tiled: FakeTiled, experiment: FakeNode
) -> None:
    """No data folder to set up first: `nrw init` made the project's own."""
    from .experiment_fixtures import reduced_rows

    catalogued(project)
    pushed(project)
    rows = reduced_rows().encode()
    reduced = FakeNode(content=rows)
    reduced.key = SEGMENT
    experiment.children["autoreduction"] = FakeNode({SEGMENT: reduced})

    shown = pulled(colleague, "--context", "--data", "--fits")

    assert shown.exit_code == 0, shown.output
    assert (colleague / "incoming" / SEGMENT).read_bytes() == rows


def test_a_colleagues_pull_applies_the_catalog_to_samples_at_once(
    project: Path,
    colleague: Path,
    tiled: FakeTiled,
    experiment: FakeNode,
    tmp_path: Path,
) -> None:
    """Pulled files land settled: each complete run is copied in straight away."""
    from .experiment_fixtures import write_autoreduced

    catalogued(project)
    pushed(project)
    files = {}
    for run in (234277, 234280):
        for path in write_autoreduced(tmp_path / "reduced", run, [1, 2, 3]):
            node = FakeNode(content=path.read_bytes())
            node.key = path.name
            files[path.name] = node
    experiment.children["autoreduction"] = FakeNode(files)
    sample = colleague / "samples" / "S1"

    dry = pulled(colleague, "--context", "--data", "--dry-run")
    dry_wrote = (sample / "sample.md").exists()
    shown = pulled(colleague, "--context", "--data")

    assert dry.exit_code == 0, dry.output
    assert not dry_wrote
    assert shown.exit_code == 0, shown.output
    assert (sample / "sample.md").is_file()
    copied = sorted(p.name for p in (sample / "data" / "steady").glob("REFL_*"))
    assert copied == sorted(files)


def test_a_data_folder_nrw_cannot_write_stops_the_pull_before_the_catalog(
    project: Path, colleague: Path, tiled: FakeTiled, experiment: FakeNode
) -> None:
    """Not the catalog pulled, the data refused, and the fits never reached."""
    import shutil

    catalogued(project)
    pushed(project)
    shutil.rmtree(colleague / "incoming")

    shown = pulled(colleague, "--context", "--data", "--fits")

    assert shown.exit_code == 1
    assert "`nrw init` creates it. Nothing was pulled." in shown.output
    assert catalog_of(colleague).runs == {}


def test_a_pull_naming_none_of_the_three_pulls_all_three_in_order(
    project: Path,
    colleague: Path,
    tiled: FakeTiled,
    experiment: FakeNode,
    tmp_path_factory,
) -> None:
    catalogued(project)
    fitted(project)
    starred(project)
    pushed(project)
    reduced = FakeNode(content=b"q r")
    reduced.key = SEGMENT
    experiment.children["autoreduction"] = FakeNode({SEGMENT: reduced})
    folder = tmp_path_factory.mktemp("data")

    shown = pulled(colleague, "--into", str(folder))

    assert shown.exit_code == 0, shown.output
    said = shown.output
    assert said.index("/context, into") < said.index("autoreduction ->")
    assert said.index("autoreduction ->") < said.index("/analysis: 1 fit(s)")
    assert catalog_of(colleague).runs[RunKey(234277)].condition == "OCV"
    assert (folder / SEGMENT).read_bytes() == b"q r"
    assert index_of(colleague).find(A)["chisq"] == 1.2


def test_a_pull_naming_one_of_the_three_pulls_that_one_alone(
    project: Path, colleague: Path, tiled: FakeTiled, experiment: FakeNode
) -> None:
    catalogued(project)
    fitted(project)
    starred(project)
    pushed(project)
    reduced = FakeNode(content=b"q r")
    reduced.key = SEGMENT
    experiment.children["autoreduction"] = FakeNode({SEGMENT: reduced})

    shown = pulled(colleague, "--fits")

    assert shown.exit_code == 0, shown.output
    assert "/context" not in shown.output
    assert "autoreduction ->" not in shown.output
    assert catalog_of(colleague).runs == {}
    assert not (colleague / "incoming" / SEGMENT).exists()
    assert index_of(colleague).find(A)["chisq"] == 1.2


def test_no_reduced_data_on_tiled_is_said_and_the_rest_is_pulled(
    project: Path, colleague: Path, tiled: FakeTiled, experiment: FakeNode
) -> None:
    """An experiment pushed before its reduction is on Tiled: what there is,
    is pulled."""
    catalogued(project)
    fitted(project)
    starred(project)
    pushed(project)

    shown = pulled(colleague)

    assert shown.exit_code == 0, shown.output
    assert (
        "projects/isaac/IPTS-00001/autoreduction is not on Tiled, or not shared "
        "with this login: no reduced file to copy."
    ) in shown.output
    assert catalog_of(colleague).runs[RunKey(234277)].condition == "OCV"
    assert index_of(colleague).find(A)["chisq"] == 1.2


def test_a_data_folder_nrw_cannot_write_is_said_and_the_rest_is_pulled(
    project: Path,
    colleague: Path,
    tiled: FakeTiled,
    experiment: FakeNode,
    tmp_path: Path,
) -> None:
    """The facility's mount, read-only, as the data folder: its reduced files
    are there already."""
    from nr_workbench.project.settings import save

    catalogued(project)
    fitted(project)
    starred(project)
    pushed(project)
    reduced = FakeNode(content=b"q r")
    reduced.key = SEGMENT
    experiment.children["autoreduction"] = FakeNode({SEGMENT: reduced})
    mount = tmp_path / "SNS" / "IPTS-00001"
    mount.mkdir(parents=True)
    mount.chmod(0o555)
    save(colleague, {"source.location": str(mount)})

    try:
        shown = pulled(colleague)
    finally:
        mount.chmod(0o755)

    assert shown.exit_code == 0, shown.output
    assert (
        f"No reduced file is copied. {mount} is not a folder nrw can write"
    ) in shown.output
    assert list(mount.iterdir()) == []
    assert catalog_of(colleague).runs[RunKey(234277)].condition == "OCV"
    assert index_of(colleague).find(A)["chisq"] == 1.2


# --------------------------------------------------------------------------
# --ipts: the experiment's project, made when there is none
# --------------------------------------------------------------------------


@pytest.fixture
def desk(tmp_path_factory, monkeypatch) -> Path:
    """The working directory: a folder that is no project, and in none."""
    here = tmp_path_factory.mktemp("desk").resolve()
    monkeypatch.chdir(here)
    return here


def from_tiled(*args: str):
    """A pull that names no project: the one it finds, or makes."""
    return nrw("pull", "--yes", *args)


def test_ipts_outside_a_project_makes_the_experiments_project_and_pulls_into_it(
    project: Path, desk: Path, tiled: FakeTiled, experiment: FakeNode
) -> None:
    """A colleague's whole start: no `nrw init` first."""
    from nr_workbench.project.config import load_config

    from .experiment_fixtures import reduced_rows

    catalogued(project)
    fitted(project)
    starred(project)
    pushed(project)
    rows = reduced_rows().encode()
    reduced = FakeNode(content=rows)
    reduced.key = SEGMENT
    experiment.children["autoreduction"] = FakeNode({SEGMENT: reduced})

    shown = from_tiled("--ipts", "IPTS-00001")

    made = desk / "IPTS-00001"
    assert shown.exit_code == 0, shown.output
    assert load_config(made).ipts == "IPTS-00001"
    assert catalog_of(made).runs[RunKey(234277)].condition == "OCV"
    assert (made / "incoming" / SEGMENT).read_bytes() == rows
    assert index_of(made).find(A)["chisq"] == 1.2
    assert (
        "The project is in IPTS-00001: `cd IPTS-00001` to work in it." in shown.output
    )


@pytest.mark.parametrize("written", ["00001", "ipts-00001"])
def test_an_ipts_written_as_a_number_names_the_same_experiment(
    desk: Path, tiled: FakeTiled, experiment: FakeNode, written: str
) -> None:
    from nr_workbench.project.config import load_config

    shown = from_tiled("--ipts", written, "--context")

    assert shown.exit_code == 0, shown.output
    assert [made.name for made in desk.iterdir()] == ["IPTS-00001"]
    assert load_config(desk / "IPTS-00001").ipts == "IPTS-00001"


def test_ipts_with_root_makes_the_project_in_that_folder(
    project: Path, desk: Path, tiled: FakeTiled, experiment: FakeNode
) -> None:
    catalogued(project)
    pushed(project)

    shown = from_tiled("--ipts", "00001", "--root", "li-on-cu", "--context")

    assert shown.exit_code == 0, shown.output
    assert [made.name for made in desk.iterdir()] == ["li-on-cu"]
    assert catalog_of(desk / "li-on-cu").samples["S1"].title == "Cu/Pt"
    assert "The project is in li-on-cu: `cd li-on-cu`" in shown.output


def test_a_second_pull_with_the_ipts_goes_into_the_project_the_first_made(
    project: Path, desk: Path, tiled: FakeTiled, experiment: FakeNode
) -> None:
    catalogued(project)
    pushed(project)
    from_tiled("--ipts", "00001", "--context")
    made = desk / "IPTS-00001"
    toml = (made / "nrw.toml").read_bytes()

    shown = from_tiled("--ipts", "00001", "--context")

    assert shown.exit_code == 0, shown.output
    assert "The catalog already holds what Tiled does." in shown.output
    assert "Scaffolded" not in shown.output
    assert (made / "nrw.toml").read_bytes() == toml
    assert not (made / "IPTS-00001").exists()


def test_ipts_in_the_experiments_own_project_pulls_into_it(
    project: Path,
    colleague: Path,
    tiled: FakeTiled,
    experiment: FakeNode,
    monkeypatch,
) -> None:
    catalogued(project)
    pushed(project)
    monkeypatch.chdir(colleague / "samples")

    shown = from_tiled("--ipts", "00001", "--context")

    assert shown.exit_code == 0, shown.output
    assert catalog_of(colleague).runs[RunKey(234277)].condition == "OCV"
    assert not (colleague / "samples" / "IPTS-00001").exists()
    assert "The project is in" not in shown.output


def test_ipts_in_another_experiments_project_is_refused_and_nothing_is_written(
    project: Path,
    colleague: Path,
    tiled: FakeTiled,
    experiment: FakeNode,
    monkeypatch,
) -> None:
    """Never one experiment's records in another's catalog, nor its project
    made inside this one."""
    catalogued(project)
    pushed(project)
    tiled.root["projects/isaac"].children["IPTS-00002"] = experiment
    monkeypatch.chdir(colleague)

    shown = from_tiled("--ipts", "00002", "--context")

    assert shown.exit_code == 1
    assert "is the project of IPTS-00001, not of IPTS-00002" in shown.output
    assert catalog_of(colleague).runs == {}
    assert not (colleague / "IPTS-00002").exists()


def test_ipts_for_a_project_that_names_none_is_refused(
    desk: Path, tiled: FakeTiled, experiment: FakeNode
) -> None:
    made = CliRunner().invoke(main, ["init", "unnamed"])
    toml = (desk / "unnamed" / "nrw.toml").read_bytes()

    shown = from_tiled("--ipts", "00001", "--root", "unnamed", "--context")

    assert made.exit_code == 0, made.output
    assert shown.exit_code == 1
    assert "nrw.toml names no IPTS" in shown.output
    assert (desk / "unnamed" / "nrw.toml").read_bytes() == toml


@pytest.mark.parametrize(
    ("args", "why"),
    [
        (("--ipts", "cu-on-pt", "--context"), "--ipts cu-on-pt is not an IPTS"),
        (
            ("--ipts", "00001", "--context", "--dry-run"),
            "is not a project yet, and --dry-run writes nothing",
        ),
        (
            ("--ipts", "00002", "--context"),
            "projects/isaac/IPTS-00002 is not on https://tiled.ornl.gov",
        ),
    ],
)
def test_a_pull_refused_before_its_project_is_made_makes_no_folder(
    desk: Path,
    tiled: FakeTiled,
    experiment: FakeNode,
    args: tuple[str, ...],
    why: str,
) -> None:
    """A mistyped number, or a dry run, leaves no project behind."""
    shown = from_tiled(*args)

    assert shown.exit_code == 1
    assert why in shown.output
    assert list(desk.iterdir()) == []


def test_a_pull_with_no_login_makes_no_folder(desk: Path, tiled: FakeTiled) -> None:
    on_tiled(tiled)

    shown = from_tiled("--ipts", "00001", "--context")

    assert shown.exit_code == 1
    assert "run `nrw tiled login`" in shown.output
    assert list(desk.iterdir()) == []


def test_a_pull_outside_a_project_says_that_ipts_makes_one(
    desk: Path, tiled: FakeTiled, experiment: FakeNode
) -> None:
    shown = from_tiled("--context")

    assert shown.exit_code == 1
    assert "No nrw.toml found" in shown.output
    assert "Or give --ipts" in shown.output
    assert list(desk.iterdir()) == []


def test_the_project_to_make_is_looked_up_on_the_server_its_pull_asks(
    desk: Path, tiled: FakeTiled, experiment: FakeNode
) -> None:
    """A `.env` above the project is not the project's: once it is made, no
    command in it reads that file, so the lookup before does not either."""
    project_env(desk, f"TILED_URL=https://other.example\nTILED_API_KEY={KEY}\n")

    shown = from_tiled("--ipts", "00001", "--context")

    assert shown.exit_code == 0, shown.output
    assert set(tiled.asked) == {("https://tiled.ornl.gov", None)}


def test_a_key_above_the_project_to_make_does_not_stand_in_for_a_login(
    desk: Path, tiled: FakeTiled
) -> None:
    """Else the project is made with the key, and its pull finds no login."""
    on_tiled(tiled)
    project_env(desk, f"TILED_API_KEY={KEY}\n")

    shown = from_tiled("--ipts", "00001", "--context")

    assert shown.exit_code == 1
    assert "run `nrw tiled login`" in shown.output
    assert [left.name for left in desk.iterdir()] == [".env"]


def test_the_env_of_the_folder_to_make_a_project_of_is_read_before_and_after(
    desk: Path, tiled: FakeTiled, experiment: FakeNode
) -> None:
    (desk / "li-on-cu").mkdir()
    project_env(desk / "li-on-cu", "TILED_URL=https://tiled-test.ornl.gov\n")

    shown = from_tiled("--ipts", "00001", "--root", "li-on-cu", "--context")

    assert shown.exit_code == 0, shown.output
    assert set(tiled.asked) == {("https://tiled-test.ornl.gov", None)}


def test_a_folder_that_cannot_be_made_is_said_and_nothing_is_pulled(
    desk: Path, tiled: FakeTiled, experiment: FakeNode
) -> None:
    (desk / "IPTS-00001").write_text("notes", encoding="utf-8")

    shown = from_tiled("--ipts", "00001", "--context")

    assert shown.exit_code == 1
    assert "IPTS-00001 could not be made" in shown.output
    assert "Nothing was pulled." in shown.output
    assert isinstance(shown.exception, SystemExit)
    assert (desk / "IPTS-00001").read_text(encoding="utf-8") == "notes"


# --------------------------------------------------------------------------
# `nrw tiled status`
# --------------------------------------------------------------------------


def test_status_says_what_a_push_writes_in_analysis(
    project: Path, tiled: FakeTiled, experiment: FakeNode
) -> None:
    import json

    from nr_workbench.project.layout import ProjectLayout
    from nr_workbench.provenance import curation

    fitted(project)
    starred(project)
    before = nrw("status", "--root", str(project))
    pushed(project)
    curation.star(ProjectLayout(root=project), A, starred=False)

    after = nrw("status", "--root", str(project))
    found = json.loads(nrw("status", "--root", str(project), "--json").output)

    assert "analysis/   not made yet" in before.output
    assert f"  ~ S1/{A}" in after.output
    assert found["analysis"]["records"]["replace"] == [f"S1/{A}"]


def test_status_says_whether_each_fit_on_tiled_is_here(
    project: Path, colleague: Path, tiled: FakeTiled, experiment: FakeNode
) -> None:
    import json

    fitted(project)
    starred(project)
    pushed(project)

    before = nrw("status", "--root", str(colleague))
    pulled(colleague, "--fits")
    found = json.loads(nrw("status", "--root", str(colleague), "--json").output)

    assert "1 on Tiled alone" in before.output
    assert (
        f"= S1/{A}  starred, dream, chisq 1.2: not here; `nrw tiled pull --fits` "
        "brings it"
    ) in before.output
    (fit,) = found["analysis"]["fits"]
    assert (fit["fit_id"], fit["here"]) == (A, True)
    assert found["analysis"]["records"]["same"] == [f"S1/{A}"]
