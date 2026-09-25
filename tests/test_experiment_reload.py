"""The running server follows nrw.toml: a new setting takes effect without a restart.

The failures these guard against are quiet: a page that keeps showing the old
folder after a save, a poller that keeps listing the old folder because a
request still held it, or a reload that pairs the new source with the old
poller.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from nr_workbench.experiment.model import RunKey
from nr_workbench.project.settings import save
from nr_workbench.web.experiment import ExperimentData

from .experiment_fixtures import write_autoreduced


@pytest.fixture
def folders(tmp_path: Path) -> tuple[Path, Path]:
    """Two facility folders: one run in the first, a different run in the second."""
    first, second = tmp_path / "facility" / "a", tmp_path / "facility" / "b"
    write_autoreduced(first, 234277, [1, 2, 3], planned=3, mtime=time.time() - 3600)
    write_autoreduced(second, 234400, [1, 2, 3], planned=3, mtime=time.time() - 3600)
    return first, second


@pytest.fixture
def data(project: Path, folders):
    save(project, {"source.location": str(folders[0])})
    experiment = ExperimentData(project, autostart=False)
    yield experiment
    experiment.stop()


def runs_shown(data: ExperimentData) -> list[int]:
    data.live.scan_once()
    return [row["run"] for row in data.overview()["runs"]]


def test_a_changed_nrw_toml_is_picked_up_without_a_restart(
    data: ExperimentData, project: Path, folders
) -> None:
    assert runs_shown(data) == [234277]

    save(project, {"source.location": str(folders[1])})

    assert runs_shown(data) == [234400]
    assert data.overview()["source"]["path"] == str(folders[1])


def test_a_cursor_from_before_a_reload_resyncs(
    data: ExperimentData, project: Path, folders
) -> None:
    data.live.scan_once()
    cursor = data.changes(None)["cursor"]

    save(project, {"source.location": str(folders[1])})
    data.live.scan_once()

    assert data.changes(cursor)["resync"] is True


def test_a_request_holding_the_old_poller_does_not_restart_it(
    project: Path, folders
) -> None:
    """A retired poller left running would list the old folder for ten minutes."""
    save(project, {"source.location": str(folders[0])})
    data = ExperimentData(project, autostart=True)
    try:
        old = data.live
        save(project, {"source.location": str(folders[1])})
        data.overview()

        old.changes(None)

        # Not "started and stopped at once": no thread at all, for a retired
        # poller whoever asks it.
        assert old._thread is None
        assert data.live is not old
    finally:
        data.stop()


def test_the_source_and_the_poller_come_from_one_configuration(
    data: ExperimentData, project: Path, folders
) -> None:
    def consistent() -> bool:
        wiring = data._wired()
        return wiring.live.source is wiring.workspace.source is wiring.source._source

    assert consistent()
    save(project, {"source.location": str(folders[1])})
    assert consistent()


def test_a_touch_without_a_change_keeps_the_same_poller(
    data: ExperimentData, project: Path
) -> None:
    before = data.live
    toml = project / "nrw.toml"
    toml.write_bytes(toml.read_bytes())

    assert data.live is before


def test_an_unparseable_edit_keeps_the_last_good_configuration(
    data: ExperimentData, project: Path, folders
) -> None:
    assert runs_shown(data) == [234277]
    toml = project / "nrw.toml"
    toml.write_text(toml.read_text(encoding="utf-8") + "\n[half typed\n")

    overview = data.overview()

    assert overview["source"]["path"] == str(folders[0])
    assert any("Still using" in p["message"] for p in overview["problems"])

    save_text = toml.read_text(encoding="utf-8").replace("\n[half typed\n", "")
    toml.write_text(save_text, encoding="utf-8")
    assert not any("Still using" in p["message"] for p in data.overview()["problems"])


def test_an_apply_reviewed_against_another_source_is_refused() -> None:
    """The plan id names the source, not only the files it lists."""
    from nr_workbench.experiment.apply import plan_apply
    from nr_workbench.experiment.model import Catalog, RunChange, apply_changes
    from nr_workbench.project.render import RenderContext

    from .experiment_fixtures import InMemorySource

    catalog = apply_changes(
        Catalog(),
        runs=[RunChange(RunKey(234277), 0, {"sample_id": "Sample6"})],
        now="2026-09-25T12:00:00Z",
    )
    context = RenderContext(project_name="p")

    def plan_id(source) -> str:
        return plan_apply(Path("/nonexistent"), catalog, {}, {}, source, context).plan_id

    here, there = InMemorySource(), InMemorySource()
    there.describe = lambda: {"kind": "memory", "location": "elsewhere", "path": None}

    assert plan_id(here) == plan_id(InMemorySource())
    assert plan_id(here) != plan_id(there)
