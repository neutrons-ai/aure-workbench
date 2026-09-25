"""Settings in nrw.toml: what nrw writes, and that `nrw init` agrees with it.

`nrw init` re-renders nrw.toml on every run and leaves a `.nrw-new` beside a
file it did not write. So everything nrw itself puts into nrw.toml -- the IPTS,
the audience, the experiment's settings -- has to be something `init` renders
back byte for byte, or every later `init` reads the project as hand-edited.
"""

from __future__ import annotations

import dataclasses
import os
import tomllib
from pathlib import Path

import pytest
from click.testing import CliRunner

from nr_workbench.cli import main
from nr_workbench.project.config import ProjectConfigError, load_config
from nr_workbench.project.render import init_context, render_tree
from nr_workbench.project.settings import (
    EXPERIMENT_KEYS,
    experiment_block,
    written_experiment,
)


def nrw(root: Path, monkeypatch, *args: str):
    monkeypatch.chdir(root)
    return CliRunner().invoke(main, list(args))


def rendered_nrw_toml(root: Path, **overrides) -> str:
    context = dataclasses.replace(init_context(root), **overrides)
    planned = {p.relpath: p for p in render_tree("project", context)}
    return planned["nrw.toml"].content.decode("utf-8")


# --------------------------------------------------------------------------
# Values that would break the file
# --------------------------------------------------------------------------


def test_a_quote_or_backslash_in_a_name_or_label_still_gives_valid_toml(
    tmp_path: Path,
) -> None:
    root = tmp_path / 'run "7" \\ april'
    root.mkdir()

    result = CliRunner().invoke(
        main, ["init", str(root), "--beamtime", 'the "april" beamtime \\ 2026']
    )

    assert result.exit_code == 0, result.output
    config = load_config(root)
    assert config.name == 'run "7" \\ april'
    assert config.beamtime == 'the "april" beamtime \\ 2026'


def test_a_table_declared_twice_names_the_fix(project: Path) -> None:
    toml = project / "nrw.toml"
    toml.write_text(
        toml.read_text(encoding="utf-8") + "\n[harness]\nkinds = []\n", encoding="utf-8"
    )

    with pytest.raises(ProjectConfigError, match="keep one"):
        load_config(project)


# --------------------------------------------------------------------------
# `nrw init` renders back what nrw wrote
# --------------------------------------------------------------------------


def test_the_experiment_block_is_commented_out_until_something_is_set() -> None:
    block = experiment_block({})

    assert tomllib.loads(block) == {}
    assert "# [experiment.source]" in block and "# [experiment.feed]" in block


def test_a_set_key_writes_its_table_out_and_keeps_the_rest_as_placeholders() -> None:
    block = experiment_block({"experiment.source": {"location": "/data/x"}})

    assert tomllib.loads(block) == {"experiment": {"source": {"location": "/data/x"}}}
    assert "# settle_seconds = 300" in block
    assert "# [experiment.feed]" in block


def test_every_managed_key_reads_back_as_written() -> None:
    chosen = {
        "experiment.source": {"kind": "local", "location": "/d", "settle_seconds": 60},
        "experiment.feed": {"kind": "directory", "poll_seconds": 15},
    }

    document = tomllib.loads(experiment_block(chosen))

    assert written_experiment(document) == chosen
    assert set(chosen) == set(EXPERIMENT_KEYS)


def test_init_twice_with_experiment_settings_reports_no_changes(
    project: Path, monkeypatch
) -> None:
    toml = project / "nrw.toml"
    toml.write_text(
        rendered_nrw_toml(
            project,
            experiment={
                "experiment.source": {"location": "/data/new_reduction"},
                "experiment.feed": {"poll_seconds": 15},
            },
        ),
        encoding="utf-8",
    )

    result = nrw(project, monkeypatch, "init", "--check")

    assert result.exit_code == 0, result.output
    assert load_config(project).raw["experiment"]["feed"] == {"poll_seconds": 15}


def test_nrw_audience_then_init_check_is_clean(project: Path, monkeypatch) -> None:
    """It used to leave nrw.toml looking hand-edited to every later `init`."""
    changed = nrw(project, monkeypatch, "audience", "--set", "statistics=expert")
    assert changed.exit_code == 0, changed.output

    result = nrw(project, monkeypatch, "init", "--check")

    assert result.exit_code == 0, result.output
    assert load_config(project).raw["audience"]["statistics"] == "expert"


def test_an_unedited_older_nrw_toml_upgrades_to_the_new_render(
    project: Path, monkeypatch
) -> None:
    """A file from before the experiment block, untouched since init wrote it."""
    from nr_workbench.project.scaffold import load_lock, sha256_bytes, write_lock

    toml = project / "nrw.toml"
    current = toml.read_text(encoding="utf-8")
    older = current[: current.index("# The experiment:")].rstrip("\n") + "\n"
    toml.write_text(older, encoding="utf-8")
    lock_path = project / ".nrw" / "scaffold.lock.json"
    lock = load_lock(lock_path)
    lock["nrw.toml"]["sha256_at_install"] = sha256_bytes(older.encode())
    write_lock(lock_path, lock)

    result = nrw(project, monkeypatch, "init")

    assert result.exit_code == 0, result.output
    assert toml.read_text(encoding="utf-8") == current
    assert not (project / "nrw.toml.nrw-new").exists()


# --------------------------------------------------------------------------
# Saving
# --------------------------------------------------------------------------


def init_check_is_clean(project: Path) -> bool:
    result = CliRunner().invoke(main, ["init", str(project), "--check"])
    return result.exit_code == 0


def test_after_any_sequence_of_saves_init_check_reports_nothing_to_do(
    project: Path,
) -> None:
    from nr_workbench.project.settings import save

    steps = [
        {"ipts": "34347"},
        {"source.location": "/data/{ipts}/new_reduction"},
        {"feed.poll_seconds": 15, "source.settle_seconds": 120},
        {"label": "april 2026"},
        {"source.location": None, "feed.poll_seconds": None},
    ]
    for step in steps:
        result = save(project, step)

        assert result.changed, step
        assert init_check_is_clean(project), step
    config = load_config(project)
    assert config.ipts == "IPTS-34347"
    assert config.raw["experiment"]["source"] == {"settle_seconds": 120}


def test_a_save_changes_no_byte_outside_the_managed_lines(project: Path) -> None:
    """A person's edits anywhere else survive, byte for byte."""
    from nr_workbench.project.settings import save

    toml = project / "nrw.toml"
    edited = toml.read_text(encoding="utf-8").replace(
        "[conventions]", "# our own note\n[conventions]"
    )
    toml.write_text(edited, encoding="utf-8")

    save(project, {"ipts": "IPTS-7", "source.location": "/data/x"})

    after = toml.read_text(encoding="utf-8")
    removed = [line for line in edited.splitlines() if line not in after.splitlines()]
    added = [line for line in after.splitlines() if line not in edited.splitlines()]
    assert removed == [
        'ipts = "IPTS-00001"',
        "# [experiment.source]",
        '# location = "/SNS/REF_L/{ipts}/shared/autoreduce/new_reduction"',
    ]
    assert added == ['ipts = "IPTS-7"', "[experiment.source]", 'location = "/data/x"']
    assert "# our own note" in after


def test_a_save_over_a_file_edited_since_it_was_read_is_a_conflict(
    project: Path,
) -> None:
    from nr_workbench.project.settings import read, save
    from nr_workbench.project.tomlfile import TomlConflictError

    shown = read(project)
    toml = project / "nrw.toml"
    toml.write_text(toml.read_text(encoding="utf-8") + "# edited\n", encoding="utf-8")
    before = toml.read_bytes()

    with pytest.raises(TomlConflictError):
        save(project, {"ipts": "IPTS-9"}, base_revision=shown.revision)

    assert toml.read_bytes() == before


def test_a_save_refreshes_the_lock_so_a_later_template_change_upgrades(
    project: Path,
) -> None:
    """Otherwise the next nrw upgrade finds the file "edited" and leaves a .nrw-new."""
    from nr_workbench.commands.init_cmd import plan_project_files
    from nr_workbench.project.scaffold import Outcome, apply_scaffold
    from nr_workbench.project.settings import save

    save(project, {"source.location": "/data/x"})
    planned = [
        dataclasses.replace(p, content=p.content + b"# a newer template\n")
        if p.relpath == "nrw.toml"
        else p
        for p in plan_project_files(init_context(project))
    ]

    report = apply_scaffold(project, planned)

    outcome = {f.relpath: f.outcome for f in report.files}["nrw.toml"]
    assert outcome is Outcome.UPGRADE
    assert "location" in load_config(project).raw["experiment"]["source"]


def test_unsetting_every_setting_returns_the_file_to_the_render(project: Path) -> None:
    from nr_workbench.project.settings import save

    toml = project / "nrw.toml"
    original = toml.read_text(encoding="utf-8")
    save(project, {"source.location": "/data/x", "feed.poll_seconds": 20})

    save(project, {"source.location": None, "feed.poll_seconds": None})

    assert toml.read_text(encoding="utf-8") == original


def test_a_save_keeps_the_previous_file(project: Path) -> None:
    from nr_workbench.project.settings import save

    before = (project / "nrw.toml").read_text(encoding="utf-8")

    result = save(project, {"ipts": "IPTS-5"})

    assert (project / result.backup).read_text(encoding="utf-8") == before
    assert result.backup.startswith(".nrw/backups/")


def test_a_preview_writes_nothing(project: Path) -> None:
    from nr_workbench.project.settings import save

    before = (project / "nrw.toml").read_bytes()

    result = save(project, {"ipts": "IPTS-5"}, write=False)

    assert result.changed and '+ipts = "IPTS-5"' in result.diff
    assert (project / "nrw.toml").read_bytes() == before


def test_a_save_on_a_damaged_lock_saves_and_leaves_the_lock(project: Path) -> None:
    from nr_workbench.project.settings import save

    lock = project / ".nrw" / "scaffold.lock.json"
    lock.write_text("{ not json", encoding="utf-8")

    result = save(project, {"ipts": "IPTS-5"})

    assert load_config(project).ipts == "IPTS-5"
    assert lock.read_text(encoding="utf-8") == "{ not json"
    assert any("scaffold lock" in note for note in result.notes)


def test_saving_the_ipts_refreshes_an_untouched_readme(project: Path) -> None:
    from nr_workbench.project.settings import save

    result = save(project, {"ipts": "IPTS-4242"})

    assert "README.md" in result.written
    assert "IPTS-4242" in (project / "README.md").read_text(encoding="utf-8")
    assert init_check_is_clean(project)


def test_a_hand_edited_readme_is_left_alone_and_said_so(project: Path) -> None:
    from nr_workbench.project.settings import save

    readme = project / "README.md"
    readme.write_text(readme.read_text(encoding="utf-8") + "\nOur notes.\n")

    result = save(project, {"ipts": "IPTS-4242"})

    assert "README.md" not in result.written
    assert readme.read_text(encoding="utf-8").endswith("Our notes.\n")
    assert any("README.md" in note for note in result.notes)


def test_a_file_from_before_the_experiment_block_gets_nrws_block(project: Path) -> None:
    from nr_workbench.project.settings import save

    toml = project / "nrw.toml"
    text = toml.read_text(encoding="utf-8")
    older = text[: text.index("# The experiment:")] + "# our own note\n"
    toml.write_text(older, encoding="utf-8")

    save(project, {"feed.poll_seconds": 10})

    after = toml.read_text(encoding="utf-8")
    assert after.startswith(older)
    assert "[experiment.feed]\n# kind = \"directory\"\npoll_seconds = 10\n" in after
    assert "# [experiment.source]" in after


def test_a_shape_nrw_does_not_edit_is_refused_with_the_lines_to_add(
    project: Path,
) -> None:
    from nr_workbench.project.settings import save
    from nr_workbench.project.tomlfile import TomlEditError

    toml = project / "nrw.toml"
    text = toml.read_text(encoding="utf-8")
    text = text[: text.index("# The experiment:")]
    text += '[experiment]\nsource = { location = "/data/old" }\n'
    toml.write_text(text, encoding="utf-8")

    with pytest.raises(TomlEditError) as refused:
        save(project, {"source.location": "/data/new"})

    assert 'location = "/data/new"' in refused.value.lines
    assert toml.read_text(encoding="utf-8") == text


def test_changing_the_ipts_of_a_catalogued_experiment_needs_confirming(
    project: Path,
) -> None:
    from nr_workbench.project.settings import NeedsConfirmation, save

    with pytest.raises(NeedsConfirmation) as asked:
        save(project, {"ipts": "IPTS-2"}, catalogued_runs=3)

    assert asked.value.needs == "ipts-change"
    assert "3 run(s)" in str(asked.value)
    assert load_config(project).ipts == "IPTS-00001"
    save(project, {"ipts": "IPTS-2"}, catalogued_runs=3, confirmed={"ipts-change"})
    assert load_config(project).ipts == "IPTS-2"


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0, reason="POSIX, not root")
def test_a_read_only_project_is_refused_cleanly(project: Path) -> None:
    from nr_workbench.project.settings import save

    before = (project / "nrw.toml").read_bytes()
    folders = [project, project / ".nrw", project / ".nrw" / "cache"]
    for folder in folders:
        folder.mkdir(exist_ok=True)
        folder.chmod(0o555)
    try:
        with pytest.raises(OSError):
            save(project, {"ipts": "IPTS-5"})
    finally:
        for folder in folders:
            folder.chmod(0o755)

    assert (project / "nrw.toml").read_bytes() == before


# --------------------------------------------------------------------------
# What a setting may be
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [("34347", "IPTS-34347"), ("ipts-00001", "IPTS-00001"), ("", ""), (None, "")],
)
def test_an_ipts_is_saved_in_one_form(project: Path, value, expected: str) -> None:
    from nr_workbench.project.settings import validate

    edits, _ = validate(project, {"ipts": value})

    assert edits["beamtime"]["ipts"].value == expected


@pytest.mark.parametrize(
    "name,value,message",
    [
        ("ipts", "IPTS-3x", "not an IPTS number"),
        ("ipts", 34347, "must be text"),
        ("label", "two\nlines", "one line"),
        ("label", "x" * 101, "longer than"),
        ("source.location", "data/new_reduction", "not a full path"),
        ("source.location", "~/data", "full path"),
        ("source.location", "/data/\x00x", "one line"),
        ("source.location", "/" + "d" * 1024, "longer than"),
        ("source.location", "", "empty"),
        ("source.settle_seconds", 5, "from 10 to 3600"),
        ("source.settle_seconds", 3601, "from 10 to 3600"),
        ("source.settle_seconds", True, "number of seconds"),
        ("feed.poll_seconds", float("nan"), "number of seconds"),
        ("feed.poll_seconds", "30", "number of seconds"),
        ("feed.kind", "monitor", "not available yet"),
        ("source.kind", "tiled", "not available yet"),
        ("source.kind", "s3", "not known"),
        ("colour", "blue", "not a setting"),
    ],
)
def test_a_value_that_is_not_allowed_is_refused_with_the_reason(
    project: Path, name: str, value, message: str
) -> None:
    from nr_workbench.project.settings import SettingsError, validate

    with pytest.raises(SettingsError, match=message):
        validate(project, {name: value})


@pytest.mark.parametrize("inside", ["samples", ".nrw", "experiment"])
def test_a_data_location_inside_the_project_is_refused(project: Path, inside: str) -> None:
    from nr_workbench.project.settings import SettingsError, validate

    with pytest.raises(SettingsError, match=f"inside this project's {inside}/"):
        validate(project, {"source.location": str(project / inside / "x")})


def test_a_data_location_in_the_home_directory_is_allowed_with_a_warning(
    project: Path,
) -> None:
    from nr_workbench.project.settings import validate

    edits, warnings = validate(
        project, {"source.location": str(Path.home() / "beamtime" / "data")}
    )

    assert edits["experiment.source"]["location"].value.endswith("beamtime/data")
    assert any("home directory" in w for w in warnings)


def test_a_location_template_keeps_its_placeholder(project: Path) -> None:
    from nr_workbench.project.settings import validate

    edits, _ = validate(project, {"source.location": "/SNS/REF_L/{ipts}/x"})

    assert edits["experiment.source"]["location"].value == "/SNS/REF_L/{ipts}/x"


def test_choosing_the_default_kind_follows_the_default(project: Path) -> None:
    from nr_workbench.project.settings import validate
    from nr_workbench.project.tomlfile import Unset

    edits, _ = validate(project, {"source.kind": "local", "feed.kind": "directory"})

    assert isinstance(edits["experiment.source"]["kind"], Unset)
    assert isinstance(edits["experiment.feed"]["kind"], Unset)


def test_needs_setup_is_judged_without_the_data_mount(project: Path) -> None:
    from nr_workbench.experiment.config import experiment_config
    from nr_workbench.project.settings import save

    save(project, {"ipts": ""})
    assert experiment_config(load_config(project)).needs_setup

    save(project, {"source.location": "/data/without/ipts"})
    assert not experiment_config(load_config(project)).needs_setup


def test_a_save_never_writes_an_edit_its_proof_rejects(project: Path, monkeypatch) -> None:
    """Whatever the block rewrite gets wrong, the proof after it stops the write."""
    from nr_workbench.project import tomlfile
    from nr_workbench.project.settings import save

    toml = project / "nrw.toml"
    toml.write_text(
        toml.read_text(encoding="utf-8") + "# hand-edited, so the edit path runs\n",
        encoding="utf-8",
    )
    before = toml.read_bytes()
    real = tomlfile.replace_block

    def careless(text, old, new):
        replaced = real(text, old, new)
        return replaced.replace('label = "june2026"', 'label = "oops"')

    monkeypatch.setattr(tomlfile, "replace_block", careless)

    with pytest.raises(tomlfile.TomlEditError):
        save(project, {"source.location": "/data/x"})

    assert toml.read_bytes() == before


def test_a_save_brings_an_unedited_older_file_up_to_date(project: Path) -> None:
    """What `nrw init` would do anyway, with the new value in -- and said so."""
    from nr_workbench.project.scaffold import load_lock, sha256_bytes, write_lock
    from nr_workbench.project.settings import save

    toml = project / "nrw.toml"
    current = toml.read_text(encoding="utf-8")
    older = current[: current.index("# The experiment:")].rstrip("\n") + "\n"
    toml.write_text(older, encoding="utf-8")
    lock_path = project / ".nrw" / "scaffold.lock.json"
    lock = load_lock(lock_path)
    lock["nrw.toml"]["sha256_at_install"] = sha256_bytes(older.encode())
    write_lock(lock_path, lock)

    result = save(project, {"feed.poll_seconds": 10})

    assert toml.read_text(encoding="utf-8") == rendered_nrw_toml(project)
    assert load_config(project).raw["experiment"]["feed"] == {"poll_seconds": 10}
    assert any("brought up to date" in note for note in result.notes)
    assert init_check_is_clean(project)
