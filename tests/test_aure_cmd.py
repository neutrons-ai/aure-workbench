"""The `nrw aure` commands.

Everything here runs without a language-model endpoint. That is most of the
surface: writing the setup, refusing a setup that cannot be written, the dry
run, the no-endpoint refusal, and the import. Only the run itself needs an
endpoint, and the part of it worth testing -- that it refuses clearly, before
writing anything -- is exactly the part that does not.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from click.testing import CliRunner

from nr_workbench.cli import main

REFERENCE = Path(__file__).parent / "data" / "reference" / "steady"

NOTES = """# Cu on Ti

## Description

50 nm copper on 5 nm titanium on a silicon wafer.

## Details

In d8-THF. Measured through the silicon substrate.

## Fits to perform

There may be a CuOx skin on the copper.
"""

FITTED = {
    "substrate": {"name": "Si", "sld": 2.07, "roughness": 3.0},
    "layers": [
        {
            "name": "Ti",
            "sld": -1.95,
            "thickness": 48.2,
            "roughness": 5.1,
            "thickness_min": 30.0,
            "thickness_max": 70.0,
        },
        {
            "name": "Cu",
            "sld": 6.55,
            "thickness": 502.7,
            "roughness": 8.3,
            "thickness_min": 400.0,
            "thickness_max": 600.0,
        },
    ],
    "ambient": {"name": "dTHF", "sld": 6.35},
    "back_reflection": True,
    "dq_is_fwhm": True,
    "intensity": {"value": 1.0},
}


@pytest.fixture
def sample(project: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A project sitting in cwd, with one run of real reference data."""
    steady = project / "samples" / "Sample1" / "data" / "steady"
    steady.mkdir(parents=True, exist_ok=True)
    for source in sorted(REFERENCE.glob("REFL_218386_*_partial.txt")):
        shutil.copy(source, steady / source.name)
    (project / "samples" / "Sample1" / "sample.md").write_text(NOTES, encoding="utf-8")
    monkeypatch.chdir(project)
    return project


def _run(*args: str):
    """Invoke the CLI, keeping the exception for a readable failure."""
    return CliRunner().invoke(main, list(args), catch_exceptions=False)


def _finished_run(project: Path, model: dict | None = None, **kwargs) -> Path:
    """Write a plausible finished AuRE output directory."""
    output = project / "samples" / "Sample1" / "aure" / "Sample1-218386" / "output"
    output.mkdir(parents=True, exist_ok=True)
    payload = {
        "success": True,
        "error": None,
        "final_chi2": 1.83,
        "state": {"current_model": model or FITTED, "best_chi2": 1.83},
    }
    payload.update(kwargs)
    (output / "final_state.json").write_text(json.dumps(payload), encoding="utf-8")
    return output


# --------------------------------------------------------------------------
# nrw aure new
# --------------------------------------------------------------------------


def test_new_writes_a_setup_aure_accepts(sample: Path) -> None:
    """The whole point of writing it in code: AuRE's own loader takes it."""
    result = _run("aure", "new", "Sample1")

    assert result.exit_code == 0, result.output
    assert (sample / "samples/Sample1/aure/Sample1-218386/setup.yaml").is_file()
    assert "3 data file(s) resolved" in result.output


def test_new_separates_measured_facts_from_the_scientists_prose(sample: Path) -> None:
    """A reader has to be able to tell which half to go and check.

    The file half is exact and must not be edited; the prose half is the part
    that is guessed, and is where a wrong first fit comes from.
    """
    result = _run("aure", "new", "Sample1")

    assert "read from the files (exact, do not edit):" in result.output
    assert "read from sample.md (yours, check it):" in result.output


def test_new_refuses_an_unfilled_description(project: Path, monkeypatch) -> None:
    """The one input AuRE cannot derive, so the one worth stopping for."""
    steady = project / "samples" / "Sample1" / "data" / "steady"
    steady.mkdir(parents=True, exist_ok=True)
    for source in sorted(REFERENCE.glob("REFL_218386_*_partial.txt")):
        shutil.copy(source, steady / source.name)
    monkeypatch.chdir(project)

    result = _run("aure", "new", "Sample1")

    assert result.exit_code != 0
    # The refusal has to say what to write, or it just moves the confusion.
    assert "## Description" in result.output
    assert "beam enters through the substrate" in result.output


def test_new_refuses_a_sample_that_does_not_exist(project: Path, monkeypatch) -> None:
    monkeypatch.chdir(project)

    result = _run("aure", "new", "Nope")

    assert result.exit_code != 0
    assert "nrw sample new" in result.output


def test_new_does_not_overwrite_without_force(sample: Path) -> None:
    """A setup someone has corrected by hand is worth more than a re-run."""
    _run("aure", "new", "Sample1")

    result = _run("aure", "new", "Sample1")

    assert result.exit_code != 0
    assert "--force" in result.output


def test_new_writes_prose_as_a_literal_block(sample: Path) -> None:
    """The description is the field most likely to be hand-corrected, and a
    quoted scalar full of escaped newlines invites an edit that breaks it."""
    _run("aure", "new", "Sample1")

    text = (sample / "samples/Sample1/aure/Sample1-218386/setup.yaml").read_text()

    assert "sample_description: |" in text


def test_new_carries_the_hypothesis(sample: Path) -> None:
    import yaml

    _run("aure", "new", "Sample1")
    document = yaml.safe_load(
        (sample / "samples/Sample1/aure/Sample1-218386/setup.yaml").read_text()
    )

    assert "CuOx" in document["hypothesis"]


def test_new_writes_no_absolute_paths(sample: Path) -> None:
    """A beamtime directory gets shared and archived; this is the defect the
    whole project exists to remove."""
    _run("aure", "new", "Sample1")

    text = (sample / "samples/Sample1/aure/Sample1-218386/setup.yaml").read_text()

    assert str(sample) not in text


# --------------------------------------------------------------------------
# nrw aure run
# --------------------------------------------------------------------------


def test_dry_run_validates_and_runs_nothing(sample: Path) -> None:
    _run("aure", "new", "Sample1")

    result = _run(
        "aure", "run", "samples/Sample1/aure/Sample1-218386/setup.yaml", "--dry-run"
    )

    assert result.exit_code == 0, result.output
    assert "nothing run" in result.output
    assert not (sample / "samples/Sample1/aure/Sample1-218386/output").exists()


def test_dry_run_names_every_environment_only_knob(sample: Path) -> None:
    """AuRE's setup file does not record these, so the run has to.

    If one is ever added upstream and we do not set it, this is the line that
    will not mention it -- which is the only warning available.
    """
    from nr_workbench.aure_adapter import ENVIRONMENT_ONLY_KNOBS

    _run("aure", "new", "Sample1")
    result = _run(
        "aure", "run", "samples/Sample1/aure/Sample1-218386/setup.yaml", "--dry-run"
    )

    # ROUGHNESS_MAX_OUTER is deliberately left unset: unset means "each layer's
    # own roughness_max", which is a real default rather than a magic number.
    for knob in ENVIRONMENT_ONLY_KNOBS:
        if knob == "ROUGHNESS_MAX_OUTER":
            continue
        assert knob in result.output, knob


def test_mode_enumeration_changes_the_recorded_knobs(sample: Path) -> None:
    """The retry has to be distinguishable from the first pass on disk."""
    _run("aure", "new", "Sample1")
    setup = "samples/Sample1/aure/Sample1-218386/setup.yaml"

    plain = _run("aure", "run", setup, "--dry-run")
    enumerated = _run("aure", "run", setup, "--mode-enumeration", "--dry-run")

    assert "MODE_ENUMERATION=0" in plain.output
    assert "MODE_ENUMERATION=1" in enumerated.output


def test_run_refuses_without_an_endpoint(sample: Path, monkeypatch) -> None:
    """AuRE fails at exit 1 before fitting without one; failing here is kinder
    and says which command diagnoses it."""
    monkeypatch.setattr("nr_workbench.aure_adapter.llm_available", lambda: False)
    _run("aure", "new", "Sample1")

    result = _run("aure", "run", "samples/Sample1/aure/Sample1-218386/setup.yaml")

    assert result.exit_code != 0
    assert "nrw check-llm" in result.output


def test_run_writes_nothing_when_it_refuses(sample: Path, monkeypatch) -> None:
    """A refused run must not leave a half-made output directory behind that a
    later `nrw aure import` could mistake for a result."""
    monkeypatch.setattr("nr_workbench.aure_adapter.llm_available", lambda: False)
    _run("aure", "new", "Sample1")

    _run("aure", "run", "samples/Sample1/aure/Sample1-218386/setup.yaml")

    run_dir = sample / "samples/Sample1/aure/Sample1-218386"
    assert not (run_dir / "output").exists()
    assert not (run_dir / "run-env.json").exists()


def test_run_rejects_a_setup_aure_will_not_parse(sample: Path) -> None:
    """Better here than after the intake calls have been paid for."""
    _run("aure", "new", "Sample1")
    setup = sample / "samples/Sample1/aure/Sample1-218386/setup.yaml"
    setup.write_text(setup.read_text() + "\nnot_a_real_key: 1\n", encoding="utf-8")

    result = _run("aure", "run", str(setup), "--dry-run")

    assert result.exit_code != 0


# --------------------------------------------------------------------------
# nrw aure import
# --------------------------------------------------------------------------


def test_import_writes_a_spec_that_validates(sample: Path) -> None:
    """The handover point: after this it is an ordinary spec on the normal path."""
    output = _finished_run(sample)

    result = _run(
        "aure", "import", str(output), "--sample", "Sample1", "--name", "first"
    )

    assert result.exit_code == 0, result.output
    validated = _run("model", "validate", "samples/Sample1/models/first.yaml")
    assert validated.exit_code == 0, validated.output


def test_import_records_that_aure_proposed_the_stack(sample: Path) -> None:
    """Months later, a reader must be able to tell a proposal from a measurement
    without going looking."""
    output = _finished_run(sample)

    _run("aure", "import", str(output), "--sample", "Sample1", "--name", "first")
    text = (sample / "samples/Sample1/models/first.yaml").read_text()

    assert "PROPOSED by AuRE" in text
    assert "not a" in text and "measurement" in text


def test_import_keeps_the_measured_angles(sample: Path) -> None:
    """Theta sets the resolution; the nominal 0.45/1.2/3.5 would be wrong.

    AuRE does not report the angles back, so they are re-read from the files'
    own headers at import -- this is what says that happened.
    """
    import yaml

    output = _finished_run(sample)

    _run("aure", "import", str(output), "--sample", "Sample1", "--name", "first")
    document = yaml.safe_load(
        (sample / "samples/Sample1/models/first.yaml").read_text()
    )

    assert 1.201 in document["states"][0]["thetas"]
    assert 1.2 not in document["states"][0]["thetas"]


def test_import_orders_the_stack_for_the_geometry(sample: Path) -> None:
    """Back reflection puts the substrate last, and the validator says so
    independently -- from the ordering and the measured critical edge."""
    output = _finished_run(sample)

    _run("aure", "import", str(output), "--sample", "Sample1", "--name", "first")
    validated = _run("model", "validate", "samples/Sample1/models/first.yaml")

    assert "back reflection" in validated.output


def test_import_refuses_a_failed_run(sample: Path) -> None:
    """Importing a failed run's last model would fake a result."""
    output = _finished_run(sample, error="intake failed", success=False)

    result = _run(
        "aure", "import", str(output), "--sample", "Sample1", "--name", "first"
    )

    assert result.exit_code != 0
    assert "failed" in result.output


def test_import_refuses_an_unfinished_run(sample: Path) -> None:
    """An interrupted run has checkpoints but no answer; say which and how."""
    output = sample / "samples/Sample1/aure/Sample1-218386/output"
    output.mkdir(parents=True, exist_ok=True)

    result = _run(
        "aure", "import", str(output), "--sample", "Sample1", "--name", "first"
    )

    assert result.exit_code != 0
    assert "resume" in result.output


def test_import_does_not_overwrite_without_force(sample: Path) -> None:
    output = _finished_run(sample)
    _run("aure", "import", str(output), "--sample", "Sample1", "--name", "first")

    result = _run(
        "aure", "import", str(output), "--sample", "Sample1", "--name", "first"
    )

    assert result.exit_code != 0
    assert "--force" in result.output


# --------------------------------------------------------------------------
# The run's orchestration, with only the process boundary stubbed
# --------------------------------------------------------------------------


@pytest.fixture
def fake_aure(monkeypatch: pytest.MonkeyPatch):
    """Stub `subprocess.run`, not `_invoke`, and record what the child saw.

    The level matters. Stubbing `_invoke` would leave the environment merge
    untested, and a merge written the other way round -- `{**overrides,
    **os.environ}` -- would pass every assertion while letting whatever the
    scientist happened to export beat the values written into `run-env.json`.
    That is exactly the "two runs from one setup differ and nothing on disk
    says why" failure the file exists to prevent, so the test has to see the
    environment the process would actually get.

    Everything either side of the boundary is the real code, including the
    setup AuRE would have been handed.
    """
    calls: list[dict] = []

    class _Result:
        returncode = 0

    def _run(command, env=None, check=False, **kwargs):
        calls.append({"command": list(command), "env": dict(env or {})})
        output = Path(command[command.index("-o") + 1])
        output.mkdir(parents=True, exist_ok=True)
        (output / "final_state.json").write_text(
            json.dumps(
                {
                    "success": True,
                    "error": None,
                    "final_chi2": 1.83,
                    "state": {"current_model": FITTED, "best_chi2": 1.83},
                }
            ),
            encoding="utf-8",
        )
        return _Result()

    monkeypatch.setattr("nr_workbench.commands.aure_cmd.subprocess.run", _run)
    monkeypatch.setattr(
        "nr_workbench.commands.aure_cmd._aure_binary", lambda: "/fake/bin/aure"
    )
    monkeypatch.setattr("nr_workbench.aure_adapter.llm_available", lambda: True)
    monkeypatch.setattr(
        "nr_workbench.aure_adapter.llm_info",
        lambda: {"available": True, "provider": "test", "model": "m"},
    )
    return calls


def test_run_records_the_knobs_aure_does_not(sample: Path, fake_aure) -> None:
    """AuRE's setup file does not record MODE_ENUMERATION and friends, so two
    runs from one setup could differ with nothing on disk explaining why."""
    _run("aure", "new", "Sample1")

    _run("aure", "run", "samples/Sample1/aure/Sample1-218386/setup.yaml")

    recorded = json.loads(
        (sample / "samples/Sample1/aure/Sample1-218386/run-env.json").read_text()
    )
    assert recorded["MODE_ENUMERATION"] == "0"
    child = fake_aure[0]["env"]
    assert all(child[key] == value for key, value in recorded.items() if value)


def test_run_passes_the_knobs_to_the_child(sample: Path, fake_aure) -> None:
    """Recording them and not setting them would be worse than neither."""
    _run("aure", "new", "Sample1")

    _run(
        "aure",
        "run",
        "samples/Sample1/aure/Sample1-218386/setup.yaml",
        "--mode-enumeration",
    )

    assert fake_aure[0]["env"]["MODE_ENUMERATION"] == "1"


def test_run_writes_the_budget_into_the_setup(sample: Path, fake_aure) -> None:
    """These keys DO have setup keys, so they belong in the file where they
    are part of the record -- not in the environment where they are not."""
    import yaml

    _run("aure", "new", "Sample1")

    _run("aure", "run", "samples/Sample1/aure/Sample1-218386/setup.yaml")

    document = yaml.safe_load(
        (sample / "samples/Sample1/aure/Sample1-218386/setup.yaml").read_text()
    )
    assert document["fit_method"] == "de"
    assert document["max_refinements"] == 1


def test_applying_the_budget_keeps_the_setup_loadable(sample: Path, fake_aure) -> None:
    """The budget is written by rewriting the file; AuRE must still take it."""
    from nr_workbench.aure_adapter import validate_setup

    _run("aure", "new", "Sample1")
    _run("aure", "run", "samples/Sample1/aure/Sample1-218386/setup.yaml")

    loaded = validate_setup(sample / "samples/Sample1/aure/Sample1-218386/setup.yaml")

    assert len(loaded["states"][0]["data_files"]) == 3


def test_applying_the_budget_keeps_the_explanatory_header(
    sample: Path, fake_aure
) -> None:
    """The header says which half of the file is measured and which is prose.
    Losing it on the first run would be losing it in every real case."""
    _run("aure", "new", "Sample1")

    _run("aure", "run", "samples/Sample1/aure/Sample1-218386/setup.yaml")

    text = (sample / "samples/Sample1/aure/Sample1-218386/setup.yaml").read_text()
    assert "written by `nrw aure new`" in text


def test_standard_budget_leaves_aures_own_defaults_alone(
    sample: Path, fake_aure
) -> None:
    """Writing AuRE's defaults into the file would freeze values it owns."""
    import yaml

    _run("aure", "new", "Sample1")

    _run(
        "aure",
        "run",
        "samples/Sample1/aure/Sample1-218386/setup.yaml",
        "--budget",
        "standard",
    )

    document = yaml.safe_load(
        (sample / "samples/Sample1/aure/Sample1-218386/setup.yaml").read_text()
    )
    assert "fit_method" not in document


def test_run_reports_the_stack_and_the_next_command(sample: Path, fake_aure) -> None:
    _run("aure", "new", "Sample1")

    result = _run("aure", "run", "samples/Sample1/aure/Sample1-218386/setup.yaml")

    assert result.exit_code == 0, result.output
    assert "1.83" in result.output
    assert "nrw aure import" in result.output
    # Incident medium last, as refl1d reads it.
    assert result.output.index("dTHF") < result.output.index("Si")


def test_a_failed_run_points_at_the_checkpoints(sample: Path, monkeypatch) -> None:
    """An interrupted run is resumable, and the message has to say so -- the
    alternative is paying for the intake calls again."""
    monkeypatch.setattr("nr_workbench.aure_adapter.llm_available", lambda: True)
    monkeypatch.setattr(
        "nr_workbench.aure_adapter.llm_info", lambda: {"provider": "t", "model": "m"}
    )
    monkeypatch.setattr("nr_workbench.commands.aure_cmd._invoke", lambda *a, **k: 1)
    _run("aure", "new", "Sample1")

    result = _run("aure", "run", "samples/Sample1/aure/Sample1-218386/setup.yaml")

    assert result.exit_code != 0
    assert "aure resume" in result.output


# --------------------------------------------------------------------------
# Which run was fitted
# --------------------------------------------------------------------------


def test_import_infers_the_run_from_the_setup(sample: Path, fake_aure) -> None:
    """A second run on the sample must not make `import` ambiguous.

    The model's thicknesses are only meaningful beside the angles of the files
    that produced them, so the run is recovered from the setup AuRE was given
    rather than asked for again.
    """
    second = sample / "samples" / "Sample1" / "data" / "steady"
    for source in sorted(REFERENCE.glob("REFL_218393_*_partial.txt")):
        shutil.copy(source, second / source.name)
    _run("sample", "scan", "Sample1")
    _run("aure", "new", "Sample1", "--run", "218386")
    _run("aure", "run", "samples/Sample1/aure/Sample1-218386/setup.yaml")

    result = _run(
        "aure",
        "import",
        "samples/Sample1/aure/Sample1-218386/output",
        "--sample",
        "Sample1",
        "--name",
        "first",
    )

    assert result.exit_code == 0, result.output
    import yaml

    document = yaml.safe_load(
        (sample / "samples/Sample1/models/first.yaml").read_text()
    )
    assert document["states"][0]["run"] == 218386


def test_run_of_matches_the_files_the_setup_named(sample: Path) -> None:
    """The link is the data files themselves, not the directory's name."""
    from nr_workbench.aure_import import run_of
    from nr_workbench.project.scan import scan_sample

    _run("aure", "new", "Sample1")
    output = sample / "samples/Sample1/aure/Sample1-218386/output"
    output.mkdir(parents=True, exist_ok=True)

    assert run_of(output, scan_sample(sample, "Sample1")) == 218386


def test_run_of_declines_to_guess_without_a_setup(sample: Path, tmp_path: Path) -> None:
    """An output directory somebody moved is not evidence of anything."""
    from nr_workbench.aure_import import run_of
    from nr_workbench.project.scan import scan_sample

    assert run_of(tmp_path, scan_sample(sample, "Sample1")) is None


def test_our_knobs_beat_the_scientists_shell(
    sample: Path, fake_aure, monkeypatch
) -> None:
    """A knob exported in .bashrc must not silently change the fitted model.

    This is the whole reason `run-env.json` exists. If the shell wins, the
    recorded file is not a record of anything.
    """
    monkeypatch.setenv("MODE_ENUMERATION", "1")
    monkeypatch.setenv("THIN_LAYER_MODE_SEEDS", "99")
    _run("aure", "new", "Sample1")

    _run("aure", "run", "samples/Sample1/aure/Sample1-218386/setup.yaml")

    child = fake_aure[0]["env"]
    assert child["MODE_ENUMERATION"] == "0"
    assert child["THIN_LAYER_MODE_SEEDS"] == "3"


def test_roughness_max_outer_is_not_inherited(
    sample: Path, fake_aure, monkeypatch
) -> None:
    """Upstream's default is each layer's own roughness_max, which no number
    expresses -- so we clear the variable rather than pass a value we made up.

    Left inherited, it would change the fitted model and appear in no record.
    """
    monkeypatch.setenv("ROUGHNESS_MAX_OUTER", "7")
    _run("aure", "new", "Sample1")

    _run("aure", "run", "samples/Sample1/aure/Sample1-218386/setup.yaml")

    assert "ROUGHNESS_MAX_OUTER" not in fake_aure[0]["env"]


def test_every_declared_knob_is_actually_set(sample: Path, fake_aure) -> None:
    """The adapter's list and the runner's environment must not drift.

    A knob named in ENVIRONMENT_ONLY_KNOBS but never set is inherited from the
    shell, changes the model, and is recorded nowhere -- and nothing else in
    the system would report it.
    """
    from nr_workbench.aure_adapter import ENVIRONMENT_ONLY_KNOBS
    from nr_workbench.commands.aure_cmd import _run_environment

    assert set(_run_environment(mode_enumeration=False)) == set(ENVIRONMENT_ONLY_KNOBS)


def test_the_shells_path_still_reaches_the_child(sample: Path, fake_aure) -> None:
    """AuRE needs the environment -- its credentials come from there."""
    _run("aure", "new", "Sample1")

    _run("aure", "run", "samples/Sample1/aure/Sample1-218386/setup.yaml")

    import os

    assert fake_aure[0]["env"]["PATH"] == os.environ["PATH"]


def test_the_child_is_told_to_analyze_this_setup(sample: Path, fake_aure) -> None:
    """The CLI contract we depend on, asserted rather than assumed."""
    _run("aure", "new", "Sample1")

    _run("aure", "run", "samples/Sample1/aure/Sample1-218386/setup.yaml")

    command = fake_aure[0]["command"]
    assert command[1] == "analyze"
    assert "-c" in command and "-o" in command


def test_a_missing_aure_binary_is_reported(sample: Path, monkeypatch) -> None:
    monkeypatch.setattr("nr_workbench.aure_adapter.llm_available", lambda: True)
    monkeypatch.setattr(
        "nr_workbench.aure_adapter.llm_info", lambda: {"provider": "t", "model": "m"}
    )
    monkeypatch.setattr("nr_workbench.commands.aure_cmd._aure_binary", lambda: None)
    _run("aure", "new", "Sample1")

    result = _run("aure", "run", "samples/Sample1/aure/Sample1-218386/setup.yaml")

    assert result.exit_code != 0
    assert "not found" in result.output


# --------------------------------------------------------------------------
# A setup may not choose the endpoint
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key,value",
    [
        ("llm_base_url", "http://127.0.0.1:9/v1"),
        ("llm_api_key", "sk-not-a-real-key"),
        ("llm_provider", "openai"),
    ],
)
def test_a_setup_may_not_redirect_the_endpoint(
    sample: Path, fake_aure, key: str, value: str
) -> None:
    """A setup is a committed, shareable file, and AuRE applies these keys as
    environment overrides -- taking this machine's own API key with it.

    So one arriving from a collaborator or an archived beamtime must not be
    able to decide where the credential is sent.
    """
    _run("aure", "new", "Sample1")
    setup = sample / "samples/Sample1/aure/Sample1-218386/setup.yaml"
    setup.write_text(setup.read_text() + f"\n{key}: {value}\n", encoding="utf-8")

    result = _run("aure", "run", str(setup))

    assert result.exit_code != 0
    assert key in result.output
    assert not fake_aure, "the run must be refused before the process starts"


def test_the_endpoint_refusal_says_to_rotate_the_key(sample: Path) -> None:
    """Somebody who ran a setup they did not write needs to be told."""
    _run("aure", "new", "Sample1")
    setup = sample / "samples/Sample1/aure/Sample1-218386/setup.yaml"
    setup.write_text(setup.read_text() + "\nllm_api_key: sk-x\n", encoding="utf-8")

    result = _run("aure", "run", str(setup), "--dry-run")

    assert "rotate" in result.output


# --------------------------------------------------------------------------
# Under an unattended agent
# --------------------------------------------------------------------------


def test_run_is_refused_under_an_agent(sample: Path, fake_aure, monkeypatch) -> None:
    """Every other model-calling path in this package stands down under a
    harness; a billed, open-ended run is the last one that should not."""
    monkeypatch.setenv("NRW_AGENT", "1")
    _run("aure", "new", "Sample1")

    result = _run("aure", "run", "samples/Sample1/aure/Sample1-218386/setup.yaml")

    assert result.exit_code != 0
    assert "ESCALATIONS.md" in result.output
    assert not fake_aure


def test_dry_run_is_still_allowed_under_an_agent(
    sample: Path, fake_aure, monkeypatch
) -> None:
    """An agent that can validate its own work and report is the point."""
    monkeypatch.setenv("NRW_AGENT", "1")
    _run("aure", "new", "Sample1")

    result = _run(
        "aure", "run", "samples/Sample1/aure/Sample1-218386/setup.yaml", "--dry-run"
    )

    assert result.exit_code == 0, result.output


def test_the_guard_refuses_the_command_line_too(sample: Path) -> None:
    """Two independent mechanisms: the hook, and nrw refusing from inside."""
    from nr_workbench.agent.guard import judge

    assert judge("nrw aure run setup.yaml").allowed is False
    assert judge("nrw aure run setup.yaml --dry-run").allowed is True
    assert judge("nrw aure new Sample1").allowed is True


# --------------------------------------------------------------------------
# Paths, and what the user is told
# --------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["../evil", "a/b", ".."])
def test_a_name_may_not_be_a_path(sample: Path, bad: str) -> None:
    """`--name` becomes a path segment; containment should not be accidental."""
    output = _finished_run(sample)

    result = _run("aure", "import", str(output), "--sample", "Sample1", "--name", bad)

    assert result.exit_code != 0
    assert "plain name" in result.output


def test_new_warns_when_the_geometry_was_never_stated(
    project: Path, monkeypatch
) -> None:
    """Computing the warning and never showing it would be the same as not
    having it: the scientist pays for a run to discover the geometry was wrong."""
    steady = project / "samples" / "Sample1" / "data" / "steady"
    steady.mkdir(parents=True, exist_ok=True)
    for source in sorted(REFERENCE.glob("REFL_218386_*_partial.txt")):
        shutil.copy(source, steady / source.name)
    (project / "samples" / "Sample1" / "sample.md").write_text(
        "# S\n\n## Description\n\n30 nm polystyrene on silicon.\n", encoding="utf-8"
    )
    monkeypatch.chdir(project)

    result = _run("aure", "new", "Sample1")

    assert result.exit_code == 0, result.output
    assert "back_reflection" in result.output


def test_force_replaces_a_setup(sample: Path) -> None:
    """The overwrite path itself, not just the message naming it."""
    _run("aure", "new", "Sample1")
    setup = sample / "samples/Sample1/aure/Sample1-218386/setup.yaml"
    setup.write_text("# hand-edited\n", encoding="utf-8")

    result = _run("aure", "new", "Sample1", "--force")

    assert result.exit_code == 0, result.output
    assert "sample_description" in setup.read_text()


def test_without_force_a_hand_edit_survives_byte_for_byte(sample: Path) -> None:
    """A setup somebody corrected is worth more than a regenerated one."""
    _run("aure", "new", "Sample1")
    setup = sample / "samples/Sample1/aure/Sample1-218386/setup.yaml"
    edited = setup.read_text() + "\n# ask Bob about the solvent\n"
    setup.write_text(edited, encoding="utf-8")

    _run("aure", "new", "Sample1")

    assert setup.read_text() == edited


def test_import_refuses_another_samples_run(sample: Path) -> None:
    """Pairing AuRE's layers with a different sample's measurements would
    validate, generate and fit -- and be about the wrong experiment."""
    _run("aure", "new", "Sample1")
    _finished_run(sample)
    _run("sample", "new", "Sample2")

    result = _run(
        "aure",
        "import",
        "samples/Sample1/aure/Sample1-218386/output",
        "--sample",
        "Sample2",
        "--name",
        "first",
    )

    assert result.exit_code != 0
    assert "Sample2" in result.output


def test_import_reports_an_unknown_sample_clearly(sample: Path) -> None:
    """The likeliest typo on a command with two required string options."""
    output = _finished_run(sample)

    result = _run("aure", "import", str(output), "--sample", "Nope", "--name", "n")

    assert result.exit_code != 0
    assert "Nope" in result.output


def test_import_reports_a_run_that_produced_nothing(sample: Path, monkeypatch) -> None:
    """AuRE exiting 0 without writing final_state.json is a real outcome."""
    monkeypatch.setattr("nr_workbench.aure_adapter.llm_available", lambda: True)
    monkeypatch.setattr(
        "nr_workbench.aure_adapter.llm_info", lambda: {"provider": "t", "model": "m"}
    )
    monkeypatch.setattr(
        "nr_workbench.commands.aure_cmd._aure_binary", lambda: "/fake/aure"
    )

    class _Ok:
        returncode = 0

    monkeypatch.setattr(
        "nr_workbench.commands.aure_cmd.subprocess.run", lambda *a, **k: _Ok()
    )
    _run("aure", "new", "Sample1")

    result = _run("aure", "run", "samples/Sample1/aure/Sample1-218386/setup.yaml")

    assert result.exit_code != 0
    assert "resume" in result.output


def test_import_warns_when_the_knobs_were_not_recorded(sample: Path) -> None:
    """A spec from a moved run must not look like one from a recorded run."""
    output = _finished_run(sample)

    _run("aure", "import", str(output), "--sample", "Sample1", "--name", "first")
    text = (sample / "samples/Sample1/models/first.yaml").read_text()

    assert "NOT recorded" in text


def test_a_stray_comment_is_not_hoisted_into_the_header(
    sample: Path, fake_aure
) -> None:
    """The file is one we told the scientist to edit. Silently relocating an
    annotation away from the line it was about is worse than refusing."""
    _run("aure", "new", "Sample1")
    setup = sample / "samples/Sample1/aure/Sample1-218386/setup.yaml"
    setup.write_text(
        setup.read_text() + "\n# ask Bob whether 218387 belongs here\n",
        encoding="utf-8",
    )

    result = _run("aure", "run", str(setup))

    assert result.exit_code != 0
    assert "ask Bob" in result.output


# Advice, not capability: the provider lives in AuRE. What this command owes
# the user is naming it when their AuRE has it, and not when it does not.
def test_aure_run_refusal_offers_the_harness_as_the_endpoint(
    sample: Path, monkeypatch
) -> None:
    """`nrw aure run` is the command that genuinely needs an endpoint, so it is
    the one where the advice matters most."""
    monkeypatch.setattr("nr_workbench.aure_adapter.llm_available", lambda: False)
    monkeypatch.setattr("nr_workbench.aure_adapter.claude_code_supported", lambda: True)
    CliRunner().invoke(main, ["aure", "new", "Sample1"])

    result = CliRunner().invoke(
        main, ["aure", "run", "samples/Sample1/aure/Sample1-218386/setup.yaml"]
    )

    assert result.exit_code != 0
    assert "claude_code" in result.output


def test_aure_run_refusal_omits_it_on_an_older_aure(sample: Path, monkeypatch) -> None:
    monkeypatch.setattr("nr_workbench.aure_adapter.llm_available", lambda: False)
    monkeypatch.setattr(
        "nr_workbench.aure_adapter.claude_code_supported", lambda: False
    )
    CliRunner().invoke(main, ["aure", "new", "Sample1"])

    result = CliRunner().invoke(
        main, ["aure", "run", "samples/Sample1/aure/Sample1-218386/setup.yaml"]
    )

    assert result.exit_code != 0
    assert "claude_code" not in result.output
    assert "nrw check-llm" in result.output
