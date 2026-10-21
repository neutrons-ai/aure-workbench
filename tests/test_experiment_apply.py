"""Apply: the one step that writes the catalog into samples/<id>/.

It writes into a directory a scientist and an agent are both working in, so
each test here pins one way it could write something it should not, or fail to
say that it did not.
"""

from __future__ import annotations

import dataclasses
import json
import os
import shutil
import threading
import time
from pathlib import Path

import pytest

from nr_workbench.experiment import apply as apply_module
from nr_workbench.experiment.apply import (
    Action,
    ApplyBusy,
    ApplyRefused,
    NewData,
    PlanChanged,
    apply,
    new_data,
    plan_apply,
    read_sources,
)
from nr_workbench.experiment.feeds.directory import DirectoryFeed
from nr_workbench.experiment.live import LiveInventory
from nr_workbench.experiment.model import (
    Catalog,
    RunChange,
    RunKey,
    SampleChange,
    apply_changes,
)
from nr_workbench.experiment.sources.local import LocalDirectorySource
from nr_workbench.project.render import RenderContext
from nr_workbench.project.scaffold import Outcome

from .experiment_fixtures import InMemorySource, write_autoreduced

NOW = "2026-09-25T12:00:00Z"
RUNS = (234277, 234280)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def catalog_for(*runs: int, sample: str = "Sample6", **run_fields) -> Catalog:
    fields = {"sample_id": sample, "measurement": "full Q", "condition": "OCV"}
    fields.update(run_fields)
    return apply_changes(
        Catalog(),
        runs=[RunChange(RunKey(run), 0, dict(fields)) for run in runs],
        samples=[SampleChange(sample, 0, {"fits_to_perform": "Fit both."})],
        now=NOW,
    )


def edit(catalog: Catalog, run: int, **changes) -> Catalog:
    entry = catalog.runs[RunKey(run)]
    return apply_changes(
        catalog, runs=[RunChange(RunKey(run), entry.rev, changes)], now=NOW
    )


def observe(source) -> tuple[dict, dict]:
    """Poll once with every file long settled; return (runs, statuses)."""
    live = LiveInventory(
        source,
        DirectoryFeed(),
        settle_seconds=300,
        poll_seconds=30,
        clock=lambda: time.time() + 10_000,
        autostart=False,
    )
    snapshot = live.scan_once()
    runs = {k: v.source for k, v in snapshot.runs.items() if v.source is not None}
    return runs, {k: v.status for k, v in snapshot.runs.items()}


#: The run measured after RUNS. Its reduced file is the evidence that they
#: have finished; nothing in these tests assigns it.
NEXT_RUN = 234290


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    """A facility folder with two complete new_reduction runs, and the next one."""
    reduced = tmp_path / "facility" / "new_reduction"
    for run in RUNS:
        write_autoreduced(reduced, run, [1, 2, 3], planned=3, mtime=time.time() - 3600)
    write_autoreduced(reduced, NEXT_RUN, [1], planned=3, mtime=time.time() - 3600)
    return reduced


@pytest.fixture
def source(folder: Path) -> LocalDirectorySource:
    return LocalDirectorySource(folder, str(folder), ipts="IPTS-00001")


class Applier:
    """Plan and apply against one project, source and catalog."""

    def __init__(self, project: Path, context: RenderContext, source) -> None:
        self.project, self.context, self.source = project, context, source

    def plan(self, catalog: Catalog, **kwargs):
        runs, statuses = observe(self.source)
        return plan_apply(
            self.project, catalog, runs, statuses, self.source, self.context, **kwargs
        )

    def apply(self, catalog: Catalog, **kwargs):
        plan = self.plan(catalog, **kwargs)
        runs, statuses = observe(self.source)
        return plan, apply(
            self.project,
            catalog,
            runs,
            statuses,
            self.source,
            self.context,
            expected_plan_id=plan.plan_id,
            **kwargs,
        )


@pytest.fixture
def applier(project: Path, context: RenderContext, source) -> Applier:
    return Applier(project, context, source)


def folder_of(applier: Applier) -> Path:
    return Path(applier.source.path)


def steady(project: Path, sample: str = "Sample6") -> Path:
    return project / "samples" / sample / "data" / "steady"


def actions(plan, sample: str = "Sample6") -> dict[str, Action]:
    chosen = next(s for s in plan.samples if s.sample_id == sample)
    return {f.name: f.action for f in chosen.files}


# --------------------------------------------------------------------------
# The ordinary case
# --------------------------------------------------------------------------


def test_apply_copies_complete_runs_and_renders_sample_md(
    applier: Applier, project: Path, folder: Path
) -> None:
    catalog = catalog_for(*RUNS)

    plan, report = applier.apply(catalog)

    names = sorted(p.name for p in folder.iterdir() if str(NEXT_RUN) not in p.name)
    assert sorted(p.name for p in steady(project).glob("REFL_*")) == names
    for name in names:
        assert (steady(project) / name).read_bytes() == (folder / name).read_bytes()
    assert plan.samples[0].sample_md is Outcome.CREATE
    text = (project / "samples" / "Sample6" / "sample.md").read_text()
    assert "| 234277 | full Q | OCV |" in text and "Fit both." in text
    assert set(read_sources(project / "samples" / "Sample6")) == set(names)
    assert not report.failed


def test_copies_are_dated_now_not_when_the_facility_wrote_them(
    applier: Applier, project: Path
) -> None:
    """A copy dated an hour ago looks settled to `nrw agent watch` on arrival."""
    applier.apply(catalog_for(*RUNS))

    for path in steady(project).glob("REFL_*"):
        assert time.time() - path.stat().st_mtime < 60


def test_apply_twice_second_run_writes_nothing(applier: Applier, project: Path) -> None:
    catalog = catalog_for(*RUNS)
    applier.apply(catalog)

    def stamp(path: Path) -> tuple[int, int]:
        # The inode as well: os.replace makes a new one even within the second
        # a coarse filesystem clock cannot tell apart.
        info = path.stat()
        return (info.st_mtime_ns, info.st_ino)

    stamps = {
        p: stamp(p)
        for p in [*project.rglob("*")]
        if p.is_file() and ".nrw/cache" not in p.as_posix()
    }

    plan, report = applier.apply(catalog)

    assert not plan.writes
    assert set(actions(plan).values()) == {Action.UNCHANGED}
    assert report.done == []
    assert {p: stamp(p) for p in stamps} == stamps


def test_downstream_scan_reads_the_applied_sample(
    applier: Applier, project: Path
) -> None:
    from nr_workbench.project.scan import scan_sample

    applier.apply(catalog_for(*RUNS))

    scan = scan_sample(project, "Sample6")
    assert sorted(scan.steady) == list(RUNS)
    assert scan.documented_but_absent == [] and scan.present_but_undocumented == []


# --------------------------------------------------------------------------
# Only complete runs
# --------------------------------------------------------------------------


def test_apply_defers_an_unconfirmed_run_until_a_person_confirms_it(
    applier: Applier, project: Path, folder: Path
) -> None:
    write_autoreduced(folder, 234290, [1], planned=3, mtime=time.time() - 3600)
    catalog = catalog_for(234290)

    plan = applier.plan(catalog)
    assert set(actions(plan).values()) == {Action.DEFERRED}
    assert "1 of 3" in plan.samples[0].files[0].detail

    _, report = applier.apply(catalog, confirmed={RunKey(234290)})
    assert [a.name for a in report.done] == ["REFL_234290_1_234290_autoreduction.dat"]


def test_confirmation_does_not_override_a_quarantine(
    applier: Applier, folder: Path
) -> None:
    write_autoreduced(folder, 234290, [1, 3], planned=3, mtime=time.time() - 3600)

    plan = applier.plan(catalog_for(234290), confirmed={RunKey(234290)})

    assert set(actions(plan).values()) == {Action.DEFERRED}
    assert "quarantined" in plan.samples[0].files[0].detail


def test_apply_rechecks_what_it_would_do_before_writing(
    applier: Applier, project: Path, folder: Path, context: RenderContext, source
) -> None:
    catalog = catalog_for(*RUNS)
    plan = applier.plan(catalog)
    # A segment is rewritten by the reduction between review and apply.
    (folder / "REFL_234277_2_234278_autoreduction.dat").write_text(
        "# rewritten\n1 2 3\n"
    )
    os.utime(folder / "REFL_234277_2_234278_autoreduction.dat", (1, 1))

    runs, statuses = observe(source)
    with pytest.raises(PlanChanged):
        apply(
            project,
            catalog,
            runs,
            statuses,
            source,
            context,
            expected_plan_id=plan.plan_id,
        )
    assert not steady(project).exists()


# --------------------------------------------------------------------------
# Never overwrite
# --------------------------------------------------------------------------


def test_apply_source_rereduced_after_copy_is_reported_not_replaced(
    applier: Applier, project: Path, folder: Path
) -> None:
    catalog = catalog_for(*RUNS)
    applier.apply(catalog)
    name = "REFL_234277_1_234277_autoreduction.dat"
    copied = (steady(project) / name).read_bytes()
    (folder / name).write_text((folder / name).read_text() + "0.3 1e-9 1e-10 0.001\n")

    plan, _ = applier.apply(catalog)

    assert actions(plan)[name] is Action.SOURCE_CHANGED
    assert (steady(project) / name).read_bytes() == copied


def test_a_touched_but_identical_source_file_is_not_a_change(
    applier: Applier, project: Path, folder: Path
) -> None:
    catalog = catalog_for(*RUNS)
    applier.apply(catalog)
    name = "REFL_234277_1_234277_autoreduction.dat"
    os.utime(folder / name, (time.time() - 100, time.time() - 100))

    plan = applier.plan(catalog)

    assert actions(plan)[name] is Action.RECORD


def test_apply_a_local_edit_to_a_copy_is_left_alone(
    applier: Applier, project: Path
) -> None:
    catalog = catalog_for(*RUNS)
    applier.apply(catalog)
    name = "REFL_234280_3_234282_autoreduction.dat"
    (steady(project) / name).write_text("# edited by hand\n")

    plan, _ = applier.apply(catalog)

    assert actions(plan)[name] is Action.LOCAL_EDITED
    assert (steady(project) / name).read_text() == "# edited by hand\n"


def test_a_different_hand_copied_file_is_a_conflict_left_alone(
    applier: Applier, project: Path
) -> None:
    steady(project).mkdir(parents=True)
    name = "REFL_234277_1_234277_autoreduction.dat"
    (steady(project) / name).write_text("# someone else's file\n")

    plan, _ = applier.apply(catalog_for(234277))

    assert actions(plan)[name] is Action.CONFLICT
    assert (steady(project) / name).read_text() == "# someone else's file\n"


def test_an_identical_hand_copied_file_is_recorded_not_copied(
    applier: Applier, project: Path, folder: Path
) -> None:
    steady(project).mkdir(parents=True)
    name = "REFL_234277_1_234277_autoreduction.dat"
    (steady(project) / name).write_bytes((folder / name).read_bytes())

    plan, _ = applier.apply(catalog_for(234277))

    assert actions(plan)[name] is Action.RECORD
    assert name in read_sources(project / "samples" / "Sample6")


def test_apply_destination_symlink_target_not_modified(
    applier: Applier, project: Path, tmp_path: Path
) -> None:
    """`nrw import` symlinks data; writing through one writes the facility's file."""
    name = "REFL_234277_1_234277_autoreduction.dat"
    # The same bytes as the source, so a digest comparison would call it a
    # match and record it: only the link check stands between them.
    outside = tmp_path / "facility-file.dat"
    outside.write_bytes((folder_of(applier) / name).read_bytes())
    steady(project).mkdir(parents=True)
    (steady(project) / name).symlink_to(outside)

    plan, _ = applier.apply(catalog_for(234277))

    chosen = next(f for f in plan.samples[0].files if f.name == name)
    assert chosen.action is Action.CONFLICT and "symbolic link" in chosen.detail
    assert name not in read_sources(project / "samples" / "Sample6")


# --------------------------------------------------------------------------
# A run is copied whole or not at all
# --------------------------------------------------------------------------


def test_apply_copy_fails_midway_no_partial_run_visible(
    project: Path, context: RenderContext
) -> None:
    source = InMemorySource(fail_reads_after=1)
    source.add_segments(234277, [1, 2, 3], planned=3, mtime=time.time() - 3600)
    source.add_segments(NEXT_RUN, [1], planned=3, mtime=time.time() - 3600)
    applier = Applier(project, context, source)

    _, report = applier.apply(catalog_for(234277))

    assert not any(steady(project).glob("REFL_*"))
    assert len(report.failed) == 3
    assert "none of its files" in report.failed[0].detail
    assert not any((project / "samples" / "Sample6" / "data").glob(".nrw-staging-*"))


def test_apply_a_source_file_that_is_not_reduced_data_is_refused(
    project: Path, context: RenderContext
) -> None:
    source = InMemorySource()
    source.add_segments(234277, [1, 2, 3], planned=3, mtime=time.time() - 3600)
    source.add_segments(NEXT_RUN, [1], planned=3, mtime=time.time() - 3600)
    source.files[
        "REFL_234277_2_234278_autoreduction.dat"
    ].data = b"<html>not data</html>"
    applier = Applier(project, context, source)

    _, report = applier.apply(catalog_for(234277))

    assert not any(steady(project).glob("REFL_*"))
    assert "not reduced data" in report.failed[0].detail


def test_apply_a_file_over_the_size_cap_is_refused(
    applier: Applier, project: Path, monkeypatch
) -> None:
    monkeypatch.setattr(apply_module, "MAX_FILE_BYTES", 100)

    _, report = applier.apply(catalog_for(234277))

    assert not any(steady(project).glob("REFL_*"))
    assert report.failed and "bytes" in report.failed[0].detail


# --------------------------------------------------------------------------
# Exclusion is real
# --------------------------------------------------------------------------


def test_an_excluded_run_moves_out_of_every_readers_way_and_back(
    applier: Applier, project: Path
) -> None:
    from nr_workbench.project.scan import scan_sample

    catalog = catalog_for(*RUNS)
    applier.apply(catalog)

    excluded = edit(catalog, 234280, include=False)
    plan, _ = applier.apply(excluded)
    assert {a for n, a in actions(plan).items() if "234280" in n} == {Action.MOVE_OUT}
    assert sorted(scan_sample(project, "Sample6").steady) == [234277]
    assert (project / "samples/Sample6/data/excluded/234280").is_dir()

    restored = edit(excluded, 234280, include=True)
    plan, _ = applier.apply(restored)
    assert {a for n, a in actions(plan).items() if "234280" in n} == {Action.RESTORE}
    assert sorted(scan_sample(project, "Sample6").steady) == list(RUNS)


def test_applying_again_after_an_exclusion_is_a_no_op(
    applier: Applier, project: Path
) -> None:
    """A copy already moved aside is not moved again.

    Planning the move a second time finds its target taken and reports a
    failure -- on every apply from then on, so ``--write`` never exits 0.
    """
    catalog = catalog_for(*RUNS)
    applier.apply(catalog)
    excluded = edit(catalog, 234280, include=False)
    applier.apply(excluded)

    plan, report = applier.apply(excluded)

    assert not plan.writes
    assert report.failed == []
    assert not any(f.run == 234280 for f in plan.samples[0].files)


def test_a_key_a_newer_nrw_wrote_in_the_copy_record_survives_a_rewrite(
    applier: Applier, project: Path
) -> None:
    """The record is committed and shared; an older nrw must not strip it."""
    catalog = catalog_for(*RUNS)
    applier.apply(catalog)
    path = project / "samples" / "Sample6" / "data" / "sources.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    name = next(n for n in document["files"] if "234280" in n)
    document["files"][name]["verified_by"] = "a newer nrw"
    path.write_text(json.dumps(document), encoding="utf-8")

    applier.apply(edit(catalog, 234280, include=False))

    entry = json.loads(path.read_text(encoding="utf-8"))["files"][name]
    assert entry["location"] == "excluded"
    assert entry["verified_by"] == "a newer nrw"


def test_a_reassigned_run_leaves_the_old_sample_and_reaches_the_new(
    applier: Applier, project: Path
) -> None:
    catalog = catalog_for(*RUNS)
    applier.apply(catalog)

    moved = edit(catalog, 234280, sample_id="Sample7")
    plan, _ = applier.apply(moved)

    old = actions(plan, "Sample6")
    assert {a for n, a in old.items() if "234280" in n} == {Action.MOVE_OUT}
    assert any("assigned to Sample7" in f.detail for f in plan.samples[0].files)
    assert len(list(steady(project, "Sample7").glob("REFL_234280_*"))) == 3
    assert not list(steady(project, "Sample6").glob("REFL_234280_*"))


def test_a_hand_copied_file_of_an_unassigned_run_is_reported_not_moved(
    applier: Applier, project: Path
) -> None:
    applier.apply(catalog_for(234277))
    stray = steady(project) / "REFL_218386_1_218386_partial.txt"
    stray.write_text("# copied in by hand\n")

    plan, _ = applier.apply(catalog_for(234277))

    assert actions(plan)[stray.name] is Action.NOT_MANAGED
    assert stray.is_file()


# --------------------------------------------------------------------------
# sample.md
# --------------------------------------------------------------------------


def test_a_hand_edited_sample_md_gets_a_nrw_new_beside_it(
    applier: Applier, project: Path
) -> None:
    catalog = catalog_for(*RUNS)
    applier.apply(catalog)
    path = project / "samples" / "Sample6" / "sample.md"
    edited = path.read_text() + "\nMy own line.\n"
    path.write_text(edited)

    changed = edit(catalog, 234277, condition="CA")
    plan, _ = applier.apply(changed)

    assert plan.samples[0].sample_md is Outcome.DRIFTED
    assert "nrw-new" in plan.samples[0].sample_md_detail
    assert path.read_text() == edited
    assert "| 234277 | full Q | CA |" in (path.parent / "sample.md.nrw-new").read_text()


def test_an_untracked_sample_md_is_named_not_silently_skipped(
    applier: Applier, project: Path
) -> None:
    """An imported sample.md is left alone -- and the plan must say so."""
    sample = project / "samples" / "Sample6"
    sample.mkdir(parents=True)
    (sample / "sample.md").write_text("# Sample6\n\nWritten by hand.\n")

    plan = applier.plan(catalog_for(*RUNS))

    assert plan.samples[0].sample_md is Outcome.UNTRACKED
    assert "Adopt" in plan.samples[0].sample_md_detail


# --------------------------------------------------------------------------
# Refusals
# --------------------------------------------------------------------------


def test_apply_a_changed_plan_id_refuses(
    applier: Applier, project: Path, context, source
) -> None:
    runs, statuses = observe(source)

    with pytest.raises(PlanChanged):
        apply(
            project,
            catalog_for(*RUNS),
            runs,
            statuses,
            source,
            context,
            expected_plan_id="not-the-plan",
        )


def test_apply_a_second_concurrent_apply_is_busy(
    applier: Applier, project, context, source
) -> None:
    runs, statuses = observe(source)
    plan = applier.plan(catalog_for(*RUNS))

    assert apply_module._APPLY_LOCK.acquire(blocking=False)
    try:
        with pytest.raises(ApplyBusy):
            apply(
                project,
                catalog_for(*RUNS),
                runs,
                statuses,
                source,
                context,
                expected_plan_id=plan.plan_id,
            )
    finally:
        apply_module._APPLY_LOCK.release()


def test_apply_conflict_markers_in_the_scaffold_lock_refuse_and_leave_it(
    applier: Applier, project: Path
) -> None:
    """A partial write over a lock that failed to load drops every other entry."""
    lock = project / ".nrw" / "scaffold.lock.json"
    damaged = "<<<<<<< HEAD\n" + lock.read_text() + "=======\n>>>>>>> other\n"
    lock.write_text(damaged)

    plan = applier.plan(catalog_for(*RUNS))

    assert any("conflict markers" in p.message for p in plan.problems)
    with pytest.raises(ApplyRefused):
        applier.apply(catalog_for(*RUNS))
    assert lock.read_text() == damaged


def test_a_sample_directory_differing_only_in_case_is_refused(
    applier: Applier, project: Path
) -> None:
    (project / "samples" / "sample6").mkdir(parents=True)

    plan = applier.plan(catalog_for(*RUNS))

    assert "only in case" in plan.samples[0].problems[0].message


def test_an_unreadable_copy_record_stops_that_sample_only(
    applier: Applier, project: Path
) -> None:
    catalog = apply_changes(
        catalog_for(234277),
        runs=[RunChange(RunKey(234280), 0, {"sample_id": "Sample7"})],
        now=NOW,
    )
    record = project / "samples" / "Sample6" / "data" / "sources.json"
    record.parent.mkdir(parents=True)
    record.write_text("{not json")

    plan = applier.plan(catalog)

    by_sample = {s.sample_id: s for s in plan.samples}
    assert by_sample["Sample6"].problems
    assert not by_sample["Sample7"].problems


def test_a_plan_reviewed_in_one_second_applies_in_the_next(
    project: Path, source, monkeypatch
) -> None:
    """A new sample's sample.yaml is stamped with the current second.

    Found by driving the page in a browser: review, then click Apply a second
    later, and the plan "changed" -- because its digest hashed that timestamp.
    """
    import datetime as real

    from nr_workbench.project import render as render_module

    ticks = iter(range(100))

    class Clock(real.datetime):
        @classmethod
        def now(cls, tz=None):
            return real.datetime(2026, 9, 25, 12, 0, next(ticks), tzinfo=tz)

    monkeypatch.setattr(render_module, "datetime", Clock)
    # The context the page and the CLI use: no fixed `created`, so every
    # render of a new sample's sample.yaml asks the clock.
    from nr_workbench.experiment.render import project_context

    context = project_context(project)
    # The precondition: if the timestamp stops coming through this module's
    # `datetime`, the patch stops biting and this test stops testing anything.
    from nr_workbench.experiment.render import plan_sample

    def register() -> bytes:
        return next(
            f.content
            for f in plan_sample(project, context, "Sample6", catalog=catalog)
            if f.relpath.endswith("sample.yaml")
        )

    catalog = catalog_for(*RUNS)
    assert register() != register()

    applier = Applier(project, context, source)
    plan = applier.plan(catalog)
    runs, statuses = observe(source)

    report = apply(
        project, catalog, runs, statuses, source, context, expected_plan_id=plan.plan_id
    )

    assert len(report.done) == 6


# --------------------------------------------------------------------------
# Never overwrite, on every path
# --------------------------------------------------------------------------


def test_restoring_a_run_never_overwrites_a_file_put_back_by_hand(
    applier: Applier, project: Path
) -> None:
    catalog = catalog_for(*RUNS)
    applier.apply(catalog)
    excluded = edit(catalog, 234280, include=False)
    applier.apply(excluded)
    name = "REFL_234280_1_234280_autoreduction.dat"
    (steady(project) / name).write_text("# put back by hand\n")

    _, report = applier.apply(edit(excluded, 234280, include=True))

    assert (steady(project) / name).read_text() == "# put back by hand\n"
    assert any(f.name == name and "already exists" in f.detail for f in report.failed)


def test_a_file_appearing_between_review_and_apply_is_not_overwritten(
    applier: Applier, project: Path, context: RenderContext, source
) -> None:
    """The window a plan cannot see: a hand copy made after review."""
    catalog = catalog_for(234277)
    plan = applier.plan(catalog)
    runs, statuses = observe(source)
    name = "REFL_234277_2_234278_autoreduction.dat"
    steady(project).mkdir(parents=True, exist_ok=True)

    original = apply_module._place

    def racing_place(part: Path, target: Path) -> None:
        if target.name == name:
            target.write_text("# arrived meanwhile\n")
        original(part, target)

    import pytest as _pytest

    with _pytest.MonkeyPatch.context() as patch:
        patch.setattr(apply_module, "_place", racing_place)
        report = apply(
            project,
            catalog,
            runs,
            statuses,
            source,
            context,
            expected_plan_id=plan.plan_id,
        )

    assert (steady(project) / name).read_text() == "# arrived meanwhile\n"
    # Whole or not at all: none of the run's files stayed.
    assert not list(steady(project).glob("REFL_234277_1_*"))
    assert all(f.run == 234277 for f in report.failed) and len(report.failed) == 3


def test_a_rename_failing_midway_leaves_no_partial_run(
    applier: Applier, project: Path, monkeypatch
) -> None:
    calls = {"n": 0}
    original = apply_module._place

    def failing(part: Path, target: Path) -> None:
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("disk full")
        original(part, target)

    monkeypatch.setattr(apply_module, "_place", failing)

    _, report = applier.apply(catalog_for(234277))

    assert not list(steady(project).glob("REFL_234277_*"))
    assert len(report.failed) == 3 and "disk full" in report.failed[0].detail
    assert not read_sources(project / "samples" / "Sample6")


# --------------------------------------------------------------------------
# The beamline's normal failures
# --------------------------------------------------------------------------


def test_with_the_source_gone_copies_stay_and_missing_runs_are_named(
    applier: Applier, project: Path, folder: Path
) -> None:
    catalog = catalog_for(234277)
    applier.apply(catalog)
    # 234280 is assigned after the mount went away: it was never copied.
    catalog = apply_changes(
        catalog, runs=[RunChange(RunKey(234280), 0, {"sample_id": "Sample6"})], now=NOW
    )
    folder.rename(folder.with_name("unmounted"))

    plan = applier.plan(catalog)

    kinds = {f.name: f.action for f in plan.samples[0].files}
    assert {a for n, a in kinds.items() if "234277" in n} == {Action.UNCHANGED}
    assert kinds["run 234280"] is Action.SOURCE_MISSING
    assert Action.MOVE_OUT not in kinds.values()


def test_a_copy_deleted_by_hand_is_a_conflict_not_a_silent_recopy(
    applier: Applier, project: Path
) -> None:
    catalog = catalog_for(234277)
    applier.apply(catalog)
    name = "REFL_234277_2_234278_autoreduction.dat"
    (steady(project) / name).unlink()

    plan, _ = applier.apply(catalog)

    assert actions(plan)[name] is Action.CONFLICT
    assert not (steady(project) / name).exists()


def test_an_unassigned_copy_moves_out_as_no_longer_assigned(
    applier: Applier, project: Path
) -> None:
    catalog = catalog_for(*RUNS)
    applier.apply(catalog)

    plan, _ = applier.apply(edit(catalog, 234280, sample_id=None))

    for_run = [f for f in plan.samples[0].files if f.run == 234280]
    # Each file once, as a move: not also "not copied by nrw; left alone",
    # which would contradict the move on the review page.
    assert [f.action for f in for_run] == [Action.MOVE_OUT] * 3
    assert "no longer assigned" in for_run[0].detail
    assert not list(steady(project).glob("REFL_234280_*"))


def test_one_broken_sample_does_not_stop_the_others(
    applier: Applier, project: Path
) -> None:
    catalog = apply_changes(
        catalog_for(234277),
        runs=[RunChange(RunKey(234280), 0, {"sample_id": "Sample7"})],
        now=NOW,
    )
    record = project / "samples" / "Sample6" / "data" / "sources.json"
    record.parent.mkdir(parents=True)
    record.write_text("{not json")

    applier.apply(catalog)

    assert len(list(steady(project, "Sample7").glob("REFL_234280_*"))) == 3
    assert record.read_text() == "{not json"
    assert not list(steady(project).glob("REFL_*"))


# --------------------------------------------------------------------------
# A measurement and its artifacts: apply copies what a fit reads
# --------------------------------------------------------------------------


def settled_file(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    old = time.time() - 3600
    os.utime(path, (old, old))
    return path


def test_apply_copies_the_segments_and_leaves_the_combined_curve_at_the_source(
    applier: Applier, project: Path
) -> None:
    """A fit co-refines the segments; the stitched curve is not the sample's data."""
    from nr_workbench.instrument.reduced import combined_filename

    from .experiment_fixtures import reduced_rows

    combined = settled_file(
        folder_of(applier) / combined_filename(234277), reduced_rows()
    )

    plan, report = applier.apply(catalog_for(234277))

    copied = sorted(p.name for p in steady(project).glob("REFL_*"))
    assert copied == [
        "REFL_234277_1_234277_autoreduction.dat",
        "REFL_234277_2_234278_autoreduction.dat",
        "REFL_234277_3_234279_autoreduction.dat",
    ]
    assert combined.name not in actions(plan)
    assert report.failed == []


def test_a_measurement_with_only_a_combined_curve_copies_that(
    applier: Applier, project: Path
) -> None:
    """Nothing else to fit, so the combined curve is what `nrw model new` fits."""
    from nr_workbench.instrument.reduced import combined_filename

    from .experiment_fixtures import reduced_rows

    only = settled_file(folder_of(applier) / combined_filename(234286), reduced_rows())

    applier.apply(catalog_for(234286))

    assert [p.name for p in steady(project).glob("REFL_*")] == [only.name]


def test_a_combined_curve_copied_before_stays_managed(
    applier: Applier, project: Path
) -> None:
    """One an earlier nrw copied is still checked, not dropped from the record."""
    from nr_workbench.instrument.reduced import combined_filename

    from .experiment_fixtures import reduced_rows

    applier.apply(catalog_for(234277))
    name = combined_filename(234277)
    settled_file(folder_of(applier) / name, reduced_rows())
    (steady(project) / name).write_text(reduced_rows(), encoding="utf-8")
    record = project / "samples" / "Sample6" / "data" / "sources.json"
    document = json.loads(record.read_text(encoding="utf-8"))
    document["files"][name] = {
        "run": 234277,
        "sha256": "0" * 64,  # not what is there now: an edited copy
    }
    record.write_text(json.dumps(document), encoding="utf-8")

    plan = applier.plan(catalog_for(234277))

    assert actions(plan)[name] is Action.LOCAL_EDITED


def test_a_note_that_would_break_sample_md_stops_its_sample_and_says_why(
    applier: Applier, project: Path
) -> None:
    catalog = catalog_for(*RUNS)
    entry = catalog.runs[RunKey(RUNS[0])]
    old = dataclasses.replace(entry, note="## Fits to perform\nFit it all.")
    catalog = dataclasses.replace(catalog, runs={**catalog.runs, entry.key: old})

    plan, _ = applier.apply(catalog)

    (sample,) = plan.samples
    assert f"the note on run {RUNS[0]} contains a heading line" in (
        sample.problems[0].message
    )
    assert not (project / "samples" / "Sample6").exists()


# --------------------------------------------------------------------------
# The sign of new data: what each poll says applying would bring in
# --------------------------------------------------------------------------

#: Permissions mean nothing to root, and Windows has none of these.
needs_posix_permissions = pytest.mark.skipif(
    os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="needs POSIX permissions, as a user other than root",
)


def news_of(applier: Applier, catalog: Catalog) -> NewData:
    runs, statuses = observe(applier.source)
    return new_data(applier.project, catalog, runs, statuses)


def touch(path: Path) -> None:
    """The same bytes with a new modification time: a new version to a listing."""
    when = time.time() - 100
    os.utime(path, (when, when))


def test_new_data_a_new_run_is_ready_to_copy_until_it_is_applied(
    applier: Applier,
) -> None:
    catalog = catalog_for(*RUNS)

    assert news_of(applier, catalog).ready == {"Sample6": RUNS}

    applier.apply(catalog)

    after = news_of(applier, catalog)
    assert (after.ready, after.reduced_again) == ({}, {})


def test_new_data_a_file_rewritten_at_the_source_is_reduced_again(
    applier: Applier, folder: Path
) -> None:
    catalog = catalog_for(*RUNS)
    applier.apply(catalog)
    name = "REFL_234277_1_234277_autoreduction.dat"
    (folder / name).write_text((folder / name).read_text() + "0.3 1e-9 1e-10 0.001\n")

    news = news_of(applier, catalog)

    assert news.reduced_again == {"Sample6": (234277,)}
    assert news.ready == {}
    # The Review reads both files, and says what the sign could not.
    assert actions(applier.plan(catalog))[name] is Action.SOURCE_CHANGED


def test_new_data_a_touched_file_is_reduced_again_until_apply_records_it(
    applier: Applier, folder: Path
) -> None:
    """Only the bytes tell a touch from a re-reduction, and the sign reads none."""
    catalog = catalog_for(*RUNS)
    applier.apply(catalog)
    touch(folder / "REFL_234280_2_234281_autoreduction.dat")

    assert news_of(applier, catalog).reduced_again == {"Sample6": (234280,)}

    applier.apply(catalog)

    assert news_of(applier, catalog).reduced_again == {}


def test_new_data_a_copy_edited_by_hand_and_reduced_again_is_said_by_both(
    applier: Applier, project: Path, folder: Path
) -> None:
    """The sign reads no copy, so it says reduced again; the Review says both."""
    catalog = catalog_for(*RUNS)
    applier.apply(catalog)
    name = "REFL_234277_1_234277_autoreduction.dat"
    (steady(project) / name).write_text("# edited by hand\n")
    touch(folder / name)

    assert news_of(applier, catalog).reduced_again == {"Sample6": (234277,)}
    (chosen,) = (f for f in applier.plan(catalog).samples[0].files if f.name == name)
    assert chosen.action is Action.LOCAL_EDITED
    assert "rewritten" in chosen.detail


def test_new_data_an_excluded_run_is_not_ready(applier: Applier) -> None:
    catalog = edit(catalog_for(*RUNS), 234280, include=False)

    assert news_of(applier, catalog).ready == {"Sample6": (234277,)}


def test_new_data_an_unconfirmed_run_is_not_ready(applier: Applier) -> None:
    """A person confirms one, and only in the Review."""
    catalog = catalog_for(NEXT_RUN)

    assert news_of(applier, catalog).ready == {}
    assert set(actions(applier.plan(catalog)).values()) == {Action.DEFERRED}


def test_new_data_a_run_partly_copied_is_ready_when_a_file_arrives(
    applier: Applier, folder: Path
) -> None:
    """Confirmed short and copied; then its last segment is reduced after all."""
    write_autoreduced(folder, 234285, [1, 2], planned=3, mtime=time.time() - 3600)
    catalog = catalog_for(234285)
    applier.apply(catalog, confirmed={RunKey(234285)})
    write_autoreduced(folder, 234285, [3], planned=3, mtime=time.time() - 3600)

    assert news_of(applier, catalog).ready == {"Sample6": (234285,)}


def test_new_data_a_run_included_again_is_not_new_data(applier: Applier) -> None:
    """Its copy moves back from data/excluded/ because a person said so;
    nothing arrived, so nothing is signed."""
    catalog = catalog_for(*RUNS)
    applier.apply(catalog)
    excluded = edit(catalog, 234280, include=False)
    applier.apply(excluded)
    included = edit(excluded, 234280, include=True)

    assert Action.RESTORE in set(actions(applier.plan(included)).values())
    news = news_of(applier, included)
    assert (news.ready, news.reduced_again) == ({}, {})


def test_new_data_counts_complete_runs_in_no_sample_apart(applier: Applier) -> None:
    news = news_of(applier, catalog_for(234277))

    # 234280 is complete and in no sample; the next run is not complete yet.
    assert news.unassigned == (234280,)
    assert news.ready == {"Sample6": (234277,)}


def test_new_data_a_run_taken_out_of_its_sample_waits_again(
    applier: Applier,
) -> None:
    """What Unassign leaves: the run's record, with no sample in it."""
    catalog = edit(catalog_for(*RUNS), 234280, sample_id=None)

    assert news_of(applier, catalog).unassigned == (234280,)


def test_new_data_a_run_excluded_from_every_sample_is_not_waiting(
    applier: Applier,
) -> None:
    """A direct beam belongs to no sample: excluding it says so, and it is not counted."""
    catalog = apply_changes(
        catalog_for(234277),
        runs=[RunChange(RunKey(234280), 0, {"include": False})],
        now=NOW,
    )

    assert news_of(applier, catalog).unassigned == ()


def _damage_nothing(project: Path, catalog: Catalog) -> Catalog:
    return catalog


def _damage_a_note(project: Path, catalog: Catalog) -> Catalog:
    """A note saved before notes were held to sample.md's rules: Sample7 stops."""
    entry = catalog.runs[RunKey(234284)]
    bad = dataclasses.replace(entry, note="## Fits to perform\nFit it all.")
    return dataclasses.replace(catalog, runs={**catalog.runs, entry.key: bad})


def _damage_the_experiment(project: Path, catalog: Catalog) -> Catalog:
    """The experiment's context, which every sample.md carries: every sample stops."""
    experiment = dataclasses.replace(
        catalog.experiment, details="## Fits to perform\nFit it all."
    )
    return dataclasses.replace(catalog, experiment=experiment)


def _damage_the_lock(project: Path, catalog: Catalog) -> Catalog:
    """A merge left in the scaffold lock: apply stops for every sample."""
    lock = project / ".nrw" / "scaffold.lock.json"
    lock.write_text("<<<<<<< HEAD\n" + lock.read_text() + "=======\n>>>>>>> other\n")
    return catalog


@pytest.mark.parametrize(
    ("damage", "ready"),
    [
        (_damage_nothing, {"Sample6": (234283,), "Sample7": (234284,)}),
        (_damage_a_note, {"Sample6": (234283,)}),
        (_damage_the_experiment, {}),
        (_damage_the_lock, {}),
    ],
    ids=["nothing-stops-it", "a-note", "the-experiment", "the-lock"],
)
def test_new_data_counts_what_apply_copies_on_the_same_listing(
    applier: Applier,
    project: Path,
    folder: Path,
    context: RenderContext,
    source,
    damage,
    ready,
) -> None:
    """Every case at once, the sign worked out from the listing apply is given.

    The answer is what apply *does*, not what the Review lists: a sample
    apply refuses still lists its copies, and has none made.
    """
    catalog = catalog_for(*RUNS)
    applier.apply(catalog)
    # Since that apply: one file reduced again, one only touched ...
    rewritten = folder / "REFL_234277_1_234277_autoreduction.dat"
    rewritten.write_text(rewritten.read_text() + "0.3 1e-9 1e-10 0.001\n")
    touch(folder / "REFL_234280_2_234281_autoreduction.dat")
    # ... and new single-segment runs, all complete: NEXT_RUN is later.
    for run in range(234283, 234289):
        write_autoreduced(folder, run, [1], planned=1, mtime=time.time() - 3600)
    hand_copied = steady(project, "Sample7") / "REFL_234286_1_234286_autoreduction.dat"
    hand_copied.parent.mkdir(parents=True)
    hand_copied.write_bytes((folder / hand_copied.name).read_bytes())
    catalog = apply_changes(
        catalog,
        runs=[
            RunChange(RunKey(234283), 0, {"sample_id": "Sample6"}),
            RunChange(RunKey(NEXT_RUN), 0, {"sample_id": "Sample6"}),  # unconfirmed
            RunChange(RunKey(234284), 0, {"sample_id": "Sample7"}),
            RunChange(RunKey(234285), 0, {"sample_id": "Sample7", "include": False}),
            RunChange(RunKey(234286), 0, {"sample_id": "Sample7"}),
            RunChange(RunKey(234288), 0, {"include": False}),  # in no sample, decided
        ],
        now=NOW,
    )
    catalog = damage(project, catalog)

    runs, statuses = observe(source)
    news = new_data(project, catalog, runs, statuses)
    plan = plan_apply(project, catalog, runs, statuses, source, context)
    # Read again by the Review because the version moved, where apply would
    # act at all -- taken before apply rewrites the copy records.
    reread = {
        f.run
        for sample in plan.samples
        if not sample.problems and not plan.problems
        for f in sample.files
        if f.action in (Action.RECORD, Action.SOURCE_CHANGED)
        and f.name in read_sources(project / "samples" / sample.sample_id)
    }
    try:
        report = apply(
            project,
            catalog,
            runs,
            statuses,
            source,
            context,
            expected_plan_id=plan.plan_id,
        )
        copied = {done.run for done in report.done if done.action is Action.COPY}
    except ApplyRefused:
        copied = set()

    assert {run for runs in news.ready.values() for run in runs} == copied
    assert {run for runs in news.reduced_again.values() for run in runs} == reread
    refusals = {p.scope for p in plan.problems} | {
        p.scope for sample in plan.samples for p in sample.problems
    }
    assert {p.scope for p in news.problems} == refusals
    # No Apply is offered for what apply would refuse.
    assert not any(s.writes for s in plan.samples if s.problems)
    assert not (plan.problems and plan.writes)
    assert news.ready == ready
    assert news.unassigned == (234287,)  # assigning is not applying


def test_new_data_reads_no_copy(applier: Applier, folder: Path, monkeypatch) -> None:
    """The page asks on every poll: hashing each copy would read the experiment."""
    catalog = catalog_for(*RUNS)
    applier.apply(catalog)
    touch(folder / "REFL_234277_1_234277_autoreduction.dat")

    def no_reading(path: Path) -> None:
        raise AssertionError(f"read {path}")

    monkeypatch.setattr(apply_module, "sha256_file_or_none", no_reading)
    # The tripwire is on planning's path: the Review trips it.
    with pytest.raises(AssertionError, match="read"):
        applier.plan(catalog)

    assert news_of(applier, catalog).reduced_again == {"Sample6": (234277,)}


@needs_posix_permissions
def test_new_data_reads_no_copy_by_any_means(applier: Applier, project: Path) -> None:
    """Copies nobody may read: any read of one would be the sample's problem."""
    catalog = catalog_for(*RUNS)
    applier.apply(catalog)
    copies = list(steady(project).iterdir())
    for path in copies:
        path.chmod(0)
    try:
        news = news_of(applier, catalog)
    finally:
        for path in copies:
            path.chmod(0o644)

    assert news.problems == ()


def test_new_data_never_asks_where_a_link_in_the_sample_points(
    applier: Applier, project: Path, tmp_path: Path, monkeypatch
) -> None:
    """`nrw import` links into the facility's tree, on a mount that may stop
    answering: a poll that followed a link would wait with it."""
    catalog = catalog_for(*RUNS)
    applier.apply(catalog)
    copy = steady(project) / "REFL_234277_1_234277_autoreduction.dat"
    target = tmp_path / "facility-mount" / copy.name
    target.parent.mkdir()
    target.write_bytes(copy.read_bytes())
    copy.unlink()
    copy.symlink_to(target)
    # And one nrw did not make, for a run this sample does not use.
    imported = steady(project) / "REFL_234271_1_234271_autoreduction.dat"
    imported.symlink_to(target)
    runs, statuses = observe(applier.source)
    real_stat = os.stat

    def stat(path, *args, follow_symlinks=True, **kwargs):
        if (
            follow_symlinks
            and isinstance(path, (str, os.PathLike))
            and Path(path) in (copy, imported)
        ):
            raise AssertionError(f"followed {path}")
        return real_stat(path, *args, follow_symlinks=follow_symlinks, **kwargs)

    monkeypatch.setattr(os, "stat", stat)
    # The tripwire is where a look that follows links looks.
    for link in (copy, imported):
        with pytest.raises(AssertionError, match="followed"):
            link.is_file()

    news = new_data(project, catalog, runs, statuses)
    plan = applier.plan(catalog)

    assert (news.ready, news.problems) == ({}, ())
    # The Review says both, and follows neither.
    assert actions(plan)[copy.name] is Action.CONFLICT
    assert actions(plan)[imported.name] is Action.NOT_MANAGED


def test_a_moved_source_that_cannot_be_read_is_named_not_taken_as_in_step(
    project: Path, context: RenderContext
) -> None:
    """A remote source can go between listing and reading: a re-reduction must
    not then pass for unchanged."""
    source = InMemorySource()
    source.add_segments(234277, [1, 2, 3])
    source.add_segments(NEXT_RUN, [1])
    applier = Applier(project, context, source)
    catalog = catalog_for(234277)
    applier.apply(catalog)
    name = "REFL_234277_1_234277_autoreduction.dat"
    source.touch(name, mtime=1.0)
    source.fail_reads_after = source.reads

    plan = applier.plan(catalog)

    (chosen,) = (f for f in plan.samples[0].files if f.name == name)
    assert chosen.action is Action.SOURCE_CHANGED
    assert "cannot be read" in chosen.detail
    assert plan.needs_attention


def test_a_hand_copied_file_whose_source_cannot_be_read_is_a_conflict(
    project: Path, context: RenderContext
) -> None:
    source = InMemorySource()
    source.add_segments(234277, [1, 2, 3])
    source.add_segments(NEXT_RUN, [1])
    name = "REFL_234277_1_234277_autoreduction.dat"
    steady(project).mkdir(parents=True)
    (steady(project) / name).write_bytes(source.files[name].data)
    source.fail_reads_after = 0

    plan = Applier(project, context, source).plan(catalog_for(234277))

    (chosen,) = (f for f in plan.samples[0].files if f.name == name)
    assert chosen.action is Action.CONFLICT
    assert "cannot be read" in chosen.detail


def test_new_data_names_a_sample_it_cannot_work_out_and_counts_the_rest(
    applier: Applier, project: Path
) -> None:
    """A sample nothing can be applied to must not look like one with nothing new."""
    catalog = apply_changes(
        catalog_for(234277),
        runs=[RunChange(RunKey(234280), 0, {"sample_id": "Sample7"})],
        now=NOW,
    )
    record = project / "samples" / "Sample6" / "data" / "sources.json"
    record.parent.mkdir(parents=True)
    record.write_text("{not json")

    news = news_of(applier, catalog)

    assert [p.scope for p in news.problems] == ["sample:Sample6"]
    assert "sources.json" in news.problems[0].message
    assert news.ready == {"Sample7": (234280,)}


def test_new_data_names_a_sample_whose_directory_differs_only_in_case(
    applier: Applier, project: Path
) -> None:
    (project / "samples" / "sample6").mkdir(parents=True)

    news = news_of(applier, catalog_for(*RUNS))

    assert news.ready == {}
    assert [p.scope for p in news.problems] == ["sample:Sample6"]
    assert "samples/sample6/" in news.problems[0].message


@needs_posix_permissions
@pytest.mark.parametrize(
    ("locked", "said"),
    [
        ("data", "cannot be looked at"),
        ("data/steady", "cannot be looked at"),
        ("data/sources.json", "may not read"),
    ],
    ids=["its-data", "its-copies", "its-copy-record"],
)
def test_a_sample_folder_nobody_may_read_is_that_samples_problem(
    applier: Applier, project: Path, locked: str, said: str
) -> None:
    """Not the page's: a colleague's umask must not take every poll down. Nor
    read as nothing copied, which Python 3.14's `exists()` would say."""
    catalog = catalog_for(234277)
    applier.apply(catalog)
    catalog = apply_changes(
        catalog, runs=[RunChange(RunKey(234280), 0, {"sample_id": "Sample7"})], now=NOW
    )
    folder = project / "samples" / "Sample6" / locked
    folder.chmod(0)
    try:
        news = news_of(applier, catalog)
        plan = applier.plan(catalog)
    finally:
        folder.chmod(0o755)

    assert [p.scope for p in news.problems] == ["sample:Sample6"]
    # Said as what it is, and without the project's full path.
    assert said in news.problems[0].message
    assert str(project) not in news.problems[0].message
    assert news.ready == {"Sample7": (234280,)}
    refused = {s.sample_id for s in plan.samples if s.problems}
    assert refused == {"Sample6"}


def test_a_sample_id_nrw_would_not_write_is_refused_and_nothing_outside_read(
    applier: Applier, project: Path
) -> None:
    """Only a catalog changed some other way than nrw -- by hand, or a remote
    one -- can hold one, and it may name a path outside samples/."""
    outside = project / "elsewhere"
    (outside / "data").mkdir(parents=True)
    (outside / "data" / "sources.json").write_text("{read if looked at")
    odd = os.path.relpath(outside, project / "samples")
    entry = catalog_for(234277).runs[RunKey(234277)]
    catalog = Catalog(runs={entry.key: dataclasses.replace(entry, sample_id=odd)})

    news = news_of(applier, catalog)
    plan = applier.plan(catalog)

    assert [p.scope for p in news.problems] == [f"sample:{odd}"]
    assert "not a usable sample id" in news.problems[0].message
    assert [p.message for s in plan.samples for p in s.problems] == [
        news.problems[0].message
    ]


def test_a_file_named_for_run_0_is_reported_not_raised(
    applier: Applier, project: Path
) -> None:
    """It raised from planning, which the page's every poll now shares."""
    catalog = catalog_for(*RUNS)
    applier.apply(catalog)
    stray = steady(project) / "REFL_0_combined_data_auto.txt"
    stray.write_text("# not a run\n")

    assert actions(applier.plan(catalog))[stray.name] is Action.NOT_MANAGED
    assert news_of(applier, catalog).problems == ()


def sources_json(project: Path) -> Path:
    path = project / "samples" / "Sample6" / "data" / "sources.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


@pytest.mark.parametrize(
    "text",
    [
        "[" * 200_000,
        '{"schema": "nrw-sources/1", "files": {}, "n": ' + "1" * 5000 + "}",
    ],
    ids=["nested-too-deep", "a-number-too-long"],
)
def test_a_copy_record_python_cannot_parse_is_refused_not_raised(
    project: Path, text: str
) -> None:
    """Neither is a JSONDecodeError: one is a RecursionError, one a ValueError."""
    sources_json(project).write_text(text)

    with pytest.raises(ApplyRefused, match="sources.json"):
        read_sources(project / "samples" / "Sample6")


def test_a_copy_record_that_is_a_directory_is_refused_without_the_full_path(
    project: Path,
) -> None:
    sources_json(project).mkdir()

    with pytest.raises(ApplyRefused) as refused:
        read_sources(project / "samples" / "Sample6")

    assert "samples/Sample6/data/sources.json" in str(refused.value)
    assert str(project) not in str(refused.value)


def test_a_copy_record_too_large_is_refused_unread(project: Path, monkeypatch) -> None:
    monkeypatch.setattr(apply_module, "MAX_SOURCES_BYTES", 1000)
    sources_json(project).write_text(" " * 1001)

    with pytest.raises(ApplyRefused, match="larger than"):
        read_sources(project / "samples" / "Sample6")


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no FIFOs here")
def test_a_copy_record_that_is_a_fifo_is_refused_without_waiting(
    project: Path,
) -> None:
    """Nothing ever writes to it: a read would wait for good, and hold the poll."""
    os.mkfifo(sources_json(project))
    outcome: list[BaseException] = []

    def read() -> None:
        try:
            read_sources(project / "samples" / "Sample6")
        except BaseException as exc:  # noqa: BLE001 - kept for the assertion
            outcome.append(exc)

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    reader.join(timeout=5)

    assert not reader.is_alive(), "read_sources is waiting on the FIFO"
    assert isinstance(outcome[0], ApplyRefused)


@pytest.mark.skipif(not Path("/dev/zero").exists(), reason="no /dev/zero here")
def test_a_copy_record_linked_to_a_device_is_refused_not_read(project: Path) -> None:
    """/dev/zero never ends: read whole on every poll, it would fill memory."""
    sources_json(project).symlink_to("/dev/zero")

    with pytest.raises(ApplyRefused, match="not a regular file"):
        read_sources(project / "samples" / "Sample6")


def test_new_data_as_dict_lists_the_runs_overall_and_per_sample() -> None:
    news = NewData(
        ready={"Sample6": (234277, 234283), "Sample7": (234280,)},
        reduced_again={"Sample8": (234271,)},
        unassigned=(234287, 234281),
    )

    assert news.as_dict() == {
        "ready": [234277, 234280, 234283],
        "reduced_again": [234271],
        "unassigned": [234281, 234287],
        "samples": {
            "Sample6": {"ready": [234277, 234283], "reduced_again": []},
            "Sample7": {"ready": [234280], "reduced_again": []},
            "Sample8": {"ready": [], "reduced_again": [234271]},
        },
    }


# --------------------------------------------------------------------------
# A sample the catalog no longer has, with copies still in it
# --------------------------------------------------------------------------


def dropped_sample(applier: Applier, sample: str = "Sample8") -> Catalog:
    """234280 assigned to *sample* alone -- no record of its own -- and applied,
    then moved to Sample7: no run names *sample* now, so the catalog has none."""
    catalog = apply_changes(
        catalog_for(234277),
        runs=[RunChange(RunKey(234280), 0, {"sample_id": sample})],
        now=NOW,
    )
    applier.apply(catalog)
    return edit(catalog, 234280, sample_id="Sample7")


def planned(plan) -> set[str]:
    return {s.sample_id for s in plan.samples}


def test_a_sample_dropped_from_the_catalog_has_its_copies_moved_out(
    applier: Applier, project: Path
) -> None:
    """Found in review: its copies stayed where every reader looks, and the run
    was in two samples."""
    catalog = dropped_sample(applier)
    assert "Sample8" not in catalog.sample_ids()
    sample_md = (project / "samples" / "Sample8" / "sample.md").read_bytes()
    # Files nrw did not copy there are someone's, and are never moved.
    notes = steady(project, "Sample8") / "notes.txt"
    notes.write_text("mine\n")
    by_hand = steady(project, "Sample8") / "REFL_234271_1_234271_autoreduction.dat"
    by_hand.write_text("# by hand\n")

    plan, report = applier.apply(catalog)

    (dropped,) = (s for s in plan.samples if s.sample_id == "Sample8")
    assert dropped.former
    assert dropped.as_dict()["note"].startswith("is no longer in the catalog")
    assert set(actions(plan, "Sample8")) == {
        f"REFL_234280_{n}_{234279 + n}_autoreduction.dat" for n in (1, 2, 3)
    }
    assert set(actions(plan, "Sample8").values()) == {Action.MOVE_OUT}
    assert not list(steady(project, "Sample8").glob("REFL_234280_*"))
    out = project / "samples" / "Sample8" / "data" / "excluded" / "234280"
    assert len(list(out.glob("REFL_234280_*"))) == 3
    assert len(list(steady(project, "Sample7").glob("REFL_234280_*"))) == 3
    # Not the catalog's to write any more: left exactly as it was.
    assert (project / "samples" / "Sample8" / "sample.md").read_bytes() == sample_md
    assert (notes.read_text(), by_hand.read_text()) == ("mine\n", "# by hand\n")
    assert not report.failed


def test_a_dropped_sample_is_nothing_new_to_the_sign(applier: Applier) -> None:
    """What moves out is what a person moved, as for any sample."""
    news = news_of(applier, dropped_sample(applier))

    assert (news.ready, news.reduced_again, news.problems) == (
        {"Sample7": (234280,)},
        {},
        (),
    )


def test_a_dropped_sample_with_its_copies_out_is_not_planned_again(
    applier: Applier,
) -> None:
    catalog = dropped_sample(applier)
    applier.apply(catalog)

    plan = applier.plan(catalog)

    assert "Sample8" not in planned(plan)
    assert not plan.writes


def test_a_dropped_samples_copies_come_back_when_its_run_does(
    applier: Applier, project: Path
) -> None:
    catalog = dropped_sample(applier)
    applier.apply(catalog)
    back = edit(catalog, 234280, sample_id="Sample8")

    plan, report = applier.apply(back)

    assert set(actions(plan, "Sample8").values()) == {Action.RESTORE}
    assert set(actions(plan, "Sample7").values()) == {Action.MOVE_OUT}
    assert len(list(steady(project, "Sample8").glob("REFL_234280_*"))) == 3
    assert not report.failed
    assert not applier.plan(back).writes


def test_a_dropped_sample_is_applied_only_when_named(
    applier: Applier, project: Path
) -> None:
    catalog = dropped_sample(applier)

    assert "Sample8" not in planned(applier.plan(catalog, samples=["Sample7"]))
    plan, report = applier.apply(catalog, samples=["Sample8"])

    assert not plan.problems and not report.failed
    out = project / "samples" / "Sample8" / "data" / "excluded" / "234280"
    assert len(list(out.glob("REFL_234280_*"))) == 3
    assert not (project / "samples" / "Sample7").exists()


def test_a_dropped_sample_moves_out_only_what_is_still_in_steady(
    applier: Applier, project: Path
) -> None:
    """One run excluded and moved out earlier, then both moved away."""
    catalog = apply_changes(
        Catalog(),
        runs=[RunChange(RunKey(run), 0, {"sample_id": "Sample8"}) for run in RUNS],
        now=NOW,
    )
    applier.apply(catalog)
    catalog = edit(catalog, 234277, include=False)
    applier.apply(catalog)
    catalog = edit(
        edit(catalog, 234277, sample_id="Sample7"), 234280, sample_id="Sample7"
    )

    plan, report = applier.apply(catalog)

    moving = actions(plan, "Sample8")
    assert len(moving) == 3 and all(n.startswith("REFL_234280_") for n in moving)
    assert not list(steady(project, "Sample8").glob("REFL_*"))
    assert not report.failed


def test_a_dropped_sample_whose_copies_are_gone_is_not_planned(
    applier: Applier, project: Path
) -> None:
    """Deleted by hand, as someone tidying a stale folder would: nothing is
    left to move, and no apply after fails over it."""
    catalog = dropped_sample(applier)
    for copy in steady(project, "Sample8").glob("REFL_*"):
        copy.unlink()

    plan, report = applier.apply(catalog)

    assert "Sample8" not in planned(plan)
    assert not report.failed


def test_a_catalog_that_knows_none_of_a_samples_runs_moves_nothing(
    applier: Applier, project: Path
) -> None:
    """What the store gives once experiment/ is gone -- a fresh clone that
    never had it, a `git clean` -- is no instruction to empty every sample."""
    applier.apply(catalog_for(*RUNS))

    plan, report = applier.apply(Catalog())

    assert plan.samples == () and not report.done
    assert len(list(steady(project).glob("REFL_*"))) == 6


def _release(project: Path) -> str:
    """`nrw experiment release`: the lock forgets sample.md."""
    from nr_workbench.project.scaffold import forget

    forget(project, "samples/Sample8/sample.md")
    return "Sample8"


def _edit_the_template(project: Path) -> str:
    """Sample1, as `nrw sample new` made it, with sample.md edited by hand."""
    md = project / "samples" / "Sample1" / "sample.md"
    md.write_text(md.read_text() + "\nMy own line.\n")
    return "Sample1"


def _write_one_by_hand(project: Path) -> str:
    """A sample.md nrw never wrote."""
    (project / "samples" / "Sample9").mkdir(parents=True)
    (project / "samples" / "Sample9" / "sample.md").write_text("# Sample9\n")
    return "Sample9"


@pytest.mark.parametrize(
    "made",
    [_release, _edit_the_template, _write_one_by_hand],
    ids=["released", "an-edited-template", "written-by-hand"],
)
def test_a_dropped_sample_has_its_copies_moved_out_whoever_wrote_its_sample_md(
    applier: Applier, project: Path, made
) -> None:
    """The copies are apply's, whoever wrote the file beside them."""
    if made is _release:
        catalog = dropped_sample(applier)
        sample = made(project)
    else:
        sample = made(project)
        catalog = dropped_sample(applier, sample)

    plan, report = applier.apply(catalog)

    assert set(actions(plan, sample).values()) == {Action.MOVE_OUT}
    assert not list(steady(project, sample).glob("REFL_234280_*"))
    assert not report.failed


def test_a_sample_copied_in_from_another_experiment_is_left_alone(
    applier: Applier, project: Path
) -> None:
    """Its copy record names runs this catalog has never heard of."""
    stranger = project / "samples" / "Elsewhere"
    (stranger / "data" / "steady").mkdir(parents=True)
    name = "REFL_199001_1_199001_autoreduction.dat"
    (stranger / "data" / "steady" / name).write_text("# theirs\n")
    (stranger / "data" / "sources.json").write_text(
        json.dumps(
            {
                "schema": "nrw-sources/1",
                "files": {name: {"run": 199001, "sha256": "0" * 64}},
            }
        )
    )

    plan, _ = applier.apply(catalog_for(*RUNS))

    assert "Elsewhere" not in planned(plan)
    assert (stranger / "data" / "steady" / name).read_text() == "# theirs\n"


def test_a_folder_differing_only_in_case_is_no_dropped_sample(
    applier: Applier,
) -> None:
    """The run moved on to `sample8`: on macOS and Windows that is Sample8's own
    folder, so Sample8 is not emptied, and `sample8` is refused."""
    catalog = edit(dropped_sample(applier), 234280, sample_id="sample8")

    plan = applier.plan(catalog)

    by_id = {s.sample_id: s for s in plan.samples}
    assert "Sample8" not in by_id
    assert "only in case" in by_id["sample8"].problems[0].message


@needs_posix_permissions
def test_a_folder_nobody_may_look_into_is_no_dropped_sample(
    applier: Applier, project: Path
) -> None:
    """Not the catalog's, and not looked into: nothing to plan, and no error."""
    catalog = dropped_sample(applier)
    data = project / "samples" / "Sample8" / "data"
    data.chmod(0)
    try:
        plan = applier.plan(catalog)
    finally:
        data.chmod(0o755)

    assert "Sample8" not in planned(plan)


def test_a_folder_named_as_nrw_names_no_sample_is_no_dropped_sample(
    applier: Applier, project: Path
) -> None:
    catalog = dropped_sample(applier)
    (project / "samples" / "Sample8").rename(project / "samples" / "-Sample8")

    assert "-Sample8" not in planned(applier.plan(catalog))


def test_a_dropped_sample_whose_record_cannot_be_read_is_named(
    applier: Applier, project: Path
) -> None:
    """nrw's record, damaged: its copies cannot be told apart, and the Review says so."""
    catalog = dropped_sample(applier)
    (project / "samples" / "Sample8" / "data" / "sources.json").write_text("{nope")

    plan = applier.plan(catalog)

    (dropped,) = (s for s in plan.samples if s.sample_id == "Sample8")
    assert dropped.former and [p.scope for p in dropped.problems] == ["sample:Sample8"]
    assert dropped.problems[0].message.startswith("Sample8 is not in the catalog, and")
    assert "sources.json" in dropped.problems[0].message


# --------------------------------------------------------------------------
# Nothing is written through a link, nor by a name that is not a file's
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "linked", ["data/excluded/234280", "data/excluded", "data/steady", "data", "."]
)
def test_nothing_is_moved_or_copied_through_a_link_among_a_samples_folders(
    applier: Applier, project: Path, tmp_path: Path, linked: str
) -> None:
    """A link there would carry a move, or a copy, to wherever it points; whoever
    can write in the project can plant one."""
    catalog = catalog_for(*RUNS)
    applier.apply(catalog)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    folder = (project / "samples" / "Sample6" / linked).resolve()
    if folder.exists():
        shutil.move(folder, tmp_path / "set-aside")
    folder.parent.mkdir(parents=True, exist_ok=True)
    folder.symlink_to(elsewhere)

    # Excluding a copied run moves it out; a new run would be copied in.
    write_autoreduced(
        folder_of(applier), 234283, [1], planned=1, mtime=time.time() - 3600
    )
    catalog = apply_changes(
        edit(catalog, 234280, include=False),
        runs=[RunChange(RunKey(234283), 0, {"sample_id": "Sample6"})],
        now=NOW,
    )
    plan, report = applier.apply(catalog)

    assert list(elsewhere.iterdir()) == []
    assert plan.needs_attention
    assert not any(done.run == 234280 for done in report.done)


def test_nothing_is_moved_back_through_a_link(
    applier: Applier, project: Path, tmp_path: Path
) -> None:
    catalog = edit(catalog_for(*RUNS), 234280, include=False)
    applier.apply(catalog_for(*RUNS))
    applier.apply(catalog)
    out = project / "samples" / "Sample6" / "data" / "excluded" / "234280"
    elsewhere = tmp_path / "elsewhere"
    shutil.move(out, elsewhere)
    out.symlink_to(elsewhere)  # the copies to move back, reached by a link

    plan, report = applier.apply(edit(catalog, 234280, include=True))

    assert {a for n, a in actions(plan).items() if "234280" in n} == {Action.CONFLICT}
    assert not list(steady(project).glob("REFL_234280_*"))
    assert len(list(elsewhere.glob("REFL_234280_*"))) == 3
    assert not report.done


def test_a_link_planted_while_apply_runs_takes_nothing_through_it(
    project: Path, context: RenderContext, tmp_path: Path
) -> None:
    """Planned without links, and looked at again right before each write."""
    source = InMemorySource()
    for run in (*RUNS, 234283, 234286):
        source.add_segments(run, [1, 2, 3])
    source.add_segments(234289, [1], planned=1)
    source.add_segments(NEXT_RUN, [1])
    applier = Applier(project, context, source)
    catalog = apply_changes(
        catalog_for(*RUNS),
        runs=[RunChange(RunKey(234283), 0, {"sample_id": "Sample7"})],
        now=NOW,
    )
    applier.apply(catalog)
    # Sample6 is to copy 234286 and move 234277 out; Sample7 to copy 234289.
    catalog = apply_changes(
        edit(catalog, 234277, include=False),
        runs=[
            RunChange(RunKey(234286), 0, {"sample_id": "Sample6"}),
            RunChange(RunKey(234289), 0, {"sample_id": "Sample7"}),
        ],
        now=NOW,
    )
    plan = applier.plan(catalog)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    out = project / "samples" / "Sample6" / "data" / "excluded" / "234277"
    sample7 = project / "samples" / "Sample7"
    read = source.read_bytes

    # The first copy apply makes -- Sample6's -- plants a link where Sample6 is
    # about to move to, and puts one in place of Sample7, not reached yet.
    def plant_then_read(file, *, max_bytes):
        if not out.is_symlink():
            out.parent.mkdir(parents=True, exist_ok=True)
            out.symlink_to(elsewhere)
            shutil.move(sample7, tmp_path / "Sample7-aside")
            sample7.symlink_to(elsewhere)
        return read(file, max_bytes=max_bytes)

    source.read_bytes = plant_then_read
    runs, statuses = observe(source)
    report = apply(
        project, catalog, runs, statuses, source, context, expected_plan_id=plan.plan_id
    )

    assert list(elsewhere.iterdir()) == []
    linked = {f.run for f in report.failed if "is a link" in f.detail}
    assert linked == {234277, 234289}


@pytest.mark.parametrize(
    "name",
    [
        "../../outside.dat",
        "a/b.dat",
        "..",
        "",
        "a\\b.dat",
        "a\x00.dat",
        "C:x.dat",
        "a.dat:stream",
        "NUL",
        "com1.dat",
        "a.dat.",
        "a.dat ",
        "a\u202e.dat",
        "a" * 252 + ".dat",
    ],
    ids=[
        "up-and-out",
        "a-folder",
        "the-parent",
        "empty",
        "a-backslash",
        "a-nul",
        "a-drive",
        "a-stream",
        "a-device",
        "a-device-with-a-suffix",
        "a-trailing-dot",
        "a-trailing-space",
        "a-right-to-left-override",
        "too-long",
    ],
)
def test_a_copy_record_naming_anything_but_a_file_of_its_own_is_refused(
    project: Path, name: str
) -> None:
    """Apply moves files by these names, and a person approves them by reading."""
    sources_json(project).write_text(
        json.dumps(
            {
                "schema": "nrw-sources/1",
                "files": {name: {"run": 234277, "sha256": "0" * 64}},
            }
        )
    )

    with pytest.raises(ApplyRefused, match="not one nrw wrote"):
        read_sources(project / "samples" / "Sample6")


def test_apply_never_moves_a_file_a_copy_record_points_outside_at(
    applier: Applier, project: Path
) -> None:
    """A key of `../../../../notes.txt` once had apply move the project's own
    file; moving back was as open."""
    catalog = catalog_for(*RUNS)
    applier.apply(catalog)
    notes = project / "notes.txt"
    notes.write_text("mine\n")
    record = project / "samples" / "Sample6" / "data" / "sources.json"
    document = json.loads(record.read_text())
    document["files"]["../../../../notes.txt"] = {"run": 234280, "sha256": "0" * 64}
    record.write_text(json.dumps(document))

    # Excluding the run is what would move its "copies" out.
    plan, report = applier.apply(edit(catalog, 234280, include=False))

    assert notes.read_text() == "mine\n"
    assert not (project / "samples" / "notes.txt").exists()
    (sample6,) = (s for s in plan.samples if s.sample_id == "Sample6")
    assert sample6.problems and not report.done
