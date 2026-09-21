"""`nrw handoff`, `nrw audience`, and making `nrw` findable.

These exist because of one measured session. An interactive assistant told to
"pick up where the agent left off" spent 28 tool calls orienting before it did
anything, ten of them looking for the `nrw` binary, and then carried an
absolute path on 136 of its 268 calls. Every one of those facts was derivable
from the project.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from nr_workbench.cli import main

pytestmark = pytest.mark.integration


def run(root: Path, monkeypatch: pytest.MonkeyPatch, *args: str):
    monkeypatch.chdir(root)
    return CliRunner().invoke(main, list(args))


# --------------------------------------------------------------------------
# Finding nrw at all
# --------------------------------------------------------------------------


def test_the_probe_ignores_our_own_environment(monkeypatch) -> None:
    """The check must not pass merely because *this* process found nrw.

    This is the whole subtlety. We are running from an environment that has
    `nrw` on PATH -- it must, or there would be no test -- so a child process
    inheriting that environment resolves it every time, and the check would
    report health in exactly the situation it exists to catch.
    """
    from nr_workbench.project import toolpath

    captured: dict[str, object] = {}

    class Result:
        returncode = 0
        stdout = "/somewhere/nrw\n"

    def fake_run(argv, **kwargs):
        captured["env"] = kwargs.get("env", {})
        return Result()

    monkeypatch.setattr(toolpath.subprocess, "run", fake_run)
    toolpath.resolvable_in_fresh_shell()

    env = captured["env"]
    assert isinstance(env, dict)
    assert env["PATH"] == toolpath._BARE_PATH
    assert "VIRTUAL_ENV" not in env


def test_a_slow_or_broken_probe_does_not_cry_wolf(monkeypatch) -> None:
    """Cannot-check is not the same as broken, and must not send anyone to fix
    a PATH that is fine."""
    from nr_workbench.project import toolpath

    def explode(argv, **kwargs):
        raise OSError("no shell here")

    monkeypatch.setattr(toolpath.subprocess, "run", explode)

    assert toolpath.resolvable_in_fresh_shell() is True


def test_install_writes_a_shim_and_an_env_var(tmp_path: Path) -> None:
    from nr_workbench.project import toolpath

    executable = tmp_path / "bin" / "nrw"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\n")
    executable.chmod(0o755)

    report = toolpath.install(tmp_path, executable=executable)

    shim = tmp_path / toolpath.SHIM_RELPATH
    assert shim.is_file()
    assert str(executable) in shim.read_text(encoding="utf-8")
    settings = json.loads(
        (tmp_path / toolpath.LOCAL_SETTINGS).read_text(encoding="utf-8")
    )
    assert settings["env"][toolpath.NRW_BIN_ENV] == str(executable)
    assert report.executable == executable


def test_install_never_writes_path(tmp_path: Path) -> None:
    """Harness settings do not interpolate ${PATH} -- verified, not assumed --
    so writing one is an override, and an override is a thing a person asks
    for rather than a thing a scaffold does."""
    from nr_workbench.project import toolpath

    executable = tmp_path / "nrw"
    executable.write_text("#!/bin/sh\n")
    executable.chmod(0o755)

    toolpath.install(tmp_path, executable=executable)

    settings = json.loads(
        (tmp_path / toolpath.LOCAL_SETTINGS).read_text(encoding="utf-8")
    )
    assert "PATH" not in settings["env"]


def test_install_merges_into_existing_settings(tmp_path: Path) -> None:
    """The file is the user's; we add a key, we do not replace it."""
    from nr_workbench.project import toolpath

    settings_path = tmp_path / toolpath.LOCAL_SETTINGS
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(
        json.dumps({"env": {"MINE": "keep"}, "permissions": {"deny": []}}),
        encoding="utf-8",
    )
    executable = tmp_path / "nrw"
    executable.write_text("#!/bin/sh\n")
    executable.chmod(0o755)

    toolpath.install(tmp_path, executable=executable)

    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    assert settings["env"]["MINE"] == "keep"
    assert settings["permissions"] == {"deny": []}
    assert toolpath.NRW_BIN_ENV in settings["env"]


def test_a_malformed_settings_file_is_left_alone(tmp_path: Path) -> None:
    from nr_workbench.project import toolpath

    settings_path = tmp_path / toolpath.LOCAL_SETTINGS
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text("{not json", encoding="utf-8")
    executable = tmp_path / "nrw"
    executable.write_text("#!/bin/sh\n")
    executable.chmod(0o755)

    report = toolpath.install(tmp_path, executable=executable)

    assert settings_path.read_text(encoding="utf-8") == "{not json"
    assert report.settings_note


def test_the_path_override_is_a_whole_path(tmp_path: Path, monkeypatch) -> None:
    """No interpolation is available, so the value has to stand alone."""
    from nr_workbench.project import toolpath

    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    executable = tmp_path / "bin" / "nrw"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\n")
    executable.chmod(0o755)

    value = toolpath.path_override(tmp_path, executable=executable)

    assert "${PATH}" not in value
    assert value.split(":")[0] == str(executable.parent)
    assert "/usr/bin" in value


# --------------------------------------------------------------------------
# Who is reading
# --------------------------------------------------------------------------


def test_defaults_are_not_a_declaration(project: Path) -> None:
    """A template choosing `practitioner` is not somebody saying so, and the
    two justify different amounts of asking."""
    from nr_workbench.project import audience

    loaded = audience.load(project)

    assert loaded.reflectometry == "practitioner"
    assert loaded.declared is False


def test_setting_an_axis_survives_a_round_trip(project: Path, monkeypatch) -> None:
    result = run(project, monkeypatch, "audience", "--set", "statistics=expert")

    assert result.exit_code == 0, result.output

    from nr_workbench.project import audience

    loaded = audience.load(project)
    assert loaded.statistics == "expert"
    assert loaded.declared is True


def test_writing_the_block_keeps_the_comments(project: Path, monkeypatch) -> None:
    """nrw.toml is mostly comments explaining the instrument conventions, and a
    TOML round-trip through the standard library deletes every one of them."""
    before = (project / "nrw.toml").read_text(encoding="utf-8")
    assert "dq_convention" in before

    run(project, monkeypatch, "audience", "--set", "domain=newcomer")

    after = (project / "nrw.toml").read_text(encoding="utf-8")
    # A sentence from the block that only a preserved comment can supply. It
    # used to be "the 4th column of every reduced file is FWHM"; that stopped
    # being true when REF_L's second reduction started writing sigma, and the
    # comment now says the convention is read per file.
    assert "read from each file's header" in after
    assert "dq_convention" in after
    assert 'domain = "newcomer"' in after


def test_an_unknown_value_is_refused(project: Path, monkeypatch) -> None:
    result = run(project, monkeypatch, "audience", "--set", "statistics=wizard")

    assert result.exit_code != 0
    assert "must be one of" in result.output


def test_the_axes_are_independent(project: Path) -> None:
    """The reference user is the reason: a reflectometry expert who wanted the
    statistics spelled out. One dial gets that reader wrong twice."""
    from nr_workbench.project import audience

    who = audience.Audience(
        reflectometry="expert", statistics="newcomer", declared=True
    )
    advice = " ".join(audience.guidance(who))

    assert "vocabulary is shared" in advice
    assert "never quote a sigma" in advice


# --------------------------------------------------------------------------
# The handoff itself
# --------------------------------------------------------------------------


def test_handoff_leads_with_how_to_run_nrw(project: Path, monkeypatch) -> None:
    """Every other line suggests a command, so this one comes first."""
    result = run(project, monkeypatch, "handoff", "Sample1")

    assert result.exit_code == 0, result.output
    assert result.output.index("## Running nrw here") < 400


def test_handoff_puts_the_escalations_first_and_whole(
    project: Path, monkeypatch
) -> None:
    (project / "ESCALATIONS.md").write_text(
        "# Escalations\n\n## 4. `fixed: true` writes 1.0 whatever you asked for\n"
        "Use `value: X, pm: tiny` instead.\n",
        encoding="utf-8",
    )

    result = run(project, monkeypatch, "handoff", "Sample1")

    assert "Escalations -- READ THIS FIRST" in result.output
    assert "pm: tiny" in result.output
    # Quoted, not spliced: the file is a previous session's writing and is full
    # of its own headings.
    assert "  | ## 4. `fixed: true`" in result.output
    assert result.output.index("Escalations") < result.output.index("Read these")


def test_handoff_states_the_reading_order_and_why(project: Path, monkeypatch) -> None:
    result = run(project, monkeypatch, "handoff", "Sample1")

    assert "Read these, in this order" in result.output
    assert "ESCALATIONS.md" in result.output
    # The natural order is wrong, so the wrongness is said out loud.
    assert "Not `results/` first" in result.output


def test_handoff_carries_the_audience(project: Path, monkeypatch) -> None:
    run(project, monkeypatch, "audience", "--set", "role=delegates")

    result = run(project, monkeypatch, "handoff", "Sample1")

    assert "Who you are working with" in result.output
    assert "They delegate" in result.output


def test_handoff_names_the_rules_that_are_not_visible_in_the_tree(
    project: Path, monkeypatch
) -> None:
    result = run(project, monkeypatch, "handoff", "Sample1")

    assert "Every recorded fit is immutable" in result.output
    assert "finished artefact" in result.output


def test_handoff_with_no_sample_lists_them(project: Path, monkeypatch) -> None:
    result = run(project, monkeypatch, "handoff")

    assert result.exit_code == 0, result.output
    assert "Sample1" in result.output


def test_handoff_refuses_an_unknown_sample(project: Path, monkeypatch) -> None:
    result = run(project, monkeypatch, "handoff", "nope")

    assert result.exit_code != 0
    assert "No sample" in result.output


def test_handoff_json_carries_the_same_text(project: Path, monkeypatch) -> None:
    result = run(project, monkeypatch, "handoff", "Sample1", "--json")

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["sample"] == "Sample1"
    assert "## Running nrw here" in payload["markdown"]
