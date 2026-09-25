"""Adopting a hand-written sample.md into the catalog, and pulling hand edits back.

A sample.md written in an editor holds a person's words. Adoption reads them
into the catalog, and a rewrite then replaces the file with the catalog's
rendering -- so a rewrite is allowed only when nothing would be lost, and the
parser has to be lossless for everything it claims to understand.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nr_workbench.experiment.adopt import (
    AdoptRefused,
    adopt,
    parse_sample_md,
    plan_adopt,
)
from nr_workbench.experiment.model import (
    Catalog,
    RunChange,
    RunKey,
    SampleChange,
    apply_changes,
)
from nr_workbench.experiment.render import CATALOG_OWNER, plan_sample, prose_for
from nr_workbench.experiment.store import ParquetCatalogStore
from nr_workbench.project.render import RenderContext
from nr_workbench.project.samples import plan_sample_files
from nr_workbench.project.scaffold import (
    Outcome,
    apply_scaffold,
    classify,
    forget,
    load_lock,
    replace_owned,
)

NOW = "2026-09-25T12:00:00Z"

HAND_WRITTEN = """\
# Cu/Pt in d8-THF (expt 11)

## Description

About 30 nm Cu on Pt, in 1 M LiBF4 in d8-THF.

## Details

- Cu 20 nm, Pt 5 nm
- Ti adhesion layer

## Measurements

| Run    | Type   | Condition   |
|--------|--------|-------------|
| 218386 | full Q | OCV         |
| 218393 | full Q | CA -0.5 V   |

## Measurement conditions

Background looked flat at high Q.

## Fits to perform

Co-refine 218386 and 218393.
"""


def rendered(catalog: Catalog, sample: str = "Sample6") -> str:
    context = RenderContext(project_name="p", prose=prose_for(catalog, sample))
    return next(
        p.content.decode()
        for p in plan_sample_files(
            context, sample, title=catalog.context_for(sample).title or None
        )
        if p.relpath.endswith("sample.md")
    )


def catalog_with(**context) -> Catalog:
    return apply_changes(
        Catalog(),
        runs=[
            RunChange(
                RunKey(218386),
                0,
                {"sample_id": "Sample6", "measurement": "full Q", "condition": "OCV"},
            ),
            RunChange(RunKey(218393), 0, {"sample_id": "Sample6", "condition": ""}),
        ],
        samples=[SampleChange("Sample6", 0, context)] if context else [],
        now=NOW,
    )


def write_sample(project: Path, text: str, sample: str = "Sample6") -> Path:
    path = project / "samples" / sample / "sample.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# The parser is lossless for what it understands
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "context",
    [
        pytest.param({}, id="empty"),
        pytest.param(
            {
                "title": "Cu/Pt in d8-THF",
                "description": "First paragraph.\n\nSecond paragraph, *emphasis*.",
                "details": "- bullet one\n- bullet two\n\n| Layer | nm |\n|---|---|\n| Cu | 20 |",
                "mounting": "once",
                "measurement_conditions": "Background flat.\n\n```\nQ R dR\n```",
                "fits_to_perform": "Co-refine both.\n\n1. first\n2. second",
            },
            id="formatted",
        ),
        pytest.param({"mounting": "remounted"}, id="remounted_only"),
        pytest.param(
            {"mounting": "unknown", "measurement_conditions": "Flat."}, id="unknown"
        ),
    ],
)
def test_parse_of_a_rendering_is_the_catalog_again(context) -> None:
    catalog = catalog_with(**context)

    parsed = parse_sample_md(rendered(catalog))

    assert parsed.leftovers == ()
    record = catalog.context_for("Sample6")
    for name in (
        "description",
        "details",
        "mounting",
        "measurement_conditions",
        "fits_to_perform",
    ):
        assert parsed.fields[name] == getattr(record, name), name
    assert [(r.run, r.type, r.condition) for r in parsed.rows] == [
        (218386, "full Q", "OCV"),
        (218393, "", ""),
    ]


def test_the_blank_scaffold_parses_to_nothing_and_leaves_nothing_over() -> None:
    """The template's own guidance comments are recognised, not reported."""
    blank = (
        Path(__file__).parent / "data" / "experiment" / "blank_sample.md"
    ).read_text()

    parsed = parse_sample_md(blank)

    assert parsed.leftovers == ()
    assert parsed.rows == ()
    assert parsed.fields["description"] == "" and parsed.fields["mounting"] == "unknown"


def test_a_hand_written_sample_md_parses_as_a_person_would_read_it() -> None:
    parsed = parse_sample_md(HAND_WRITTEN)

    assert parsed.title == "Cu/Pt in d8-THF (expt 11)"
    assert parsed.fields["details"] == "- Cu 20 nm, Pt 5 nm\n- Ti adhesion layer"
    assert [(r.run, r.condition) for r in parsed.rows] == [
        (218386, "OCV"),
        (218393, "CA -0.5 V"),
    ]
    assert parsed.leftovers == ()


@pytest.mark.parametrize(
    "extra,reason",
    [
        pytest.param(
            "\n## Notes\n\nKeep for later.\n", "'## Notes' section", id="own_section"
        ),
        pytest.param(
            "\n## Details\n\nagain\n", "second '## Details'", id="duplicate_section"
        ),
    ],
)
def test_text_with_no_place_in_the_catalog_is_a_leftover(
    extra: str, reason: str
) -> None:
    parsed = parse_sample_md(HAND_WRITTEN + extra)

    assert any(reason in leftover for leftover in parsed.leftovers)


def test_a_persons_own_comment_is_a_leftover_not_template_guidance() -> None:
    text = HAND_WRITTEN.replace(
        "Background looked flat", "<!-- check this -->\nBackground looked flat"
    )

    parsed = parse_sample_md(text)

    assert any("check this" in leftover for leftover in parsed.leftovers)
    assert (
        parsed.fields["measurement_conditions"] == "Background looked flat at high Q."
    )


def test_a_table_column_the_catalog_does_not_keep_is_a_leftover() -> None:
    text = HAND_WRITTEN.replace(
        "| Run    | Type   | Condition   |", "| Run    | Type   | Condition   | Notes |"
    ).replace(
        "| 218386 | full Q | OCV         |", "| 218386 | full Q | OCV | aborted? |"
    )

    parsed = parse_sample_md(text)

    assert any("notes=aborted?" in leftover for leftover in parsed.leftovers)


def test_a_heading_inside_a_section_is_a_leftover_with_the_reason() -> None:
    text = HAND_WRITTEN.replace(
        "- Ti adhesion layer", "- Ti adhesion layer\n### Substrate\nSi"
    )

    parsed = parse_sample_md(text)

    assert any("heading" in leftover for leftover in parsed.leftovers)
    assert "details" not in parsed.fields


# --------------------------------------------------------------------------
# Adopting
# --------------------------------------------------------------------------


def test_adopt_a_hand_written_sample_assigns_its_runs_and_context(
    project: Path, context: RenderContext
) -> None:
    write_sample(project, HAND_WRITTEN)
    store = ParquetCatalogStore.for_project(project)

    plan = plan_adopt(project, store.load(), "Sample6", context)
    adopt(project, store, plan, context, rewrite=False)

    catalog = store.load()
    assert catalog.runs[RunKey(218393)].condition == "CA -0.5 V"
    assert (
        catalog.context_for("Sample6").fits_to_perform == "Co-refine 218386 and 218393."
    )
    # Without --rewrite, the file is left exactly as the person wrote it.
    assert (project / "samples/Sample6/sample.md").read_text() == HAND_WRITTEN


def test_adopt_with_rewrite_backs_up_and_later_applies_are_unchanged(
    project: Path, context: RenderContext
) -> None:
    path = write_sample(project, HAND_WRITTEN)
    store = ParquetCatalogStore.for_project(project)

    plan = plan_adopt(project, store.load(), "Sample6", context)
    assert plan.sample_md is Outcome.UNTRACKED and plan.rewrite_ready
    report = adopt(project, store, plan, context, rewrite=True)

    backup = project / report.backup
    assert backup.read_text() == HAND_WRITTEN
    entry = load_lock(project / ".nrw" / "scaffold.lock.json")[
        "samples/Sample6/sample.md"
    ]
    assert entry["owner"] == CATALOG_OWNER
    # The catalog now renders exactly what is on disk.
    md = next(
        p
        for p in plan_sample(project, context, "Sample6", catalog=store.load())
        if p.relpath.endswith("sample.md")
    )
    assert classify(md, path, entry) is Outcome.UNCHANGED
    assert "| 218393 | full Q | CA -0.5 V |" in path.read_text()


def test_adopt_refuses_a_rewrite_that_would_lose_text(
    project: Path, context: RenderContext
) -> None:
    path = write_sample(project, HAND_WRITTEN + "\n## Notes\n\nKeep for later.\n")
    store = ParquetCatalogStore.for_project(project)
    plan = plan_adopt(project, store.load(), "Sample6", context)

    with pytest.raises(AdoptRefused, match="Notes"):
        adopt(project, store, plan, context, rewrite=True)

    assert "Keep for later." in path.read_text()
    assert not store.exists()


def test_adopt_a_run_the_catalog_gives_to_another_sample_is_refused(
    project: Path, context: RenderContext
) -> None:
    write_sample(project, HAND_WRITTEN)
    store = ParquetCatalogStore.for_project(project)
    store.update(runs=[RunChange(RunKey(218386), 0, {"sample_id": "Sample7"})], now=NOW)

    plan = plan_adopt(project, store.load(), "Sample6", context)

    assert any("Sample7" in p.message for p in plan.problems)
    with pytest.raises(AdoptRefused):
        adopt(project, store, plan, context, rewrite=False)


def test_runs_on_disk_but_not_in_the_table_are_reported_not_assigned(
    project: Path, context: RenderContext
) -> None:
    write_sample(project, HAND_WRITTEN)
    steady = project / "samples" / "Sample6" / "data" / "steady"
    steady.mkdir(parents=True)
    (steady / "REFL_218400_1_218400_partial.txt").write_text("0.01 1 0.1 0.001\n")

    plan = plan_adopt(project, Catalog(), "Sample6", context)

    assert plan.undocumented == (218400,)
    assert RunKey(218400) not in {c.key for c in plan.run_changes}


# --------------------------------------------------------------------------
# Pulling hand edits
# --------------------------------------------------------------------------


def test_pull_a_hand_edit_back_into_the_catalog(
    project: Path, context: RenderContext
) -> None:
    store = ParquetCatalogStore.for_project(project)
    store.update(
        runs=[
            RunChange(RunKey(218386), 0, {"sample_id": "Sample6", "condition": "OCV"}),
            RunChange(RunKey(218393), 0, {"sample_id": "Sample6", "condition": "OCV"}),
        ],
        samples=[SampleChange("Sample6", 0, {"description": "Before."})],
        now=NOW,
    )
    apply_scaffold(project, plan_sample(project, context, "Sample6"))
    path = project / "samples" / "Sample6" / "sample.md"
    edited = (
        path.read_text()
        .replace("| 218393 |  | OCV |", "| 218393 |  | CA |")
        .replace("\nBefore.\n", "\nAfter, by hand.\n")
    )
    path.write_text(edited)

    plan = plan_adopt(project, store.load(), "Sample6", context)
    assert plan.sample_md is Outcome.DRIFTED
    # Pulled, the catalog renders the edited file byte for byte.
    assert plan.in_step
    adopt(project, store, plan, context, rewrite=True)

    catalog = store.load()
    assert catalog.runs[RunKey(218393)].condition == "CA"
    assert catalog.context_for("Sample6").description == "After, by hand."
    report = apply_scaffold(project, plan_sample(project, context, "Sample6"))
    assert {f.outcome for f in report.files if f.relpath.endswith("sample.md")} == {
        Outcome.UNCHANGED
    }


def test_pull_a_row_removed_by_hand_excludes_the_run(
    project: Path, context: RenderContext
) -> None:
    store = ParquetCatalogStore.for_project(project)
    store.update(
        runs=[
            RunChange(RunKey(218386), 0, {"sample_id": "Sample6"}),
            RunChange(RunKey(218393), 0, {"sample_id": "Sample6"}),
        ],
        now=NOW,
    )
    apply_scaffold(project, plan_sample(project, context, "Sample6"))
    path = project / "samples" / "Sample6" / "sample.md"
    path.write_text(path.read_text().replace("| 218393 |  |  |\n", ""))

    plan = plan_adopt(project, store.load(), "Sample6", context)

    assert RunChange(RunKey(218393), 1, {"include": False}) in plan.run_changes


# --------------------------------------------------------------------------
# The scaffold helpers
# --------------------------------------------------------------------------


def test_replace_owned_keeps_a_backup_and_records_the_owner(project: Path) -> None:
    from nr_workbench.project.scaffold import PlannedFile

    target = write_sample(project, "# mine\n")
    planned = PlannedFile(
        "samples/Sample6/sample.md",
        b"# theirs\n",
        "sample/sample.md.j2",
        owner="experiment",
    )

    backup = replace_owned(project, planned)

    assert backup.read_text() == "# mine\n"
    assert target.read_text() == "# theirs\n"
    assert (
        load_lock(project / ".nrw/scaffold.lock.json")[planned.relpath]["owner"]
        == "experiment"
    )


def test_force_still_leaves_a_users_own_file_alone(
    project: Path, context: RenderContext
) -> None:
    """replace_owned is separate precisely so `--force` does not grow."""
    target = write_sample(project, "# brought by the user\n", sample="Sample9")

    apply_scaffold(project, plan_sample_files(context, "Sample9"), force=True)

    assert target.read_text() == "# brought by the user\n"


def test_forget_makes_a_file_its_owners(project: Path, context: RenderContext) -> None:
    """`nrw experiment release`: the lock forgets, the file stays, no plan writes it."""
    catalog = catalog_with(fits_to_perform="Fit.")
    apply_scaffold(project, plan_sample(project, context, "Sample6", catalog=catalog))
    path = project / "samples" / "Sample6" / "sample.md"
    content = path.read_text()

    assert forget(project, "samples/Sample6/sample.md")

    report = apply_scaffold(
        project, plan_sample(project, context, "Sample6", catalog=Catalog())
    )
    outcome = {f.relpath: f.outcome for f in report.files}["samples/Sample6/sample.md"]
    assert outcome is Outcome.UNTRACKED
    assert path.read_text() == content
