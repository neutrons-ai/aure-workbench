from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from nr_workbench.cli import main
from nr_workbench.experiment.model import RunChange, RunKey, SampleChange
from nr_workbench.experiment.render import plan_sample, project_context
from nr_workbench.experiment.store import ParquetCatalogStore
from nr_workbench.project.scaffold import apply_scaffold


def init_project(tmp_path: Path) -> Path:
    root = tmp_path / "analysis"
    result = CliRunner().invoke(main, ["init", str(root)])
    assert result.exit_code == 0, result.output
    return root


def add_catalog_sample(root: Path, sample: str = "S6") -> None:
    ParquetCatalogStore.for_project(root).update(
        samples=[SampleChange(sample, 0, {"title": "Sample 6", "description": ""})]
    )


def make_sample_dir(root: Path, sample: str = "S6") -> None:
    catalog = ParquetCatalogStore.for_project(root).load()
    apply_scaffold(root, plan_sample(root, project_context(root), sample, catalog=catalog))


def test_remove_deletes_a_scaffold_only_directory(tmp_path: Path) -> None:
    root = init_project(tmp_path)
    add_catalog_sample(root)
    make_sample_dir(root)

    result = CliRunner().invoke(
        main,
        [
            "experiment",
            "remove",
            "S6",
            "--delete-dir",
            "--write",
            "--yes",
            "--root",
            str(root),
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    assert "deleted samples/S6/" in result.output
    assert not (root / "samples" / "S6").exists()
    assert ParquetCatalogStore.for_project(root).load().sample_ids() == []


def test_remove_keeps_a_sample_directory_with_real_content(tmp_path: Path) -> None:
    root = init_project(tmp_path)
    add_catalog_sample(root)
    make_sample_dir(root)
    model = root / "samples" / "S6" / "models" / "m.yaml"
    model.write_text("schema: nrw-model/1\n")

    result = CliRunner().invoke(
        main,
        ["experiment", "remove", "S6", "--write", "--yes", "--root", str(root)],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    assert "stays on disk as an unmanaged sample" in result.output
    assert model.is_file()
    assert ParquetCatalogStore.for_project(root).load().sample_ids() == []


def test_remove_refuses_to_delete_a_directory_with_real_content(tmp_path: Path) -> None:
    root = init_project(tmp_path)
    add_catalog_sample(root)
    make_sample_dir(root)
    model = root / "samples" / "S6" / "models" / "m.yaml"
    model.write_text("schema: nrw-model/1\n")

    result = CliRunner().invoke(
        main,
        [
            "experiment",
            "remove",
            "S6",
            "--delete-dir",
            "--write",
            "--yes",
            "--root",
            str(root),
        ],
        catch_exceptions=False,
    )

    assert result.exit_code != 0
    assert "Run again without --delete-dir" in result.output
    assert model.is_file()
    assert ParquetCatalogStore.for_project(root).load().sample_ids() == ["S6"]


def test_remove_refuses_a_sample_with_assigned_runs(tmp_path: Path) -> None:
    root = init_project(tmp_path)
    store = ParquetCatalogStore.for_project(root)
    store.update(
        runs=[RunChange(RunKey(218386), 0, {"sample_id": "S6"})],
        samples=[SampleChange("S6", 0, {"title": "Sample 6"})],
    )

    result = CliRunner().invoke(
        main, ["experiment", "remove", "S6", "--root", str(root)], catch_exceptions=False
    )

    assert result.exit_code != 0
    assert "still has runs assigned" in result.output
    assert store.load().sample_ids() == ["S6"]
