"""The runs a project's samples hold in their own data/ folders."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from nr_workbench.experiment.held import HeaderCache, held_runs, held_version
from nr_workbench.experiment.model import SERIES, STEADY, RunKey

from . import tnr_fixture
from .experiment_fixtures import write_autoreduced


def add_sample(project: Path, sample_id: str) -> Path:
    """A sample folder with a sample.md, which is what makes it a sample."""
    folder = project / "samples" / sample_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "sample.md").write_text(f"# {sample_id}\n", encoding="utf-8")
    return folder


@pytest.fixture
def filled(project: Path) -> Path:
    """Sample1 filled by hand: two steady runs and a flat tNR series."""
    data = project / "samples" / "Sample1" / "data"
    write_autoreduced(data / "steady", 234277, [1, 2, 3])
    write_autoreduced(data / "steady", 234280, [1, 2])
    tnr_fixture.build(data / "tnr")
    return project


def test_held_runs_lists_steady_runs_with_their_angles_and_titles(
    filled: Path,
) -> None:
    held = held_runs(filled, headers=HeaderCache())

    run = held[RunKey(234277, STEADY)]
    assert run.sample_ids == ("Sample1",)
    assert run.segments == (1, 2, 3)
    assert run.thetas == (0.45, 1.251, 3.5)
    assert run.title.startswith("S1_air-234277")
    assert run.folder == "samples/Sample1/data/steady/"
    assert all(f.startswith("samples/Sample1/data/steady/") for f in run.files)
    assert held[RunKey(234280, STEADY)].segments == (1, 2)


def test_held_runs_lists_a_flat_series_by_its_run(filled: Path) -> None:
    held = held_runs(filled, headers=HeaderCache())

    series = held[RunKey(tnr_fixture.RUN_NUMBER, SERIES)]
    assert series.files == ("samples/Sample1/data/tnr",)
    assert series.folder == "samples/Sample1/data/tnr/"
    assert series.n_slices > 1
    assert series.reduction_json is not None


def test_held_runs_lists_a_series_in_its_own_folder(project: Path) -> None:
    tnr_fixture.build(project / "samples" / "Sample1" / "data" / "tnr" / "x")

    held = held_runs(project, headers=HeaderCache())

    assert held[RunKey(tnr_fixture.RUN_NUMBER, SERIES)].files == (
        "samples/Sample1/data/tnr/x",
    )


def test_held_runs_names_every_sample_that_holds_a_run(filled: Path) -> None:
    other = add_sample(filled, "Sample2")
    write_autoreduced(other / "data" / "steady", 234277, [1])

    held = held_runs(filled, headers=HeaderCache())

    run = held[RunKey(234277, STEADY)]
    assert run.sample_ids == ("Sample1", "Sample2")
    assert run.segments == (1, 2, 3)  # the first sample's files


def test_held_runs_skips_a_series_whose_run_is_unknown(project: Path) -> None:
    tnr = project / "samples" / "Sample1" / "data" / "tnr"
    tnr.mkdir(parents=True, exist_ok=True)
    for name in ("r1_a.txt", "r2_b.txt"):  # two runs: no one run number
        (tnr / name).write_text("0.01 1 0.1\n", encoding="utf-8")

    assert held_runs(project, headers=HeaderCache()) == {}


def test_held_runs_of_a_project_with_no_data_is_empty(project: Path) -> None:
    assert held_runs(project, headers=HeaderCache()) == {}


def test_header_cache_reads_a_file_once_until_it_changes(
    filled: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import nr_workbench.experiment.held as held_module

    reads: list[Path] = []
    real = held_module.read_header

    def counting(path: Path, **kwargs):
        reads.append(path)
        return real(path, **kwargs)

    monkeypatch.setattr(held_module, "read_header", counting)
    cache = HeaderCache()
    path = next((filled / "samples" / "Sample1" / "data" / "steady").glob("*.dat"))

    cache.get(path)
    cache.get(path)
    os.utime(path, ns=(1, path.stat().st_mtime_ns + 1_000_000_000))
    cache.get(path)

    assert reads == [path, path]


def test_header_cache_gives_none_for_an_unreadable_header(tmp_path: Path) -> None:
    path = tmp_path / "REFL_1_1_1_autoreduction.dat"
    path.write_text("# Meta:{not json\n0.01 1 0.1 0.001\n", encoding="utf-8")

    assert HeaderCache().get(path) is None


def test_held_version_changes_when_a_sample_gains_a_run(filled: Path) -> None:
    cache = HeaderCache()
    before = held_version(held_runs(filled, headers=cache))

    steady = filled / "samples" / "Sample1" / "data" / "steady"
    write_autoreduced(steady, 234283, [1])

    assert held_version(held_runs(filled, headers=cache)) != before
    assert held_version(held_runs(filled, headers=cache)) == held_version(
        held_runs(filled, headers=cache)
    )
