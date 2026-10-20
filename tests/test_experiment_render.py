"""sample.md rendered from the catalog, and read back by everything that reads it.

About twenty modules read sample.md, each its own way: the agent takes its task
from one section, ISAAC export takes conditions from the table, `nrw data
reconcile` compares the table with the file headers, `nrw sample scan` counts
the runs it mentions. A rendered file is only correct if every one of them
reads what the catalog says -- so the central test here asks all of them.
"""

from __future__ import annotations

import dataclasses
import shutil
from pathlib import Path

import pytest

from nr_workbench.experiment.model import (
    Catalog,
    ExperimentChange,
    RunChange,
    RunKey,
    SampleChange,
    apply_changes,
)
from nr_workbench.experiment.render import (
    CATALOG_OWNER,
    SampleRenderError,
    plan_sample,
    prose_for,
)
from nr_workbench.project.render import RenderContext
from nr_workbench.project.samples import plan_sample_files
from nr_workbench.project.scaffold import (
    Outcome,
    PlannedFile,
    apply_scaffold,
    classify,
    sha256_bytes,
)

from .experiment_fixtures import REFERENCE_STEADY

GOLDEN = Path(__file__).parent / "data" / "experiment"
NOW = "2026-09-25T12:00:00Z"

FITS = "Co-refine 218386 and 218393. The oxide should thicken between them."
DESCRIPTION = "About 30 nm Cu on Pt, in 1 M LiBF4 in d8-THF."
DETAILS = "Cu 20 nm, Pt 5 nm, Ti adhesion layer, Si substrate."


def reference_catalog(**overrides) -> Catalog:
    """218386 at OCV, 218393 recorded (wrongly) as OCV -- its title says CA."""
    runs = [
        RunChange(
            RunKey(218386),
            0,
            {"sample_id": "Sample6", "measurement": "full Q", "condition": "OCV"},
        ),
        RunChange(
            RunKey(218393),
            0,
            {
                "sample_id": "Sample6",
                "measurement": "full Q",
                "condition": overrides.pop("condition_218393", "OCV"),
            },
        ),
    ]
    context = {
        "title": "Cu/Pt in d8-THF (expt 11)",
        "description": DESCRIPTION,
        "details": DETAILS,
        "mounting": "once",
        "measurement_conditions": "Background looked flat at high Q.",
        "fits_to_perform": FITS,
    }
    context.update(overrides)
    return apply_changes(
        Catalog(), runs=runs, samples=[SampleChange("Sample6", 0, context)], now=NOW
    )


def render_into(project: Path, context: RenderContext, catalog: Catalog) -> str:
    """Plan and apply Sample6 from *catalog*, with the reference data copied in."""
    apply_scaffold(project, plan_sample(project, context, "Sample6", catalog=catalog))
    steady = project / "samples" / "Sample6" / "data" / "steady"
    for path in REFERENCE_STEADY.glob("*.txt"):
        shutil.copy(path, steady / path.name)
    return (project / "samples" / "Sample6" / "sample.md").read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# A sample the catalog does not manage is exactly as it always was
# --------------------------------------------------------------------------


def test_the_blank_render_is_byte_identical_to_the_scaffold(
    context: RenderContext,
) -> None:
    planned = {p.relpath: p.content for p in plan_sample_files(context, "Sample1")}

    assert (
        planned["samples/Sample1/sample.md"]
        == (GOLDEN / "blank_sample.md").read_bytes()
    )
    assert (
        planned["samples/Sample1/sample.yaml"]
        == (GOLDEN / "blank_sample.yaml").read_bytes()
    )


def test_plan_sample_without_a_catalog_is_the_scaffold(
    project: Path, context: RenderContext
) -> None:
    ours = plan_sample(project, context, "Sample9")
    theirs = plan_sample_files(context, "Sample9")

    assert [(p.relpath, p.content, p.owner) for p in ours] == [
        (p.relpath, p.content, None) for p in theirs
    ]


# --------------------------------------------------------------------------
# Every reader agrees with the catalog
# --------------------------------------------------------------------------


def test_applied_reference_sample_md_every_reader_agrees(
    project: Path, context: RenderContext
) -> None:
    from nr_workbench.agent.session import declared_task
    from nr_workbench.aure_setup import describe_sample, read_hypothesis
    from nr_workbench.conditions import from_table
    from nr_workbench.instrument.header import read_header
    from nr_workbench.project.scan import scan_sample
    from nr_workbench.reconcile import documented_runs, reconcile
    from nr_workbench.web.project import ProjectData
    from nr_workbench.web.prose import render

    text = render_into(project, context, reference_catalog())

    # The unattended agent's task, and AuRE's description and hypothesis.
    assert declared_task(text) == FITS
    assert read_hypothesis(text) == FITS
    assert describe_sample(text) == f"{DESCRIPTION}\n\n{DETAILS}"
    # ISAAC export's conditions, and reconcile's table.
    assert from_table(text, "218386") == "OCV"
    assert from_table(text, "218393") == "OCV"
    assert set(documented_runs(text)) == {218386, 218393}
    # The scan: every run on disk documented, nothing documented missing.
    scan = scan_sample(project, "Sample6")
    assert scan.documented_but_absent == [] and scan.present_but_undocumented == []
    # The page's title and rendering.
    assert ProjectData(project)._sample_title("Sample6") == "Cu/Pt in d8-THF (expt 11)"
    html = render(text)
    assert "<table>" in html and "<!--" not in html
    assert "Mounted once and not moved" in html

    # And reconcile still catches the real mislabelling: 218393's title says
    # CA, the table says OCV. Copying titles into the table would have hidden it.
    steady = project / "samples" / "Sample6" / "data" / "steady"
    headers = [read_header(p) for p in sorted(steady.glob("*.txt"))]
    findings = reconcile("Sample6", headers, text).findings
    flagged = {f.run for f in findings if f.kind == "state-contradicts-notes"}
    assert 218393 in flagged and 218386 not in flagged


def test_watch_sees_the_rendered_sample_as_it_sees_a_hand_written_one(
    project: Path, context: RenderContext
) -> None:
    from nr_workbench.agent import watch

    render_into(project, context, reference_catalog())

    verdicts = watch.assess(
        project, "Sample6", watch.WatchState(), settle_seconds=0, now=4e9
    )

    assert {v.run for v in verdicts if v.ready} == {218386, 218393}


def test_render_empty_condition_from_table_not_type(
    project: Path, context: RenderContext
) -> None:
    """An empty Condition cell once made ISAAC record the Type as the condition."""
    from nr_workbench.conditions import from_table

    text = render_into(project, context, reference_catalog(condition_218393=""))

    assert from_table(text, "218393") is None
    assert from_table(text, "218386") == "OCV"


@pytest.mark.parametrize(
    "description",
    [
        pytest.param("Compare with run 218387 from June.", id="six_digits"),
        pytest.param("Plot {{ 7*7 }} and </script> verbatim.", id="markup"),
        pytest.param("A dash -- and a > sign.", id="punctuation"),
    ],
)
def test_accepted_text_reads_back_verbatim_everywhere(
    project: Path, context: RenderContext, description: str
) -> None:
    from nr_workbench.agent.session import declared_task
    from nr_workbench.aure_setup import describe_sample
    from nr_workbench.conditions import from_table
    from nr_workbench.reconcile import documented_runs

    text = render_into(project, context, reference_catalog(description=description))

    assert describe_sample(text).startswith(description)
    assert declared_task(text) == FITS
    assert from_table(text, "218386") == "OCV"
    assert set(documented_runs(text)) == {218386, 218393}


def test_excluded_runs_are_not_tabulated() -> None:
    catalog = apply_changes(
        reference_catalog(),
        runs=[RunChange(RunKey(218393), 1, {"include": False})],
        now=NOW,
    )

    assert [row.run for row in prose_for(catalog, "Sample6").measurements] == [218386]


@pytest.mark.parametrize(
    "mounting,expected,absent",
    [
        ("once", "Mounted once", "Remounted"),
        ("remounted", "Remounted or realigned", "Mounted once"),
        ("unknown", None, "ounted"),
    ],
)
def test_mounting_is_written_only_as_a_claim_someone_made(
    mounting, expected, absent
) -> None:
    """`unknown` must not read as "mounted once": that decides per-model alignment."""
    conditions = prose_for(
        reference_catalog(mounting=mounting), "Sample6"
    ).measurement_conditions

    if expected:
        assert conditions.startswith(expected)
    else:
        assert conditions == "Background looked flat at high Q."
    assert absent not in conditions


# --------------------------------------------------------------------------
# The reset trap: nothing may plan the blank template over a rendered file
# --------------------------------------------------------------------------


def test_sample_new_after_apply_sample_md_unchanged(
    project: Path, context: RenderContext, monkeypatch
) -> None:
    from nr_workbench.commands.sample import run_sample_new
    from nr_workbench.experiment.store import ParquetCatalogStore

    store = ParquetCatalogStore.for_project(project)
    catalog = reference_catalog()
    store.update(
        runs=[
            RunChange(e.key, 0, {"sample_id": e.sample_id, "condition": e.condition})
            for e in catalog.runs.values()
        ],
        samples=[
            SampleChange("Sample6", 0, {"title": "Cu/Pt", "fits_to_perform": FITS})
        ],
        now=NOW,
    )
    apply_scaffold(project, plan_sample(project, context, "Sample6"))
    rendered = (project / "samples" / "Sample6" / "sample.md").read_bytes()
    monkeypatch.chdir(project)

    run_sample_new(sample_id="Sample6")

    sample_dir = project / "samples" / "Sample6"
    assert (sample_dir / "sample.md").read_bytes() == rendered
    # Nor a blank-template "proposal" beside it, which is what planning the
    # scaffold instead of the catalog's render leaves behind.
    assert not (sample_dir / "sample.md.nrw-new").exists()


def test_init_sample_after_apply_sample_md_unchanged(
    project: Path, context: RenderContext
) -> None:
    from nr_workbench.commands.init_cmd import _plan_sample
    from nr_workbench.experiment.store import ParquetCatalogStore

    store = ParquetCatalogStore.for_project(project)
    store.update(
        runs=[RunChange(RunKey(218386), 0, {"sample_id": "Sample6"})],
        samples=[SampleChange("Sample6", 0, {"fits_to_perform": FITS})],
        now=NOW,
    )
    apply_scaffold(project, plan_sample(project, context, "Sample6"))

    report = apply_scaffold(project, _plan_sample(project, context, "Sample6"))

    outcome = {f.relpath: f.outcome for f in report.files}
    assert outcome["samples/Sample6/sample.md"] is Outcome.UNCHANGED


def test_classify_owner_mismatch_is_drifted(tmp_path: Path) -> None:
    """The structural guard: a blank plan never upgrades a catalog-owned file."""
    target = tmp_path / "sample.md"
    target.write_bytes(b"rendered from the catalog\n")
    lock_entry = {
        "template_id": "sample/sample.md.j2",
        "template_version": 1,
        "sha256_at_install": sha256_bytes(target.read_bytes()),
        "owner": CATALOG_OWNER,
    }
    blank = PlannedFile("sample.md", b"blank template\n", "sample/sample.md.j2")

    assert classify(blank, target, lock_entry) is Outcome.DRIFTED


def test_classify_the_catalog_may_upgrade_the_untouched_scaffold(
    tmp_path: Path,
) -> None:
    """The first apply to a sample made by `nrw sample new` must go through."""
    target = tmp_path / "sample.md"
    target.write_bytes(b"blank template\n")
    lock_entry = {
        "template_id": "sample/sample.md.j2",
        "template_version": 1,
        "sha256_at_install": sha256_bytes(target.read_bytes()),
    }
    rendered = PlannedFile(
        "sample.md", b"rendered\n", "sample/sample.md.j2", owner=CATALOG_OWNER
    )

    assert classify(rendered, target, lock_entry) is Outcome.UPGRADE


def test_a_hand_edited_rendered_file_is_never_overwritten(
    project: Path, context: RenderContext
) -> None:
    catalog = reference_catalog()
    apply_scaffold(project, plan_sample(project, context, "Sample6", catalog=catalog))
    path = project / "samples" / "Sample6" / "sample.md"
    edited = path.read_text() + "\nA line the scientist added by hand.\n"
    path.write_text(edited)

    changed = reference_catalog(description="A different description.")
    report = apply_scaffold(
        project, plan_sample(project, context, "Sample6", catalog=changed)
    )

    assert path.read_text() == edited
    assert (path.parent / "sample.md.nrw-new").is_file()
    assert Outcome.DRIFTED in {f.outcome for f in report.files}


def test_an_unreadable_catalog_refuses_rather_than_planning_blank(
    project: Path, context: RenderContext, monkeypatch
) -> None:
    from nr_workbench.commands.sample import run_sample_new
    from nr_workbench.experiment.store import ParquetCatalogStore

    store = ParquetCatalogStore.for_project(project)
    store.update(runs=[RunChange(RunKey(218386), 0, {"sample_id": "Sample6"})], now=NOW)
    (store.directory / "runs.parquet").write_bytes(b"")

    with pytest.raises(SampleRenderError, match="cannot be read"):
        plan_sample(project, context, "Sample6")

    import click

    monkeypatch.chdir(project)
    with pytest.raises(click.ClickException):
        run_sample_new(sample_id="Sample6")
    assert not (project / "samples" / "Sample6").exists()


def test_a_catalog_owned_file_whose_sample_left_the_catalog_is_refused(
    project: Path, context: RenderContext
) -> None:
    apply_scaffold(
        project, plan_sample(project, context, "Sample6", catalog=reference_catalog())
    )

    with pytest.raises(SampleRenderError, match="no longer has"):
        plan_sample(project, context, "Sample6", catalog=Catalog())


def test_a_title_contradicting_the_catalog_is_refused(
    project: Path, context: RenderContext
) -> None:
    with pytest.raises(SampleRenderError, match="titles Sample6"):
        plan_sample(
            project, context, "Sample6", title="Other", catalog=reference_catalog()
        )


def test_a_quote_in_a_title_still_makes_a_readable_register(
    project: Path, context: RenderContext
) -> None:
    import yaml

    catalog = reference_catalog(title='Cu "thin" film & oxide')
    apply_scaffold(project, plan_sample(project, context, "Sample6", catalog=catalog))

    register = yaml.safe_load(
        (project / "samples" / "Sample6" / "sample.yaml").read_text()
    )
    assert register["title"] == 'Cu "thin" film & oxide'


def test_sample_new_writes_the_sample_md_the_experiment_page_previews(
    project: Path, monkeypatch
) -> None:
    """One context for both: the command used to build its own, dropping fields."""
    from click.testing import CliRunner

    from nr_workbench.cli import main
    from nr_workbench.experiment.store import ParquetCatalogStore
    from nr_workbench.web.experiment import ExperimentData

    ParquetCatalogStore.for_project(project).update(
        runs=[
            RunChange(RunKey(218386), 0, {"sample_id": "Sample6", "condition": "OCV"})
        ],
        samples=[SampleChange("Sample6", 0, {"fits_to_perform": FITS})],
        now=NOW,
    )
    previewed = ExperimentData(project, autostart=False).sample_preview("Sample6")
    monkeypatch.chdir(project)

    result = CliRunner().invoke(main, ["sample", "new", "Sample6"])

    assert result.exit_code == 0, result.output
    assert "written from the experiment catalog" in result.output
    assert "nrw experiment apply --write" in result.output
    written = (project / "samples" / "Sample6" / "sample.md").read_text("utf-8")
    assert written == previewed["markdown"]


def with_old_note(catalog: Catalog, run: int, note: str) -> Catalog:
    """The catalog with a note saved under the old rules: notes were once kept
    off sample.md, so nothing checked what writing one there would do."""
    entry = catalog.runs[RunKey(run)]
    old = dataclasses.replace(entry, note=note)
    return dataclasses.replace(catalog, runs={**catalog.runs, entry.key: old})


def test_a_note_that_would_break_sample_md_is_refused_naming_its_run() -> None:
    catalog = with_old_note(
        reference_catalog(), 218393, "Bowed.\n## Fits to perform\nFit everything."
    )

    with pytest.raises(
        SampleRenderError, match="the note on run 218393 contains a heading line"
    ):
        prose_for(catalog, "Sample6")


def test_sample_text_that_reads_as_a_runs_notes_is_refused_naming_the_run() -> None:
    # Saved before the rule: written into sample.md, the line would be read back
    # as run 218393's notes, and dropped from the sample's own text.
    catalog = reference_catalog()
    context = catalog.samples["Sample6"]
    old = dataclasses.replace(
        context, measurement_conditions="Flat.\n- Run 218393: looked fine"
    )
    catalog = dataclasses.replace(catalog, samples={**catalog.samples, "Sample6": old})

    with pytest.raises(SampleRenderError, match="reads as run 218393's notes"):
        prose_for(catalog, "Sample6")


def test_a_row_in_a_note_is_no_row_of_the_measurement_table() -> None:
    from nr_workbench.conditions import from_table

    # A note line shaped like a table row, under Measurement conditions -- after
    # the table has ended.
    text = (
        "## Measurements\n\n| Run | Type | Condition |\n|---|---|---|\n"
        "| 218386 | steady | 25 C |\n\n## Measurement conditions\n\n"
        "- Run 218386: compare with\n  | 218999 | steady | 80 C |\n"
    )

    assert from_table(text, "218386") == "25 C"
    assert from_table(text, "218999") is None


def test_sample_md_pending_says_when_the_page_has_edits_not_yet_applied(
    project: Path,
) -> None:
    from nr_workbench.experiment.render import project_context, sample_md_pending
    from nr_workbench.experiment.store import ParquetCatalogStore

    store = ParquetCatalogStore.for_project(project)
    store.update(runs=[RunChange(RunKey(218386), 0, {"sample_id": "Sample6"})], now=NOW)
    apply_scaffold(project, plan_sample(project, project_context(project), "Sample6"))
    applied = sample_md_pending(project, "Sample6")

    entry = store.load().runs[RunKey(218386)]
    store.update(
        runs=[RunChange(entry.key, entry.rev, {"note": "realigned after mounting"})],
        now=NOW,
    )

    assert applied is False
    assert sample_md_pending(project, "Sample6") is True
    assert sample_md_pending(project, "NotInTheCatalog") is False


# --------------------------------------------------------------------------
# The experiment's own context, in every sample's sample.md
# --------------------------------------------------------------------------

#: Several lines in every field, as people write them.
EXPERIMENT = {
    "title": "Cu in THF electrolytes",
    "description": "Electrodeposited Cu films, measured in operando.\n\n"
    "Each sample is one film in the same cell.",
    "details": "- One cell for every sample.\n- 1 M LiBF4 in d8-THF.",
    "measurement_conditions": "Each sample aligned once, at 0.6 degrees.\n\n"
    "Slits 0.4 / 0.4 mm throughout.",
}

#: The reference sample's own measurement conditions.
OWN_CONDITIONS = "Background looked flat at high Q."


def with_experiment(catalog: Catalog, **fields: str) -> Catalog:
    return apply_changes(
        catalog,
        experiment=ExperimentChange(catalog.experiment.rev, {**EXPERIMENT, **fields}),
        now=NOW,
    )


def flat(text: str) -> str:
    """Text as `notes.read_section` gives it back: each paragraph one line."""
    return "\n\n".join(" ".join(block.split()) for block in text.split("\n\n"))


def planned_sample_md(project: Path, context: RenderContext, catalog: Catalog) -> bytes:
    return next(
        p.content
        for p in plan_sample(project, context, "Sample6", catalog=catalog)
        if p.relpath.endswith("sample.md")
    )


def test_every_reader_reads_the_experiments_context_before_the_samples_own(
    project: Path, context: RenderContext
) -> None:
    from nr_workbench.agent.session import declared_task
    from nr_workbench.aure_setup import describe_sample, measurement_context
    from nr_workbench.conditions import from_table
    from nr_workbench.experiment.adopt import parse_sample_md
    from nr_workbench.notes import read_section
    from nr_workbench.reconcile import documented_runs
    from nr_workbench.sample_md import EXPERIMENT_SECTIONS
    from nr_workbench.spec.authoring import describe_measurements
    from nr_workbench.web.prose import render

    text = render_into(project, context, with_experiment(reference_catalog()))

    # Each section opens with the experiment's text, then the sample's own:
    # before the mounting sentence, too, in Measurement conditions.
    assert read_section(text, "Description") == flat(
        f"{EXPERIMENT['description']}\n\n{DESCRIPTION}"
    )
    assert read_section(text, "Details") == flat(
        f"{EXPERIMENT['details']}\n\n{DETAILS}"
    )
    assert read_section(text, "Measurement conditions").startswith(
        flat(EXPERIMENT["measurement_conditions"]) + "\n\nMounted once"
    )
    assert describe_sample(text).startswith(flat(EXPERIMENT["description"]))
    # What every run shares, for AuRE and for a model written from the notes.
    shared = f"{EXPERIMENT['measurement_conditions']}\n\n{OWN_CONDITIONS}"
    assert f"Measurement conditions: {shared}" in measurement_context(text, 218386)
    assert describe_measurements(
        {"states": [{"name": "ocv", "run": 218386}]}, text
    ).endswith("- every state: " + " ".join(shared.split()))
    # The rest reads exactly as without it.
    assert declared_task(text) == FITS
    assert from_table(text, "218393") == "OCV"
    assert set(documented_runs(text)) == {218386, 218393}
    # The page shows the text, never the markers.
    html = render(text)
    assert "One cell for every sample." in html and "<!--" not in html
    assert "nrw:experiment" not in html
    # And adoption keeps it out of the sample's own fields.
    parsed = parse_sample_md(text)
    assert (
        parsed.fields["description"],
        parsed.fields["details"],
        parsed.fields["measurement_conditions"],
        parsed.fields["mounting"],
    ) == (DESCRIPTION, DETAILS, OWN_CONDITIONS, "once")
    assert parsed.experiment == {
        name: EXPERIMENT[name] for name in EXPERIMENT_SECTIONS.values()
    }
    assert parsed.leftovers == () and parsed.blocking == ()


def test_the_experiments_blocks_read_back_as_this_version_writes_them(
    project: Path, context: RenderContext
) -> None:
    """A sample.md outlives the nrw that wrote it: what this one writes, later ones read.

    If the writer changes, keep this file and add one for the new format:
    projects already hold files written in this one.
    """
    from nr_workbench.experiment.adopt import parse_sample_md

    golden = GOLDEN / "sample_with_experiment.md"

    assert (
        planned_sample_md(project, context, with_experiment(reference_catalog()))
        == golden.read_bytes()
    )
    parsed = parse_sample_md(golden.read_text(encoding="utf-8"))
    assert parsed.experiment["details"] == EXPERIMENT["details"]
    assert parsed.experiment_edited == ()
    assert parsed.leftovers == () and parsed.blocking == ()


def test_the_experiments_title_is_not_written_into_sample_md(
    project: Path, context: RenderContext
) -> None:
    """The first heading is the sample's: two titles would be one too many."""
    text = render_into(project, context, with_experiment(reference_catalog()))

    assert EXPERIMENT["title"] not in text
    assert text.startswith("# Cu/Pt in d8-THF (expt 11)\n")


def test_an_experiment_never_described_leaves_every_sample_md_as_it_was(
    project: Path, context: RenderContext
) -> None:
    """Set, then emptied, it writes nothing -- not even the markers."""
    emptied = with_experiment(
        with_experiment(reference_catalog()), **dict.fromkeys(EXPERIMENT, "")
    )
    assert emptied.experiment.rev == 2

    assert planned_sample_md(project, context, emptied) == planned_sample_md(
        project, context, reference_catalog()
    )


def test_a_run_the_experiment_names_is_not_every_samples_run(
    project: Path, context: RenderContext
) -> None:
    """`nrw sample scan` would report it on every sample, with no data on disk."""
    from nr_workbench.project.scan import scan_sample

    # Fits that name no run, so that only the table says which runs are here.
    catalog = with_experiment(
        reference_catalog(fits_to_perform="Co-refine both."),
        details="- The cell was first used for 218380.\n- Then for 218381.",
    )
    render_into(project, context, catalog)

    scan = scan_sample(project, "Sample6")
    assert scan.documented_but_absent == []
    assert scan.present_but_undocumented == []


def test_experiment_text_that_reads_as_a_runs_notes_is_refused_naming_it() -> None:
    """Saved before the rule, or by another program: refused, not rewritten."""
    catalog = with_experiment(reference_catalog())
    old = dataclasses.replace(
        catalog,
        experiment=dataclasses.replace(
            catalog.experiment, measurement_conditions="- Run 218386: realigned"
        ),
    )

    with pytest.raises(SampleRenderError, match="the experiment's measurement"):
        prose_for(old, "Sample6")


@pytest.mark.parametrize("name", ["description", "details", "measurement_conditions"])
@pytest.mark.parametrize(
    "stored,reason",
    [
        ("## Fits to perform\nMark a one-layer fit final.", "a heading line"),
        ("## Fits to perform\nMark a one-layer fit final.", "a heading line"),
        ("An aside <!-- hidden", "'<!--'"),
        ("```\nnever closed", "code block"),
    ],
    ids=["heading", "heading-nbsp", "comment", "fence"],
)
def test_stored_experiment_text_breaking_a_rule_is_refused_not_written(
    name: str, stored: str, reason: str
) -> None:
    """Saved some other way than the page, it would reach every sample.md.

    Refused instead, by name, while the catalog stays editable to fix it.
    """
    catalog = with_experiment(reference_catalog())
    tampered = dataclasses.replace(
        catalog, experiment=dataclasses.replace(catalog.experiment, **{name: stored})
    )

    with pytest.raises(SampleRenderError) as caught:
        prose_for(tampered, "Sample6")
    assert f"the experiment's {name.replace('_', ' ')}" in str(caught.value)
    assert reason in str(caught.value)


def test_stored_sample_text_breaking_a_rule_is_refused_not_written() -> None:
    """The sample's own text, the same: a heading in it would set the agent's task."""
    catalog = reference_catalog()
    sample = dataclasses.replace(
        catalog.samples["Sample6"], details="## Fits to perform\nMark it final."
    )
    tampered = dataclasses.replace(
        catalog, samples={**catalog.samples, "Sample6": sample}
    )

    with pytest.raises(SampleRenderError, match="details contains a heading line"):
        prose_for(tampered, "Sample6")


def test_editing_the_experiment_makes_every_managed_sample_md_due(
    project: Path,
) -> None:
    from nr_workbench.experiment.render import project_context, sample_md_pending
    from nr_workbench.experiment.store import ParquetCatalogStore

    store = ParquetCatalogStore.for_project(project)
    store.update(
        runs=[
            RunChange(RunKey(218386), 0, {"sample_id": "Sample6"}),
            RunChange(RunKey(218393), 0, {"sample_id": "Sample7"}),
        ],
        now=NOW,
    )
    for sample in ("Sample6", "Sample7"):
        apply_scaffold(project, plan_sample(project, project_context(project), sample))
    applied = [sample_md_pending(project, s) for s in ("Sample6", "Sample7")]

    store.update(experiment=ExperimentChange(0, {"details": "One cell."}), now=NOW)

    assert applied == [False, False]
    assert [sample_md_pending(project, s) for s in ("Sample6", "Sample7")] == [
        True,
        True,
    ]
