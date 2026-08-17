"""Tests for `nrw check-llm`.

Nothing here makes a real API call. The parts worth testing are the ones that
decide *what a result means* -- a harness that exited without answering, a
gateway that replied with something else, an endpoint that is simply not
configured -- and each of those is reachable by handing the reader the output
a real run would have produced.

The one integration-shaped test drives the whole command through a fake
harness on PATH, because the thing most likely to break is the argv contract
between this and `agent/session.py`, and a mocked `subprocess.run` would not
notice it changing.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from click.testing import CliRunner

from nr_workbench.agent import guard
from nr_workbench.cli import main
from nr_workbench.commands import check_llm

# --------------------------------------------------------------------------
# Reading a finished harness run
# --------------------------------------------------------------------------


def _stream(**overrides: object) -> str:
    """A `stream-json` stream ending in a result event."""
    event = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": check_llm.PROBE_TOKEN,
        "total_cost_usd": 0.0123,
        "modelUsage": {"claude-opus-4-8": {}},
    }
    event.update(overrides)
    return (
        json.dumps({"type": "system", "subtype": "init"})
        + "\n"
        + json.dumps(event)
        + "\n"
    )


def test_read_harness_output_success_reports_the_model_and_cost() -> None:
    probe = check_llm._read_harness_output(_stream(), "", 0, 2.5)

    assert probe.status == "ok"
    assert probe.model == "claude-opus-4-8"
    assert probe.cost_usd == pytest.approx(0.0123)
    assert probe.reply == check_llm.PROBE_TOKEN


def test_read_harness_output_no_result_event_quotes_stderr() -> None:
    """The shape a provider misconfiguration takes.

    Claude Code exits before it reaches the model and says why on stderr; the
    event stream is empty. Reporting only "exited 1" would throw away the one
    line that names the problem.
    """
    probe = check_llm._read_harness_output(
        "", "Error: ANTHROPIC_FOUNDRY_RESOURCE is not set", 1, 0.4
    )

    assert probe.status == "error"
    assert "ANTHROPIC_FOUNDRY_RESOURCE" in probe.detail


def test_read_harness_output_error_event_is_a_failure() -> None:
    probe = check_llm._read_harness_output(
        _stream(is_error=True, result="404 deployment not found"), "", 1, 1.0
    )

    assert probe.status == "error"
    assert "deployment not found" in probe.detail


def test_read_harness_output_wrong_reply_warns_rather_than_passing() -> None:
    """A round-trip that works but mangles the reply is its own failure mode.

    A gateway that rewrites or summarises answers will mangle a session's tool
    calls too, and it is indistinguishable from a healthy setup in every check
    that stops at "did bytes come back".
    """
    probe = check_llm._read_harness_output(
        _stream(result="Sure! Here is your token."), "", 0, 1.0
    )

    assert probe.status == "warn"
    assert check_llm.PROBE_TOKEN in probe.detail


def test_read_harness_output_takes_the_last_result_event() -> None:
    stream = _stream(result="first") + _stream(result=check_llm.PROBE_TOKEN)

    assert check_llm._read_harness_output(stream, "", 0, 1.0).status == "ok"


def test_read_harness_output_survives_non_json_lines() -> None:
    """A wrapper script that prints a banner must not break the reader."""
    stream = "starting my-harness v3\n" + _stream() + "done\n"

    assert check_llm._read_harness_output(stream, "", 0, 1.0).status == "ok"


# --------------------------------------------------------------------------
# What the environment says
# --------------------------------------------------------------------------


def test_selected_provider_names_foundry_when_switched_on() -> None:
    assert (
        check_llm.selected_provider({"CLAUDE_CODE_USE_FOUNDRY": "1"})
        == "microsoft foundry"
    )


def test_selected_provider_ignores_a_switch_set_to_zero() -> None:
    """`CLAUDE_CODE_USE_FOUNDRY=0` means "not this one".

    Reporting it as the selected provider would send someone debugging an
    Azure resource when the call went to api.anthropic.com.
    """
    environment = {"CLAUDE_CODE_USE_FOUNDRY": "0", "ANTHROPIC_API_KEY": "sk-x"}

    assert check_llm.selected_provider(environment) == "anthropic api"


def test_selected_provider_with_nothing_set_says_so() -> None:
    assert "credentials" in check_llm.selected_provider({})


def test_provider_settings_redacts_keys_and_keeps_the_rest() -> None:
    environment = {
        "CLAUDE_CODE_USE_FOUNDRY": "1",
        "ANTHROPIC_FOUNDRY_RESOURCE": "ornl-nr",
        "ANTHROPIC_FOUNDRY_API_KEY": "abcdefghijklmnop",
        "ANTHROPIC_DEFAULT_OPUS_MODEL": "claude-opus-4-8",
    }

    found = check_llm.provider_settings(environment)

    assert found["ANTHROPIC_FOUNDRY_RESOURCE"] == "ornl-nr"
    assert found["ANTHROPIC_DEFAULT_OPUS_MODEL"] == "claude-opus-4-8"
    assert "abcdefghijklmnop" not in json.dumps(found)
    assert "mnop" in found["ANTHROPIC_FOUNDRY_API_KEY"]


def test_provider_settings_omits_what_is_not_set() -> None:
    assert check_llm.provider_settings({}) == {}


# --------------------------------------------------------------------------
# Refusals and skips
# --------------------------------------------------------------------------


def test_the_harness_probe_refuses_to_nest_under_an_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A session running this would start a second harness inside itself.

    Skipped rather than failed: an agent that runs `nrw check-llm` to diagnose
    something should still get the endpoint result and a plain explanation.
    """
    monkeypatch.setenv(guard.AGENT_ENV, "1")

    probe = check_llm.probe_harness()

    assert probe.status == "missing"
    assert guard.AGENT_ENV in probe.detail


def test_an_absent_endpoint_is_normal_but_named_when_asked_for(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`nrw agent run` needs no endpoint, so its absence must not fail a run.

    Asking for it by name is a different question, and the answer is no.
    """
    monkeypatch.setattr("nr_workbench.aure_adapter.llm_available", lambda: False)
    monkeypatch.setattr("nr_workbench.aure_adapter.is_available", lambda: True)

    assert check_llm.probe_endpoint(required=False).status == "missing"
    assert check_llm.probe_endpoint(required=True).status == "error"


def test_the_endpoint_probe_reports_a_failed_call_without_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from nr_workbench import aure_adapter

    def explode(*args: object, **kwargs: object) -> str:
        raise aure_adapter.AureUnavailableError("401 Unauthorized")

    monkeypatch.setattr("nr_workbench.aure_adapter.is_available", lambda: True)
    monkeypatch.setattr("nr_workbench.aure_adapter.llm_available", lambda: True)
    monkeypatch.setattr(
        "nr_workbench.aure_adapter.llm_info",
        lambda: {"provider": "openai", "model": "gpt-4o", "base_url": ""},
    )
    monkeypatch.setattr("nr_workbench.aure_adapter.complete", explode)

    probe = check_llm.probe_endpoint()

    assert probe.status == "error"
    assert "401" in probe.detail


# --------------------------------------------------------------------------
# End to end, against a fake harness
# --------------------------------------------------------------------------


def _fake_harness(directory: Path, *, stream: str, returncode: int = 0) -> Path:
    """A harness on PATH that echoes a fixed stream-json stream.

    Written as a real executable rather than a patched `subprocess.run` so the
    argv contract between this command and `agent/session.py` is actually
    exercised. It asserts on the flags it was given: those are the documented
    contract in `docs/agent.md`, and a probe run with different ones would
    pass while `nrw agent run` failed.
    """
    script = directory / "fake-harness"
    script.write_text(
        textwrap.dedent(
            f"""\
            #!{sys.executable}
            import sys

            argv = sys.argv[1:]
            assert "-p" in argv, argv
            assert "--max-turns" in argv, argv
            assert argv[argv.index("--max-turns") + 1] == "1", argv
            assert "--output-format" in argv, argv
            assert argv[argv.index("--output-format") + 1] == "stream-json", argv

            prompt = argv[argv.index("-p") + 1]
            assert prompt.startswith("@"), prompt
            assert {check_llm.PROBE_TOKEN!r} in open(prompt[1:]).read()

            sys.stdout.write({stream!r})
            sys.exit({returncode})
            """
        ),
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return script


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neither an agent nor a provider configuration from the real machine."""
    monkeypatch.delenv(guard.AGENT_ENV, raising=False)
    for group in check_llm.PROVIDER_VARS.values():
        for name in group:
            monkeypatch.delenv(name, raising=False)


def test_check_llm_reports_a_working_harness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    _fake_harness(tmp_path, stream=_stream())
    monkeypatch.setenv("NRW_HARNESS", "fake-harness")
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")

    result = CliRunner().invoke(main, ["check-llm", "--harness", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    probe = payload["probes"][0]
    assert probe["status"] == "ok"
    assert probe["model"] == "claude-opus-4-8"
    assert probe["cost_usd"] == pytest.approx(0.0123)


def test_check_llm_exits_non_zero_when_the_harness_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    """The whole point of the exit status: this belongs in a pre-beamtime script."""
    _fake_harness(
        tmp_path,
        stream=_stream(is_error=True, result="deployment 'claude-opus-4-8' not found"),
        returncode=1,
    )
    monkeypatch.setenv("NRW_HARNESS", "fake-harness")
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")

    result = CliRunner().invoke(main, ["check-llm", "--harness"])

    assert result.exit_code == 1
    assert "not found" in result.output


def test_check_llm_reports_a_missing_harness_without_traceback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, clean_env: None
) -> None:
    monkeypatch.setenv("NRW_HARNESS", "no-such-harness-anywhere")
    monkeypatch.setenv("PATH", str(tmp_path))

    result = CliRunner().invoke(main, ["check-llm", "--harness"])

    assert result.exit_code == 1
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "no-such-harness-anywhere" in result.output


def test_check_llm_never_prints_a_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    """A diagnostic gets pasted into issues and Slack. It may not carry a key."""
    _fake_harness(tmp_path, stream=_stream())
    monkeypatch.setenv("NRW_HARNESS", "fake-harness")
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("CLAUDE_CODE_USE_FOUNDRY", "1")
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_API_KEY", "super-secret-value-1234")

    result = CliRunner().invoke(main, ["check-llm", "--harness"])

    assert "super-secret-value" not in result.output
    assert "microsoft foundry" in result.output


def test_check_llm_help_does_not_import_heavy_modules() -> None:
    """`nrw check-llm --help` must not pay for the LLM stack either.

    The command's whole subject is language models, which makes it the most
    likely place for a module-scope `import aure` to be added without thinking.
    """
    script = textwrap.dedent(
        """
        import sys
        from click.testing import CliRunner
        from nr_workbench.cli import main

        result = CliRunner().invoke(main, ["check-llm", "--help"])
        assert result.exit_code == 0, result.output

        leaked = [m for m in ("aure", "langchain_core") if m in sys.modules]
        if leaked:
            print("LEAKED:" + ",".join(leaked))
            sys.exit(1)
        """
    )

    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=120
    )

    assert result.returncode == 0, result.stdout
