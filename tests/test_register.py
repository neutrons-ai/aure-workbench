"""`sample.yaml` as a register you can curate, not a write-only report.

`nrw sample scan` writes it from the disk, but a beamtime directory routinely
holds alignment scans, aborted runs and other conditions that belong to the
sample without belonging to a particular model. Editing the register is the
supported way to say "co-refine only these" -- and that only works if
something reads it back.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import yaml

from nr_workbench.project.scan import load_register, register_drift, scan_sample


def write_run(steady: Path, run: int, segments: int = 2) -> None:
    """Write per-angle files for one run."""
    q = np.linspace(0.01, 0.2, 20)
    r = 1e-3 * (0.01 / q) ** 4
    body = "\n".join(
        f"{a:.6e} {b:.6e} {c:.6e} {d:.6e}"
        for a, b, c, d in zip(q, r, 0.05 * r, 0.02 * q, strict=True)
    )
    for segment in range(1, segments + 1):
        (steady / f"REFL_{run}_{segment}_{run + segment - 1}_partial.txt").write_text(
            body
        )


@pytest.fixture
def sample(project: Path) -> Path:
    """A sample with three steady runs on disk and a scanned register."""
    steady = project / "samples" / "Sample1" / "data" / "steady"
    steady.mkdir(parents=True, exist_ok=True)
    for run in (100001, 100010, 100020):
        write_run(steady, run)

    found = scan_sample(project, "Sample1")
    (project / "samples" / "Sample1" / "sample.yaml").write_text(
        yaml.safe_dump(found.as_dict(title="Sample1"), sort_keys=False),
        encoding="utf-8",
    )
    return project


def test_the_register_round_trips_what_the_scan_found(sample: Path) -> None:
    """Reading it back must give the same measurements the scan wrote."""
    scanned = scan_sample(sample, "Sample1")
    registered = load_register(sample, "Sample1")

    assert registered is not None
    assert set(registered.steady) == set(scanned.steady)
    assert registered.steady[100001].partials == scanned.steady[100001].partials


def test_a_curated_register_drops_a_run(sample: Path) -> None:
    """The point of the whole exercise: co-refine a subset."""
    path = sample / "samples" / "Sample1" / "sample.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["steady"] = [s for s in document["steady"] if s["run"] != 100010]
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")

    registered = load_register(sample, "Sample1")

    assert sorted(registered.steady) == [100001, 100020]
    assert 100010 in scan_sample(sample, "Sample1").steady, "still on disk"


def test_drift_reports_data_the_register_does_not_list(sample: Path) -> None:
    """A stale register and a curated one look the same on disk.

    So this reports rather than resolves -- only the scientist knows which it
    is, and silently re-adding the run would undo a deliberate choice.
    """
    write_run(sample / "samples" / "Sample1" / "data" / "steady", 100030)

    unregistered, missing = register_drift(sample, "Sample1")

    assert unregistered == [100030]
    assert missing == []


def test_drift_reports_registered_runs_with_no_data(sample: Path) -> None:
    path = sample / "samples" / "Sample1" / "sample.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["steady"].append({"run": 999999, "segments": ["nowhere.txt"]})
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")

    unregistered, missing = register_drift(sample, "Sample1")

    assert missing == [999999]
    assert unregistered == []


def test_the_scaffolded_stub_is_not_a_curated_empty_set(project: Path) -> None:
    """`nrw sample new` writes `steady: []`, and that must not mean "nothing".

    Otherwise every sample whose owner copied data in without running
    `nrw sample scan` first would report "no data found" from `nrw model new`.
    """
    stub = project / "samples" / "Sample1" / "sample.yaml"

    assert stub.is_file(), "the scaffold writes one"
    assert "steady: []" in stub.read_text(encoding="utf-8")
    assert load_register(project, "Sample1") is None


def test_no_register_at_all_falls_back_to_scanning(project: Path) -> None:
    (project / "samples" / "Sample1" / "sample.yaml").unlink()

    assert load_register(project, "Sample1") is None


def test_a_malformed_register_falls_back_rather_than_failing(sample: Path) -> None:
    """A half-edited YAML must not block `nrw model new`."""
    (sample / "samples" / "Sample1" / "sample.yaml").write_text(
        "steady: [unclosed", encoding="utf-8"
    )

    assert load_register(sample, "Sample1") is None


def test_model_new_uses_the_register_not_the_disk(
    sample: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The behaviour the register exists for, through the command."""
    from click.testing import CliRunner

    from nr_workbench.cli import main

    path = sample / "samples" / "Sample1" / "sample.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["steady"] = [s for s in document["steady"] if s["run"] == 100001]
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    monkeypatch.chdir(sample)

    result = CliRunner().invoke(main, ["model", "new", "Sample1", "--name", "m"])

    assert result.exit_code == 0, result.output
    spec = yaml.safe_load(
        (sample / "samples/Sample1/models/m.yaml").read_text(encoding="utf-8")
    )
    assert [state["run"] for state in spec["states"]] == [100001]
    assert "does not list run(s) 100010, 100020" in result.output
