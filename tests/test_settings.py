"""Settings in nrw.toml: what nrw writes, and that `nrw init` agrees with it.

`nrw init` re-renders nrw.toml on every run and leaves a `.nrw-new` beside a
file it did not write. So everything nrw itself puts into nrw.toml -- the IPTS,
the audience, the experiment's settings -- has to be something `init` renders
back byte for byte, or every later `init` reads the project as hand-edited.
"""

from __future__ import annotations

import dataclasses
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
