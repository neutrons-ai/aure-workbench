"""Tests for the model lifecycle: scan, new, fork, check drift, diff.

These close the loop between "the docs promise this command" and "the command
exists and does what the docs say" -- three of them were cited in error
messages and skills before they were written.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from nr_workbench.cli import main

pytestmark = pytest.mark.integration


def write_partials(directory: Path, run: int, segments: int = 3) -> None:
    """Write plausible REF_L partial files for one run."""
    directory.mkdir(parents=True, exist_ok=True)
    for segment in range(1, segments + 1):
        rows = "\n".join(
            f"{0.01 + 0.001 * i:.6f} {1e-3 / (i + 1):.6e} {1e-4:.6e} {2e-4:.6e}"
            for i in range(30)
        )
        (
            directory / f"REFL_{run}_{segment}_{run + segment - 1}_partial.txt"
        ).write_text(rows + "\n", encoding="utf-8")


def write_slices(directory: Path, run: int, count: int = 6, step: int = 240) -> None:
    """Write time-binned tNR slices."""
    directory.mkdir(parents=True, exist_ok=True)
    for i in range(count):
        rows = "\n".join(
            f"{0.01 + 0.001 * j:.6f} {1e-3 / (j + 1):.6e} {1e-4:.6e} {2e-4:.6e}"
            for j in range(30)
        )
        (directory / f"r{run}_t{i * step:06d}.txt").write_text(
            rows + "\n", encoding="utf-8"
        )


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A project with one sample carrying two steady runs and a tNR series."""
    root = tmp_path / "proj"
    result = CliRunner().invoke(main, ["init", str(root), "--sample", "S1"])
    assert result.exit_code == 0, result.output

    data = root / "samples" / "S1" / "data"
    write_partials(data / "steady", 100001)
    write_partials(data / "steady", 100005)
    write_slices(data / "tnr" / "100003", 100003)
    return root


def run(root: Path, monkeypatch: pytest.MonkeyPatch, *args: str):
    """Invoke the CLI with the project as the working directory."""
    monkeypatch.chdir(root)
    return CliRunner().invoke(main, list(args))


# --------------------------------------------------------------------------
# sample scan
# --------------------------------------------------------------------------


def test_scan_registers_what_is_on_disk(project: Path, monkeypatch) -> None:
    result = run(project, monkeypatch, "sample", "scan", "S1", "--json")

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)[0]
    assert payload["steady_runs"] == [100001, 100005]
    assert len(payload["series"]) == 1
    assert payload["series"][0]["n_slices"] == 6


def test_scan_writes_sample_yaml(project: Path, monkeypatch) -> None:
    import yaml

    run(project, monkeypatch, "sample", "scan", "S1")

    document = yaml.safe_load((project / "samples" / "S1" / "sample.yaml").read_text())
    assert document["schema"] == "nrw-sample/1"
    assert [entry["run"] for entry in document["steady"]] == [100001, 100005]


def test_scan_detects_a_uniform_time_step(project: Path, monkeypatch) -> None:
    """Knowing the step is what lets `model new` write a `select` block."""
    result = run(project, monkeypatch, "sample", "scan", "S1", "--json")

    series = json.loads(result.stdout)[0]["series"][0]
    assert series["t_step"] == 240.0
    assert series["t_start"] == 0.0


def test_scan_reports_data_the_prose_does_not_mention(
    project: Path, monkeypatch
) -> None:
    """The gap between sample.md and the disk is usually the interesting part."""
    result = run(project, monkeypatch, "sample", "scan", "S1", "--json")

    payload = json.loads(result.stdout)[0]
    assert set(payload["present_but_undocumented"]) == {100001, 100003, 100005}


def test_scan_reports_documented_runs_with_no_data(project: Path, monkeypatch) -> None:
    """A run written up but absent is usually one that failed."""
    sample_md = project / "samples" / "S1" / "sample.md"
    sample_md.write_text(
        sample_md.read_text(encoding="utf-8") + "\n| 100001 | full Q | OCV |\n"
        "| 999999 | full Q | never arrived |\n",
        encoding="utf-8",
    )

    result = run(project, monkeypatch, "sample", "scan", "S1", "--json")

    payload = json.loads(result.stdout)[0]
    assert payload["documented_but_absent"] == [999999]


def test_scan_ignores_runs_in_the_templates_commented_example(
    project: Path, monkeypatch
) -> None:
    """The shipped sample.md has a worked example inside an HTML comment.

    Counting those would report every new sample as documenting runs it has
    never had.
    """
    result = run(project, monkeypatch, "sample", "scan", "S1", "--json")

    assert json.loads(result.stdout)[0]["documented_but_absent"] == []


def test_scan_with_no_write_leaves_the_register_alone(
    project: Path, monkeypatch
) -> None:
    register = project / "samples" / "S1" / "sample.yaml"
    before = register.read_text(encoding="utf-8")

    run(project, monkeypatch, "sample", "scan", "S1", "--no-write")

    assert register.read_text(encoding="utf-8") == before


# --------------------------------------------------------------------------
# model new
# --------------------------------------------------------------------------


def test_model_new_produces_a_spec_that_validates(project: Path, monkeypatch) -> None:
    """A scaffold that fails on first contact teaches the pattern backwards."""
    created = run(project, monkeypatch, "model", "new", "S1", "--name", "m")
    assert created.exit_code == 0, created.output

    checked = run(project, monkeypatch, "model", "validate", "samples/S1/models/m.yaml")

    assert checked.exit_code == 0, checked.output


def test_model_new_produces_a_spec_that_generates(project: Path, monkeypatch) -> None:
    run(project, monkeypatch, "model", "new", "S1", "--name", "m")

    generated = run(
        project, monkeypatch, "model", "generate", "samples/S1/models/m.yaml"
    )

    assert generated.exit_code == 0, generated.output
    assert (project / "samples" / "S1" / "models" / "m.py").is_file()


def test_model_new_picks_up_both_states_and_the_series(
    project: Path, monkeypatch
) -> None:
    import yaml

    run(project, monkeypatch, "model", "new", "S1", "--name", "m")

    spec = yaml.safe_load(
        (project / "samples" / "S1" / "models" / "m.yaml").read_text()
    )
    assert len(spec["states"]) == 2
    assert len(spec["series"]) == 1
    assert spec["constraints"][0]["form"] == "linear_in_time"


def test_model_new_refuses_to_clobber(project: Path, monkeypatch) -> None:
    run(project, monkeypatch, "model", "new", "S1", "--name", "m")

    again = run(project, monkeypatch, "model", "new", "S1", "--name", "m")

    assert again.exit_code != 0
    assert "already exists" in again.output


def test_model_new_on_a_sample_with_no_data_says_so(project: Path, monkeypatch) -> None:
    run(project, monkeypatch, "sample", "new", "Empty")

    result = run(project, monkeypatch, "model", "new", "Empty", "--name", "m")

    assert result.exit_code != 0
    assert "No data found" in result.output


# --------------------------------------------------------------------------
# model fork
# --------------------------------------------------------------------------


@pytest.fixture
def generated(project: Path, monkeypatch) -> Path:
    """A project with a generated script."""
    run(project, monkeypatch, "model", "new", "S1", "--name", "m")
    result = run(project, monkeypatch, "model", "generate", "samples/S1/models/m.yaml")
    assert result.exit_code == 0, result.output
    return project


def test_fork_creates_a_hand_owned_copy(generated: Path, monkeypatch) -> None:
    result = run(
        generated,
        monkeypatch,
        "model",
        "fork",
        "samples/S1/models/m.yaml",
        "--name",
        "mine",
    )

    assert result.exit_code == 0, result.output
    fork = generated / "samples" / "S1" / "models" / "mine.py"
    assert fork.is_file()
    source = fork.read_text(encoding="utf-8")
    assert "HAND-OWNED SCRIPT" in source
    assert "GENERATED BY nr-workbench" not in source


def test_fork_records_where_it_came_from(generated: Path, monkeypatch) -> None:
    """Escaping the generator must not mean escaping provenance."""
    run(
        generated,
        monkeypatch,
        "model",
        "fork",
        "samples/S1/models/m.yaml",
        "--name",
        "mine",
    )

    source = (generated / "samples" / "S1" / "models" / "mine.py").read_text(
        encoding="utf-8"
    )

    assert "forked from: samples/S1/models/m.yaml" in source
    assert "spec sha256:" in source


def test_a_fork_still_runs(generated: Path, monkeypatch) -> None:
    """A fork that does not execute would be a trap, not an escape hatch."""
    import subprocess
    import sys

    run(
        generated,
        monkeypatch,
        "model",
        "fork",
        "samples/S1/models/m.yaml",
        "--name",
        "mine",
    )
    fork = generated / "samples" / "S1" / "models" / "mine.py"

    result = subprocess.run(
        [sys.executable, str(fork)], capture_output=True, text=True, timeout=600
    )

    assert result.returncode == 0, result.stderr[-1500:]


def test_fork_requires_a_generated_script_first(project: Path, monkeypatch) -> None:
    run(project, monkeypatch, "model", "new", "S1", "--name", "m")

    result = run(project, monkeypatch, "model", "fork", "samples/S1/models/m.yaml")

    assert result.exit_code != 0
    assert "model generate" in result.output


def test_fork_will_not_overwrite(generated: Path, monkeypatch) -> None:
    run(
        generated,
        monkeypatch,
        "model",
        "fork",
        "samples/S1/models/m.yaml",
        "--name",
        "mine",
    )

    again = run(
        generated,
        monkeypatch,
        "model",
        "fork",
        "samples/S1/models/m.yaml",
        "--name",
        "mine",
    )

    assert again.exit_code != 0


# --------------------------------------------------------------------------
# check: script drift
# --------------------------------------------------------------------------


def test_check_passes_on_a_freshly_generated_script(
    generated: Path, monkeypatch
) -> None:
    result = run(generated, monkeypatch, "check")

    assert result.exit_code == 0, result.output


def test_check_detects_a_hand_edited_script(generated: Path, monkeypatch) -> None:
    """The unsupported way to take control; `nrw model fork` is the supported one."""
    script = generated / "samples" / "S1" / "models" / "m.py"
    script.write_text(
        script.read_text(encoding="utf-8") + "\n# sneaky\n", encoding="utf-8"
    )

    result = run(generated, monkeypatch, "check", "--json")

    assert result.exit_code == 1
    kinds = {p["kind"] for p in json.loads(result.stdout)["problems"]}
    assert "hand-edited-script" in kinds


def test_check_detects_a_script_older_than_its_spec(
    generated: Path, monkeypatch
) -> None:
    spec = generated / "samples" / "S1" / "models" / "m.yaml"
    spec.write_text(
        spec.read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8"
    )

    result = run(generated, monkeypatch, "check", "--json")

    assert result.exit_code == 1
    kinds = {p["kind"] for p in json.loads(result.stdout)["problems"]}
    assert "stale-script" in kinds


def test_check_leaves_forks_alone(generated: Path, monkeypatch) -> None:
    """A fork is hand-owned by design; flagging it would make `check` noise."""
    run(
        generated,
        monkeypatch,
        "model",
        "fork",
        "samples/S1/models/m.yaml",
        "--name",
        "mine",
    )
    fork = generated / "samples" / "S1" / "models" / "mine.py"
    fork.write_text(
        fork.read_text(encoding="utf-8") + "\n# my own edit\n", encoding="utf-8"
    )

    result = run(generated, monkeypatch, "check")

    assert result.exit_code == 0, result.output


def test_check_leaves_plain_hand_written_scripts_alone(
    generated: Path, monkeypatch
) -> None:
    """Running a hand-written script unchanged is the adoption path.

    Flagging every such file would make `check` useless for exactly the case
    the package promises to support. Only files that claim to be generated are
    policed.
    """
    (generated / "samples" / "S1" / "models" / "handwritten.py").write_text(
        "problem = None\n", encoding="utf-8"
    )

    result = run(generated, monkeypatch, "check")

    assert result.exit_code == 0, result.output


def test_check_flags_a_generated_script_whose_spec_is_gone(
    generated: Path, monkeypatch
) -> None:
    """This one *is* orphaned: it claims a spec that no longer exists."""
    (generated / "samples" / "S1" / "models" / "m.yaml").unlink()

    result = run(generated, monkeypatch, "check", "--json")

    assert result.exit_code == 1
    kinds = {p["kind"] for p in json.loads(result.stdout)["problems"]}
    assert "missing-spec" in kinds


# --------------------------------------------------------------------------
# diff
# --------------------------------------------------------------------------


@pytest.fixture
def two_fits(generated: Path, monkeypatch) -> tuple[Path, str, str]:
    """A project with two fits differing only in optimizer settings."""
    script = "samples/S1/models/m.py"
    first = run(
        generated,
        monkeypatch,
        "fit",
        "run",
        script,
        "--method",
        "amoeba",
        "--steps",
        "8",
        "--seed",
        "1",
    )
    assert first.exit_code == 0, first.output
    second = run(
        generated,
        monkeypatch,
        "fit",
        "run",
        script,
        "--method",
        "amoeba",
        "--steps",
        "14",
        "--seed",
        "1",
    )
    assert second.exit_code == 0, second.output

    listed = run(generated, monkeypatch, "ls", "--json")
    rows = json.loads(listed.stdout)
    return generated, rows[1]["fit_id"], rows[0]["fit_id"]


def test_diff_attributes_a_settings_change(two_fits, monkeypatch) -> None:
    root, a, b = two_fits

    result = run(root, monkeypatch, "diff", a, b, "--json")

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["changed"]["settings"] is True
    assert payload["changed"]["inputs"] is False
    assert payload["changed"]["script"] is False
    assert "fit settings only" in payload["verdict"]


def test_diff_does_not_call_a_model_edit_a_data_change(two_fits, monkeypatch) -> None:
    """The script is one of the recorded inputs, so `inputs_digest` moves when
    the model is edited. Reading that as "the data changed" inverts the one
    distinction this command exists to draw -- it would tell you a comparison
    is invalid at exactly the moment it is most valid.
    """
    root, a, _ = two_fits
    script = root / "samples" / "S1" / "models" / "m.py"
    script.write_text(
        script.read_text(encoding="utf-8") + "\n# a comment\n", encoding="utf-8"
    )
    later = run(
        root,
        monkeypatch,
        "fit",
        "run",
        "samples/S1/models/m.py",
        "--method",
        "amoeba",
        "--steps",
        "8",
        "--seed",
        "1",
    )
    assert later.exit_code == 0, later.output
    b = json.loads(run(root, monkeypatch, "ls", "--json").stdout)[0]["fit_id"]

    payload = json.loads(run(root, monkeypatch, "diff", a, b, "--json").stdout)

    assert payload["changed"]["script"] is True
    assert payload["changed"]["data"] is False
    assert "DATA changed" not in payload["verdict"]
    assert "attributable to the model" in payload["verdict"]


def test_ls_says_what_each_fit_was_and_what_changed(two_fits, monkeypatch) -> None:
    """A fit id identifies a run but does not describe it, and by the tenth
    row the listing is a wall of hashes."""
    root, _, _ = two_fits

    rows = json.loads(run(root, monkeypatch, "ls", "--json").stdout)

    assert [r["change"] for r in rows][-1] == "first run of this model"
    assert "steps 8 -> 14" in rows[0]["change"], rows[0]["change"]
    assert rows[0]["compared_to"] == rows[1]["fit_id"]

    text = run(root, monkeypatch, "ls").output
    assert "steps 8 -> 14" in text, text


def test_ls_shows_the_note_a_fit_was_run_with(project: Path, monkeypatch) -> None:
    """The scientist's own words beat anything generated."""
    run(project, monkeypatch, "model", "new", "S1", "--name", "m")
    run(project, monkeypatch, "model", "generate", "samples/S1/models/m.yaml")
    run(
        project,
        monkeypatch,
        "fit",
        "run",
        "samples/S1/models/m.py",
        "--method",
        "amoeba",
        "--steps",
        "6",
        "--note",
        "oxide freed",
    )

    text = run(project, monkeypatch, "ls").output

    assert "oxide freed" in text


def test_diff_calls_out_a_data_change_above_everything_else(
    two_fits, monkeypatch
) -> None:
    """The distinction that matters: a better chi-squared on different data
    is not an improvement, and the verdict has to say so."""
    root, a, b = two_fits
    data = root / "samples" / "S1" / "data" / "steady"
    target = next(data.glob("*.txt"))
    target.write_text(
        target.read_text(encoding="utf-8") + "0.2 1e-6 1e-7 2e-4\n", encoding="utf-8"
    )
    third = run(
        root,
        monkeypatch,
        "fit",
        "run",
        "samples/S1/models/m.py",
        "--method",
        "amoeba",
        "--steps",
        "8",
        "--seed",
        "1",
    )
    assert third.exit_code == 0, third.output
    c = json.loads(run(root, monkeypatch, "ls", "--json").stdout)[0]["fit_id"]

    result = run(root, monkeypatch, "diff", a, c, "--json")

    payload = json.loads(result.stdout)
    assert payload["changed"]["inputs"] is True
    assert "DATA changed" in payload["verdict"]


def test_diff_reports_identical_runs_as_replicates(two_fits, monkeypatch) -> None:
    root, a, _ = two_fits
    forced = run(
        root,
        monkeypatch,
        "fit",
        "run",
        "samples/S1/models/m.py",
        "--method",
        "amoeba",
        "--steps",
        "8",
        "--seed",
        "1",
        "--force",
    )
    assert forced.exit_code == 0, forced.output
    replicate = json.loads(run(root, monkeypatch, "ls", "--json").stdout)[0]["fit_id"]

    result = run(root, monkeypatch, "diff", a, replicate, "--json")

    assert "replicates" in json.loads(result.stdout)["verdict"]


def test_diff_rejects_an_unknown_fit(two_fits, monkeypatch) -> None:
    root, a, _ = two_fits

    result = run(root, monkeypatch, "diff", a, "nonesuch")

    assert result.exit_code != 0
    assert "No fit matching" in result.output
