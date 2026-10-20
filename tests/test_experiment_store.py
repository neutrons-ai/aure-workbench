"""The parquet catalog store: nothing a person decided may be lost quietly.

Each test here is one way the catalog could lose the experimenter's decisions
without anyone noticing: a damaged file read as empty, an interrupted save
read as complete, a binary merge conflict read as resolved, two writers
overwriting each other, or an older nrw dropping columns it does not know.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from nr_workbench.experiment import store as store_module
from nr_workbench.experiment.model import (
    ExperimentChange,
    RecordConflict,
    RunChange,
    RunKey,
    SampleChange,
)
from nr_workbench.experiment.store import (
    EXPERIMENT_FILE,
    MANIFEST_FILE,
    RUNS_FILE,
    SAMPLES_FILE,
    CatalogCorruptError,
    CatalogError,
    CatalogUnmergedError,
    CatalogVersionError,
    ParquetCatalogStore,
)

NOW = "2026-09-25T12:00:00Z"


@pytest.fixture
def store(tmp_path: Path) -> ParquetCatalogStore:
    return ParquetCatalogStore(tmp_path / "experiment", tmp_path / "cache")


def assign(store: ParquetCatalogStore, run: int, base_rev: int = 0, **changes):
    return store.update(runs=[RunChange(RunKey(run), base_rev, changes)], now=NOW)


def populated(store: ParquetCatalogStore) -> ParquetCatalogStore:
    assign(store, 218386, sample_id="Sample6", condition="OCV", title="CuPt-218386-1.")
    store.update(
        samples=[SampleChange("Sample6", 0, {"title": "Cu/Pt", "mounting": "once"})],
        now=NOW,
    )
    return store


# --------------------------------------------------------------------------
# Round trip
# --------------------------------------------------------------------------


def test_load_before_anything_is_written_is_an_empty_catalog(store) -> None:
    catalog = store.load()

    assert catalog.runs == {} and catalog.samples == {}
    assert not store.exists()


def test_round_trip_preserves_every_field(store) -> None:
    populated(store)

    catalog = ParquetCatalogStore(store.directory, store.cache_dir).load()

    entry = catalog.runs[RunKey(218386)]
    assert (entry.sample_id, entry.condition, entry.title, entry.rev) == (
        "Sample6",
        "OCV",
        "CuPt-218386-1.",
        1,
    )
    assert entry.include is True
    context = catalog.samples["Sample6"]
    assert (context.title, context.mounting, context.rev) == ("Cu/Pt", "once", 1)


def test_the_manifest_records_what_was_written(store) -> None:
    populated(store)

    manifest = json.loads((store.directory / MANIFEST_FILE).read_text())

    assert manifest["schema"] == "nrw-experiment/1"
    assert manifest["tables"]["runs"]["rows"] == 1
    assert manifest["tables"]["samples"]["rows"] == 1


def test_tables_carry_their_schema_and_name(store) -> None:
    import pyarrow.parquet as pq

    populated(store)

    metadata = pq.read_schema(store.directory / RUNS_FILE).metadata
    assert metadata[b"nrw.schema"] == b"nrw-experiment/1"
    assert metadata[b"nrw.table"] == b"runs"


def test_no_absolute_path_is_stored(store) -> None:
    """`nrw check` greps committed files for home paths, but skips binaries."""
    populated(store)

    for name in (RUNS_FILE, SAMPLES_FILE, MANIFEST_FILE):
        data = (store.directory / name).read_bytes()
        assert str(store.directory).encode() not in data
        assert b"/Users/" not in data and b"/home/" not in data


# --------------------------------------------------------------------------
# A damaged catalog is never an empty one
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "damage",
    [
        pytest.param(lambda data: b"", id="zero_bytes"),
        pytest.param(lambda data: data[: len(data) // 2], id="truncated"),
        pytest.param(lambda data: b"not parquet at all", id="garbage"),
    ],
)
def test_load_a_damaged_table_raises_rather_than_loading_empty(store, damage) -> None:
    populated(store)
    path = store.directory / RUNS_FILE
    path.write_bytes(damage(path.read_bytes()))
    # Keep the manifest consistent with the damage, so the only thing standing
    # between the damage and an empty catalog is the table read itself.
    manifest = json.loads((store.directory / MANIFEST_FILE).read_text())
    manifest["tables"]["runs"]["sha256"] = store_module._sha(path.read_bytes())
    (store.directory / MANIFEST_FILE).write_text(json.dumps(manifest))

    with pytest.raises(CatalogCorruptError):
        store.load()


def test_corrupt_error_is_not_a_value_error() -> None:
    """pyarrow's ArrowInvalid IS a ValueError; this must not be catchable as one."""
    assert not issubclass(CatalogCorruptError, ValueError)
    assert issubclass(CatalogCorruptError, CatalogError)


def test_update_on_a_damaged_catalog_refuses_and_leaves_it_alone(store) -> None:
    populated(store)
    (store.directory / RUNS_FILE).write_bytes(b"")
    before = {p.name: p.read_bytes() for p in store.directory.iterdir()}

    with pytest.raises(CatalogError):
        assign(store, 218393, sample_id="Sample6")

    assert {p.name: p.read_bytes() for p in store.directory.iterdir()} == before


def test_a_table_swapped_for_the_other_is_refused(store) -> None:
    populated(store)
    shutil.copy(store.directory / SAMPLES_FILE, store.directory / RUNS_FILE)
    (store.directory / MANIFEST_FILE).unlink()

    with pytest.raises(CatalogCorruptError, match="not the nrw runs table"):
        store.load()


def test_a_row_with_the_wrong_type_is_refused(store) -> None:
    """A table edited by hand (say, with pandas) can carry include="false"."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    populated(store)
    path = store.directory / RUNS_FILE
    table = pq.read_table(path)
    index = table.schema.get_field_index("include")
    table = table.set_column(index, "include", pa.array(["false"]))
    pq.write_table(table, path)
    (store.directory / MANIFEST_FILE).unlink()

    with pytest.raises(CatalogCorruptError, match="include"):
        store.load()


def test_missing_manifest_loads_the_tables_and_says_so(store) -> None:
    populated(store)
    (store.directory / MANIFEST_FILE).unlink()

    catalog, problems = store.load_report()

    assert RunKey(218386) in catalog.runs
    assert any("manifest" in p.message for p in problems)


# --------------------------------------------------------------------------
# Interrupted saves
# --------------------------------------------------------------------------


def _crash_on_write_number(monkeypatch, n: int) -> None:
    """Make the n-th file replacement of the next save raise, as a crash would."""
    real = store_module._replace
    calls = {"count": 0}

    def replace(target, data, scratch):
        calls["count"] += 1
        if calls["count"] == n:
            raise OSError("simulated crash")
        return real(target, data, scratch)

    monkeypatch.setattr(store_module, "_replace", replace)


def test_a_crash_between_the_two_tables_recovers_the_last_complete_catalog(
    store, monkeypatch
) -> None:
    populated(store)
    _crash_on_write_number(monkeypatch, 2)  # runs replaced, samples not

    with pytest.raises(OSError, match="simulated crash"):
        store.update(
            runs=[RunChange(RunKey(218386), 1, {"condition": "CA"})],
            samples=[SampleChange("Sample6", 1, {"title": "renamed"})],
            now=NOW,
        )
    monkeypatch.undo()

    catalog, problems = store.load_report()

    assert catalog.runs[RunKey(218386)].condition == "OCV"
    assert catalog.samples["Sample6"].title == "Cu/Pt"
    assert any("interrupted" in p.message for p in problems)


def test_the_next_save_after_a_crash_writes_a_consistent_catalog(
    store, monkeypatch
) -> None:
    populated(store)
    _crash_on_write_number(monkeypatch, 2)
    with pytest.raises(OSError):
        assign(store, 218386, base_rev=1, condition="CA")
    monkeypatch.undo()

    assign(store, 218386, base_rev=1, condition="CA")

    catalog, problems = ParquetCatalogStore(
        store.directory, store.cache_dir
    ).load_report()
    assert catalog.runs[RunKey(218386)].condition == "CA"
    assert problems == ()


def test_a_mismatch_the_kept_copy_does_not_explain_is_refused(store) -> None:
    """Restoring an older generation silently would undo edits nobody was told of."""
    populated(store)
    assign(store, 218393, sample_id="Sample6")
    # Damage the current runs table; the kept copy is from the previous save,
    # which the current manifest does not describe.
    (store.directory / RUNS_FILE).write_bytes(b"PAR1 damaged")

    with pytest.raises(CatalogCorruptError, match="manifest"):
        store.load()


# --------------------------------------------------------------------------
# Versions and unknown columns
# --------------------------------------------------------------------------


def test_a_catalog_from_a_newer_nrw_is_refused(store) -> None:
    populated(store)
    manifest = json.loads((store.directory / MANIFEST_FILE).read_text())
    manifest["schema_version"] = 99
    (store.directory / MANIFEST_FILE).write_text(json.dumps(manifest))

    with pytest.raises(CatalogVersionError, match="newer"):
        store.load()
    with pytest.raises(CatalogVersionError):
        assign(store, 218393, sample_id="Sample6")


def test_columns_this_version_does_not_know_survive_a_save(store) -> None:
    """A column added by a newer nrw must not vanish when an older one saves."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    populated(store)
    path = store.directory / RUNS_FILE
    table = pq.read_table(path)
    table = table.append_column("control_mode", pa.array(["galvanostatic"]))
    table = table.replace_schema_metadata(pq.read_schema(path).metadata)
    pq.write_table(table, path)
    (store.directory / MANIFEST_FILE).unlink()

    assign(store, 218386, base_rev=1, condition="-0.5 mA/cm2")

    reread = pq.read_table(path).to_pylist()
    assert reread[0]["control_mode"] == "galvanostatic"
    assert reread[0]["condition"] == "-0.5 mA/cm2"


# --------------------------------------------------------------------------
# Concurrent edits
# --------------------------------------------------------------------------


def test_an_edit_on_a_stale_record_is_refused_and_writes_nothing(store) -> None:
    populated(store)
    before = (store.directory / RUNS_FILE).read_bytes()

    with pytest.raises(RecordConflict):
        assign(store, 218386, base_rev=0, condition="CA")

    assert (store.directory / RUNS_FILE).read_bytes() == before


def test_an_edit_to_another_record_is_merged_not_refused(store) -> None:
    populated(store)

    assign(store, 218393, sample_id="Sample6")
    store.update(samples=[SampleChange("Sample6", 1, {"title": "B"})], now=NOW)

    catalog = store.load()
    assert set(catalog.runs) == {RunKey(218386), RunKey(218393)}


def test_an_unchanged_save_writes_nothing(store) -> None:
    def stamps() -> dict[str, tuple[int, int]]:
        # The inode as well: a replace within one tick of a coarse clock keeps
        # the mtime and still makes a new file -- and a new git diff.
        return {
            p.name: (p.stat().st_mtime_ns, p.stat().st_ino)
            for p in store.directory.iterdir()
        }

    populated(store)
    before = stamps()

    assign(store, 218386, base_rev=1, condition="OCV")

    assert stamps() == before


def test_concurrent_saves_from_many_threads_lose_nothing(store) -> None:
    """The web server is threaded; every writer's edit must survive."""
    runs = list(range(218400, 218416))
    errors: list[Exception] = []

    def edit(run: int) -> None:
        try:
            assign(store, run, sample_id="Sample6")
        except Exception as exc:  # noqa: BLE001 - reported below
            errors.append(exc)

    threads = [threading.Thread(target=edit, args=(run,)) for run in runs]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert {key.run for key in store.load().runs} == set(runs)


# --------------------------------------------------------------------------
# The experiment's own context
# --------------------------------------------------------------------------


def describe(store: ParquetCatalogStore, base_rev: int = 0, **changes):
    return store.update(experiment=ExperimentChange(base_rev, changes), now=NOW)


def _crash_on_write_to(monkeypatch, name: str) -> None:
    """Make the next save's replacement of *name* raise, as a crash would."""
    real = store_module._replace

    def replace(target, data, scratch):
        if Path(target).name == name:
            raise OSError("simulated crash")
        return real(target, data, scratch)

    monkeypatch.setattr(store_module, "_replace", replace)


def _list_again(store: ParquetCatalogStore) -> None:
    """Make the manifest vouch for the experiment table as it now is."""
    import hashlib

    manifest = json.loads((store.directory / MANIFEST_FILE).read_text())
    data = (store.directory / EXPERIMENT_FILE).read_bytes()
    manifest["tables"]["experiment"]["sha256"] = hashlib.sha256(data).hexdigest()
    (store.directory / MANIFEST_FILE).write_text(json.dumps(manifest))


def _unlist(store: ParquetCatalogStore) -> None:
    manifest = json.loads((store.directory / MANIFEST_FILE).read_text())
    del manifest["tables"]["experiment"]
    (store.directory / MANIFEST_FILE).write_text(json.dumps(manifest))


def _as_an_older_nrw_saves(store: ParquetCatalogStore) -> None:
    """What an nrw from before the experiment's table does when it saves.

    It keeps the runs, the samples and the manifest aside, as its own
    `_keep_previous` did, and writes a manifest that lists no experiment
    table. The file it does not know stays where it is.
    """
    previous = store.cache_dir / "previous"
    previous.mkdir(parents=True, exist_ok=True)
    for name in (RUNS_FILE, SAMPLES_FILE, MANIFEST_FILE):
        shutil.copy2(store.directory / name, previous / name)
    _unlist(store)


def test_the_experiments_context_round_trips(store) -> None:
    populated(store)

    describe(
        store,
        title="Cu in THF electrolytes",
        details="Every sample in the same cell.",
        measurement_conditions="Aligned once per sample.",
    )

    experiment = ParquetCatalogStore(store.directory, store.cache_dir).load().experiment
    assert experiment.content() == (
        "Cu in THF electrolytes",
        "",
        "Every sample in the same cell.",
        "Aligned once per sample.",
    )
    assert (experiment.rev, experiment.updated_at) == (1, NOW)
    manifest = json.loads((store.directory / MANIFEST_FILE).read_text())
    assert manifest["tables"]["experiment"]["rows"] == 1


def test_a_catalog_that_never_sets_it_keeps_the_files_it_always_had(store) -> None:
    """Every project that predates it reads, and saves, exactly as before."""
    populated(store)

    assert not (store.directory / EXPERIMENT_FILE).exists()
    manifest = json.loads((store.directory / MANIFEST_FILE).read_text())
    assert "experiment" not in manifest["tables"]
    catalog, problems = store.load_report()
    assert catalog.experiment.rev == 0 and problems == ()


def test_an_experiment_table_the_manifest_does_not_list_is_used_and_said(
    store,
) -> None:
    """An nrw from before it keeps the file when it saves, but stops listing it."""
    populated(store)
    describe(store, title="Cu in THF")
    _unlist(store)

    catalog, problems = store.load_report()

    assert catalog.experiment.title == "Cu in THF"
    assert any("not in the manifest" in p.message for p in problems)
    # The next save lists it again.
    describe(store, base_rev=1, title="Cu in d8-THF")
    assert store.load_report()[1] == ()


@pytest.mark.parametrize("manifest", ["vouching for it", "not listing it"])
def test_a_damaged_experiment_table_is_refused_not_read_as_empty(
    store, manifest: str
) -> None:
    """Read as empty, the next apply would strip it from every sample.md."""
    populated(store)
    describe(store, title="Cu in THF")
    (store.directory / EXPERIMENT_FILE).write_bytes(b"PAR1 damaged")
    # So that the refusal is the table's own, and not the manifest's.
    _list_again(store) if manifest == "vouching for it" else _unlist(store)

    with pytest.raises(CatalogCorruptError, match="cannot be read"):
        store.load()
    with pytest.raises(CatalogCorruptError):
        describe(store, base_rev=1, title="anything")


def test_an_experiment_table_of_two_rows_is_refused(store) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    populated(store)
    describe(store, title="Cu in THF")
    path = store.directory / EXPERIMENT_FILE
    table = pq.read_table(path)
    pq.write_table(pa.concat_tables([table, table]), path)
    _list_again(store)

    with pytest.raises(CatalogCorruptError, match="2 rows"):
        store.load()


@pytest.mark.parametrize("column,value", [("rev", "1"), ("title", 7)])
def test_an_experiment_row_of_the_wrong_type_is_refused(
    store, column: str, value
) -> None:
    """Another program's `"1"` is no revision, and a number is no title."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    populated(store)
    describe(store, title="Cu in THF")
    path = store.directory / EXPERIMENT_FILE
    rows = pq.read_table(path).to_pylist()
    rows[0][column] = value
    metadata = pq.read_schema(path).metadata
    pq.write_table(pa.Table.from_pylist(rows).replace_schema_metadata(metadata), path)
    _list_again(store)

    with pytest.raises(CatalogCorruptError, match="its row cannot be read"):
        store.load()


def test_columns_the_experiment_table_does_not_know_survive_a_save(store) -> None:
    """A field a newer nrw added -- a proposal, say -- outlives an older one's save."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    populated(store)
    describe(store, title="Cu in THF")
    path = store.directory / EXPERIMENT_FILE
    table = pq.read_table(path).append_column("proposal", pa.array(["In operando"]))
    pq.write_table(table.replace_schema_metadata(pq.read_schema(path).metadata), path)
    _list_again(store)

    assign(store, 218386, base_rev=1, condition="CA")  # a save of a run alone

    reread = pq.read_table(path).to_pylist()
    assert (reread[0]["proposal"], reread[0]["title"]) == ("In operando", "Cu in THF")


def test_a_stale_edit_of_the_experiments_context_writes_nothing(store) -> None:
    populated(store)
    describe(store, title="Cu in THF")
    before = {
        name: (store.directory / name).read_bytes()
        for name in (RUNS_FILE, EXPERIMENT_FILE)
    }

    with pytest.raises(RecordConflict, match="the experiment's context"):
        store.update(
            runs=[RunChange(RunKey(218393), 0, {"sample_id": "Sample6"})],
            experiment=ExperimentChange(0, {"title": "Cu in d8-THF"}),
            now=NOW,
        )

    after = {
        name: (store.directory / name).read_bytes()
        for name in (RUNS_FILE, EXPERIMENT_FILE)
    }
    assert after == before


def test_a_crash_before_its_table_is_replaced_keeps_the_last_context(
    store, monkeypatch
) -> None:
    populated(store)
    describe(store, title="Cu in THF")
    _crash_on_write_to(monkeypatch, EXPERIMENT_FILE)

    with pytest.raises(OSError, match="simulated crash"):
        describe(store, base_rev=1, title="Cu in d8-THF")
    monkeypatch.undo()

    catalog, problems = store.load_report()
    assert catalog.experiment.title == "Cu in THF"
    assert problems == ()  # the manifest was never replaced: nothing was half-saved


def test_a_crash_after_its_table_is_replaced_recovers_the_last_context(
    store, monkeypatch
) -> None:
    populated(store)
    describe(store, title="Cu in THF")
    describe(store, base_rev=1, title="Cu in d8-THF")
    # Then a save whose experiment table lands and whose manifest does not.
    _crash_on_write_to(monkeypatch, MANIFEST_FILE)

    with pytest.raises(OSError, match="simulated crash"):
        describe(store, base_rev=2, title="Cu in THF again")
    monkeypatch.undo()

    catalog, problems = store.load_report()
    assert catalog.experiment.title == "Cu in d8-THF"
    assert any("interrupted" in p.message for p in problems)


def test_a_crash_after_an_older_nrw_saved_keeps_the_context_it_left(
    store, monkeypatch
) -> None:
    """The kept set holds the table the manifest no longer lists, as loads read it."""
    populated(store)
    describe(store, title="Cu in THF")
    _as_an_older_nrw_saves(store)
    _crash_on_write_to(monkeypatch, MANIFEST_FILE)

    with pytest.raises(OSError, match="simulated crash"):
        assign(store, 218386, base_rev=1, condition="CA")
    monkeypatch.undo()

    catalog, problems = store.load_report()
    assert catalog.experiment.title == "Cu in THF"
    assert catalog.runs[RunKey(218386)].condition == "OCV"
    assert any("interrupted" in p.message for p in problems)


def test_an_edit_undone_by_recovery_does_not_come_back_with_the_next_save(
    store, monkeypatch
) -> None:
    """One request, one answer -- even across a crash."""
    populated(store)
    # The first experiment edit, sent with a run edit, and interrupted after its
    # table was written: recovery shows neither.
    _crash_on_write_to(monkeypatch, MANIFEST_FILE)
    with pytest.raises(OSError, match="simulated crash"):
        store.update(
            runs=[RunChange(RunKey(218386), 1, {"condition": "CA"})],
            experiment=ExperimentChange(0, {"title": "Cu in THF"}),
            now=NOW,
        )
    monkeypatch.undo()
    catalog, _ = store.load_report()
    assert (catalog.runs[RunKey(218386)].condition, catalog.experiment.rev) == (
        "OCV",
        0,
    )

    assign(store, 218393, sample_id="Sample6")

    catalog, problems = store.load_report()
    assert catalog.experiment.rev == 0 and problems == ()
    assert not (store.directory / EXPERIMENT_FILE).exists()


def test_a_first_context_interrupted_alone_is_kept_and_said(store, monkeypatch) -> None:
    """One record, all of it written: kept as the edit made, with a note to check it."""
    populated(store)
    _crash_on_write_to(monkeypatch, MANIFEST_FILE)

    with pytest.raises(OSError, match="simulated crash"):
        describe(store, title="Cu in THF")
    monkeypatch.undo()

    catalog, problems = store.load_report()
    assert catalog.experiment.title == "Cu in THF"
    assert any("not in the manifest" in p.message for p in problems)


def test_a_listed_experiment_table_that_never_arrived_says_to_commit_it(
    store,
) -> None:
    """Committed with `git commit -a`, the manifest went and the new file did not."""
    populated(store)
    describe(store, title="Cu in THF")
    (store.directory / EXPERIMENT_FILE).unlink()

    with pytest.raises(CatalogCorruptError, match="git add experiment/experiment"):
        store.load()


# --------------------------------------------------------------------------
# Git
# --------------------------------------------------------------------------


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        env={
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.com",
            "HOME": str(root),
            "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
        },
    )


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_an_unmerged_catalog_is_refused_for_reading_and_writing(tmp_path: Path) -> None:
    """A binary conflict leaves "ours" in place with no markers at all."""
    root = tmp_path / "project"
    root.mkdir()
    store = ParquetCatalogStore(root / "experiment", tmp_path / "cache", git_root=root)
    _git(root, "init", "-q", "-b", "main")
    assign(store, 218386, sample_id="Sample6")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    _git(root, "checkout", "-qb", "other")
    assign(store, 218386, base_rev=1, condition="CA")
    _git(root, "commit", "-qam", "other")
    _git(root, "checkout", "-q", "main")
    assign(store, 218386, base_rev=1, condition="OCV")
    _git(root, "commit", "-qam", "main")
    subprocess.run(
        ["git", "merge", "other"], cwd=root, capture_output=True, check=False
    )

    with pytest.raises(CatalogUnmergedError, match="unmerged"):
        store.load()
    with pytest.raises(CatalogUnmergedError):
        assign(store, 218393, sample_id="Sample6")


# --------------------------------------------------------------------------
# Import cost
# --------------------------------------------------------------------------


def test_importing_the_store_does_not_import_pyarrow() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, nr_workbench.experiment.store; "
            "assert 'pyarrow' not in sys.modules; print('clean')",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
