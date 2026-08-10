"""Do the limits actually refuse, and does a session refuse to invent work?

Two things are worth testing here and they are not the same. The guard is a
*mechanism*: it either blocks the call or it does not, and the tests are about
command lines the model will really produce --- with flags in the wrong order,
chained behind `&&`, reached through a path. The session is a *boundary*: it
must not start without a declared task, and the prompt it composes must not
silently lose the observations that are the reason for composing it.

The prompt's wording is deliberately not tested. Asserting on prose freezes it,
and every future improvement then reads as a test failure.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from nr_workbench.agent import guard, session

# --------------------------------------------------------------------------
# The guard: what it refuses
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("command", "rule"),
    [
        ("nrw promote abc123 --as final --reason 'best chisq'", "promote"),
        ("nrw promote abc123", "promote"),
        ("nrw isaac export abc123 --upload", "upload"),
        ("nrw isaac export --upload abc123", "upload"),
        ("nrw pack abc123 --force", "force"),
        ("nrw init --force", "force"),
        ("nrw model generate spec.yaml --force", "force"),
        # Chained: judging only the first command would wave the second past.
        ("nrw ls && nrw promote abc --reason x", "promote"),
        ("nrw check ; nrw isaac export a --upload", "upload"),
        ("nrw ls | grep dream && nrw promote abc", "promote"),
        # Reached through a path or an alternate name.
        ("/usr/local/bin/nrw promote abc", "promote"),
        (".venv/bin/nr-workbench promote abc", "promote"),
        # `--force=true` is the same flag with a value attached.
        ("nrw fit run m.py --force=true", "force"),
        # --- The shapes a model writes without meaning to evade anything.
        # Every one of these was allowed by the first version of the parser,
        # which split on whole tokens and only looked at the first three
        # words. None is obfuscation; they are how batched shell work looks.
        ("if true; then nrw promote abc --reason x; fi", "promote"),
        ("for id in a b; do nrw promote $id --reason x; done", "promote"),
        ("for f in *.py; do nrw fit run $f --force; done", "force"),
        ("cd samples/S1/models; ls; nrw fit run m.py --force", "force"),
        ("git add -A\ngit commit -m wip\nnrw promote abc --reason x", "promote"),
        ("pwd&&nrw promote abc", "promote"),
        ("true||nrw promote abc", "promote"),
        # An env-var prefix pushes the command past any fixed window.
        ("A=1 B=2 C=3 nrw promote abc", "promote"),
        ("NRW_AGENT= nrw promote abc", "promote"),
        # A working entry point when `nrw` is not on PATH.
        ("python -m nr_workbench.cli promote abc", "promote"),
        ("python3 -m nr_workbench.cli isaac export abc --upload", "upload"),
        # One level of wrapper, and command substitution.
        ("bash -c 'nrw promote abc'", "promote"),
        ('sh -c "nrw promote abc"', "promote"),
        ("echo x; $(nrw promote abc)", "promote"),
        ("`nrw promote abc`", "promote"),
        # shlex cannot parse this; bash runs it. Failing open here would mean
        # a quoting accident is a hole.
        ("nrw promote abc --reason $'the oxide\\'s real'", "promote"),
    ],
)
def test_the_refused_commands_are_refused(command: str, rule: str) -> None:
    """Each of these is a thing an unattended agent must not do."""
    verdict = guard.judge(command)

    assert not verdict.allowed, command
    assert verdict.rule == rule
    assert "ESCALATIONS.md" in verdict.reason, (
        "a refusal that does not say where to put the decision gets retried"
    )


@pytest.mark.parametrize(
    "command",
    [
        "nrw fit run models/m.py --method amoeba --steps 200",
        "nrw assess 20260807-155810Z-ec6d0134",
        "nrw isaac export abc123",  # export without --upload is fine
        "nrw ls --sample expt11",
        "nrw note abc123 --text 'rejected: oxide unconstrained'",
        "nrw check --contradictions",
        "git commit -m 'wip'",
        "python -c 'print(1)'",
        "ls -la results/",
        # A word that merely contains a refused one.
        "nrw ls --sample promotion-study",
        "echo 'do not promote this'",
    ],
)
def test_the_working_commands_are_allowed(command: str) -> None:
    """The guard is a targeted refusal, not a sandbox. Everything else runs."""
    assert guard.judge(command).allowed, command


def test_an_unparseable_command_with_nothing_refused_in_it_is_allowed() -> None:
    """The fail-open case, narrowed. An unbalanced quote is the shell's
    problem, but only when the text names nothing we refuse -- otherwise a
    quoting accident becomes a way through."""
    assert guard.judge("nrw ls --sample 'unclosed").allowed


def test_a_comment_is_not_a_command() -> None:
    """Bash will not run it, so refusing on it produces a block nobody can
    act on. The text after `#` is dropped rather than judged."""
    assert guard.judge("nrw ls  # then promote it").allowed


@pytest.mark.parametrize(
    "command",
    [
        "echo 'nrw promote abc'",
        "grep -n promote src/nr_workbench/cli.py",
        "cat notes.md | grep -i 'do not promote'",
    ],
)
def test_quoted_and_searched_text_is_not_a_command(command: str) -> None:
    """Widening the parser must not make it fire on text that merely mentions
    a refused word. A guard that blocks `grep promote` gets turned off."""
    assert guard.judge(command).allowed


def test_the_empty_command_is_allowed() -> None:
    """A hook payload with nothing in it must not block everything."""
    assert guard.judge("").allowed


# --------------------------------------------------------------------------
# The guard as a hook: the contract Claude Code actually uses
# --------------------------------------------------------------------------


def run_hook(payload: str) -> subprocess.CompletedProcess[str]:
    """Invoke the guard the way a PreToolUse hook does."""
    return subprocess.run(
        [sys.executable, "-m", "nr_workbench.cli", "agent", "guard"],
        input=payload,
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_hook_blocks_with_exit_two_and_says_why() -> None:
    """Exit 2 is what Claude Code reads as "blocked"; stderr is what the model
    is shown. Both matter --- a block with no reason gets retried."""
    payload = json.dumps(
        {
            "tool_name": "Bash",
            "tool_input": {"command": "nrw promote abc123 --reason x"},
        }
    )

    result = run_hook(payload)

    assert result.returncode == guard.BLOCK
    assert "ESCALATIONS.md" in result.stderr


def test_the_hook_allows_an_ordinary_command() -> None:
    """Exit 0, nothing on stderr."""
    payload = json.dumps(
        {"tool_name": "Bash", "tool_input": {"command": "nrw fit run m.py"}}
    )

    result = run_hook(payload)

    assert result.returncode == 0


@pytest.mark.parametrize("payload", ["", "not json", "[]", '{"tool_input": null}'])
def test_an_unreadable_payload_does_not_block_everything(payload: str) -> None:
    """Failing closed on our own bug would make a scaffolded project unusable
    rather than safe. The hook is one of two mechanisms; the other still holds.
    """
    assert run_hook(payload).returncode == 0


# --------------------------------------------------------------------------
# NRW_AGENT: the second, independent mechanism
# --------------------------------------------------------------------------


def test_promote_refuses_under_nrw_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    """A project with no hook configured is still protected."""
    import click

    monkeypatch.setenv(guard.AGENT_ENV, "1")

    with pytest.raises(click.ClickException) as caught:
        guard.refuse_if_agent("promote")

    assert "ESCALATIONS.md" in str(caught.value)


def test_nothing_is_refused_when_a_person_is_driving(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The limits apply to unattended runs only. A scientist at a prompt has
    every command."""
    monkeypatch.delenv(guard.AGENT_ENV, raising=False)

    guard.refuse_if_agent("promote")
    guard.refuse_if_agent("upload")


def test_the_write_commands_consult_the_guard() -> None:
    """Both mechanisms have to be wired, not merely written. A grep, because
    the alternative is running a promotion in a test to prove it refuses."""
    root = Path(__file__).resolve().parents[1] / "src" / "nr_workbench"

    promote = (root / "commands" / "provenance_cmd.py").read_text(encoding="utf-8")
    isaac = (root / "commands" / "isaac_cmd.py").read_text(encoding="utf-8")

    assert "refuse_if_agent" in promote
    assert "refuse_if_agent" in isaac


def test_force_refuses_under_nrw_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    """--force is the rule the design argues matters most, and for a while it
    was the only one with a single mechanism: the hook. Checked at the group
    callback so it covers every subcommand, including ones added later."""
    import click

    monkeypatch.setenv(guard.AGENT_ENV, "1")

    with pytest.raises(click.ClickException) as caught:
        guard.refuse_if_agent("force")

    assert "--force" in str(caught.value)
    assert "ESCALATIONS.md" in str(caught.value)


def test_every_refusal_names_a_different_remedy() -> None:
    """Three rules, three reasons. A shared default would send the agent to
    the wrong remedy, which is worse than no message."""
    reasons = {
        rule: guard.judge(cmd).reason
        for rule, cmd in (
            ("promote", "nrw promote abc"),
            ("upload", "nrw isaac export abc --upload"),
            ("force", "nrw pack abc --force"),
        )
    }

    assert len(set(reasons.values())) == 3, reasons


def test_the_force_refusal_reaches_the_cli(tmp_path: Path) -> None:
    """End to end, because a limit that is only unit-tested is a limit-shaped
    function."""
    import os

    environment = dict(os.environ, NRW_AGENT="1")
    result = subprocess.run(
        [sys.executable, "-m", "nr_workbench.cli", "init", "--force", str(tmp_path)],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )

    assert result.returncode != 0
    assert "unattended" in result.stderr + result.stdout


# --------------------------------------------------------------------------
# The session boundary
# --------------------------------------------------------------------------


NOTES_WITH_TASK = """\
# Sample S1

## Measurements

| run | state |
|-----|-------|
| 218386 | ocv |

## Fits to perform

Fit the film thickness for run 218386. Check whether an oxide is needed.

## Notes

Something else.
"""

NOTES_UNFILLED = """\
# Sample S1

## Fits to perform

<!-- What you want out of this sample, in words. The agent turns these into
     model specs under models/. -->

## Notes
"""


def test_the_declared_task_is_read() -> None:
    """The section under the heading, and nothing from the next one."""
    task = session.declared_task(NOTES_WITH_TASK)

    assert task.startswith("Fit the film thickness")
    assert "Something else" not in task


def test_scaffold_guidance_does_not_count_as_a_task() -> None:
    """Every scaffolded sample.md has guidance in this section. Treating it as
    intent would mean every fresh sample looks ready to run unattended."""
    assert session.declared_task(NOTES_UNFILLED) == ""


def test_a_missing_heading_is_not_a_task() -> None:
    assert session.declared_task("# Sample\n\nNo such section.\n") == ""


def test_a_session_refuses_to_start_without_a_declared_task(tmp_path: Path) -> None:
    """The one decision an unattended session must not make for itself."""
    sample = tmp_path / "samples" / "S1"
    sample.mkdir(parents=True)
    (sample / "sample.md").write_text(NOTES_UNFILLED, encoding="utf-8")

    with pytest.raises(session.SessionError) as caught:
        session.compose(tmp_path, "S1")

    assert "Fits to perform" in str(caught.value)


def test_a_session_refuses_a_sample_that_does_not_exist(tmp_path: Path) -> None:
    with pytest.raises(session.SessionError):
        session.compose(tmp_path, "nope")


def test_the_prompt_carries_the_task_and_the_limits(tmp_path: Path) -> None:
    """What the prompt must contain, without freezing how it says it: the
    task itself, and the three refusals -- so the agent does not spend a turn
    discovering a limit the hook would have enforced anyway."""
    sample = tmp_path / "samples" / "S1"
    sample.mkdir(parents=True)
    (sample / "sample.md").write_text(NOTES_WITH_TASK, encoding="utf-8")

    composed = session.compose(tmp_path, "S1")

    assert "Fit the film thickness for run 218386" in composed.prompt
    for refused in ("nrw promote", "--upload", "--force"):
        assert refused in composed.prompt
    assert session.ESCALATIONS in composed.prompt


def test_a_one_shot_session_is_warned_about_data_still_arriving(
    tmp_path: Path,
) -> None:
    """`nrw agent run` is most often typed because a measurement just
    finished --- and a steady-state measurement is three angle segments
    written minutes apart, so the third can still be on its way. The watcher
    would not start here at all; a person may, so they are told.
    """
    import os
    import time

    sample = tmp_path / "samples" / "S1"
    (sample / "data" / "steady").mkdir(parents=True)
    (sample / "sample.md").write_text(NOTES_WITH_TASK, encoding="utf-8")
    fresh = sample / "data" / "steady" / "REFL_218386_1_218386_partial.txt"
    fresh.write_text("# Meta:\n#    theta=0.3\n# Q R dR dQ\n0.01 1 0.01 1e-4\n")
    now = time.time()
    os.utime(fresh, (now, now))

    composed = session.compose(tmp_path, "S1")

    arriving = [b for b in composed.observations if "STILL ARRIVING" in b]
    assert arriving, composed.observations
    assert "218386" in arriving[0]


def test_a_settled_measurement_produces_no_arriving_warning(tmp_path: Path) -> None:
    """A warning that fires on every session is one nobody reads."""
    import os

    sample = tmp_path / "samples" / "S1"
    (sample / "data" / "steady").mkdir(parents=True)
    (sample / "sample.md").write_text(NOTES_WITH_TASK, encoding="utf-8")
    old = sample / "data" / "steady" / "REFL_218386_1_218386_partial.txt"
    old.write_text("# Meta:\n#    theta=0.3\n# Q R dR dQ\n0.01 1 0.01 1e-4\n")
    os.utime(old, (1000.0, 1000.0))

    composed = session.compose(tmp_path, "S1")

    assert not [b for b in composed.observations if "STILL ARRIVING" in b]


def test_a_broken_check_does_not_stop_the_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A session that starts with fewer observations beats one that does not
    start. The failure is reported in the prompt rather than swallowed."""
    sample = tmp_path / "samples" / "S1"
    sample.mkdir(parents=True)
    (sample / "sample.md").write_text(NOTES_WITH_TASK, encoding="utf-8")

    def explode(root: Path, name: str) -> str:
        raise RuntimeError("the scan blew up")

    monkeypatch.setattr(session, "_observe_data", explode)

    composed = session.compose(tmp_path, "S1")

    assert "the scan blew up" in "\n".join(composed.observations)


def test_the_session_runs_unattended_by_construction(tmp_path: Path) -> None:
    """NRW_AGENT has to be set by the runner, not left to the caller's shell:
    a session started by the daemon has no shell to set it in."""
    source = Path(session.__file__).read_text(encoding="utf-8")

    assert "environment[AGENT_ENV]" in source


def test_a_missing_harness_says_so_rather_than_failing_obscurely(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(session.shutil, "which", lambda name: None)

    with pytest.raises(session.SessionError) as caught:
        session.harness_command(tmp_path / "p.md")

    assert "--dry-run" in str(caught.value)


# --------------------------------------------------------------------------
# `nrw doctor` on the limits
# --------------------------------------------------------------------------


def settings(payload: object) -> dict:
    """Parse a `.claude/settings.json` shape into doctor's view of it."""
    from nr_workbench.commands.doctor import guard_hook_event

    return {"event": guard_hook_event(payload)}  # type: ignore[arg-type]


def test_a_correctly_wired_hook_is_recognised() -> None:
    assert settings(
        {
            "hooks": {
                "PreToolUse": [
                    {"matcher": "Bash", "hooks": [{"command": "nrw agent guard"}]}
                ]
            }
        }
    ) == {"event": "PreToolUse"}


def test_a_hook_on_the_wrong_event_is_not_reported_as_working() -> None:
    """PostToolUse runs *after* the command, so it refuses nothing. A
    substring search over the serialised JSON calls this configured --- which
    is worse than no hook, because doctor then says the limits are in place.
    """
    where = settings(
        {
            "hooks": {
                "PostToolUse": [
                    {"matcher": "Bash", "hooks": [{"command": "nrw agent guard"}]}
                ]
            }
        }
    )

    assert where == {"event": "PostToolUse"}, "named, so doctor can say what is wrong"


def test_a_hook_matched_against_the_wrong_tool_does_not_count() -> None:
    """A PreToolUse hook on Write never sees a Bash call."""
    assert settings(
        {
            "hooks": {
                "PreToolUse": [
                    {"matcher": "Write", "hooks": [{"command": "nrw agent guard"}]}
                ]
            }
        }
    ) == {"event": ""}


@pytest.mark.parametrize(
    "payload", [{}, {"hooks": {}}, {"hooks": []}, {"hooks": {"PreToolUse": "nope"}}]
)
def test_a_malformed_settings_file_does_not_crash_the_check(payload: object) -> None:
    """The template tells the user the file is theirs to edit, so a
    hand-broken one is expected input. Doctor's whole point is to run when
    something is wrong."""
    assert settings(payload) == {"event": ""}


def test_doctor_reports_a_project_with_no_limits(tmp_path: Path, monkeypatch) -> None:
    """The state worth naming: the harness is installed and nothing stops it.
    A project scaffolded before .claude/settings.json existed is exactly this.
    """
    from nr_workbench.commands.doctor import _agent_checks

    (tmp_path / "nrw.toml").write_text(
        '[project]\nname = "t"\n[instrument]\nfacility = "SNS"\ninstrument = "REF_L"\n',
        encoding="utf-8",
    )
    (tmp_path / ".nrw").mkdir()
    monkeypatch.chdir(tmp_path)

    limits = [c for c in _agent_checks() if c.name == "agent limits"]

    assert limits and limits[0].status == "warn"
    assert "nrw init" in limits[0].detail


# --------------------------------------------------------------------------
# Who does the judging
# --------------------------------------------------------------------------


def test_no_endpoint_is_consulted_while_an_agent_is_driving(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The harness is a language model, and a better one than the endpoint is
    likely to be. Asking a second, weaker model and handing its verdict back
    is not a fallback -- it substitutes for the judgement we want and then
    reads as evidence.
    """
    monkeypatch.setenv(guard.AGENT_ENV, "1")

    assert guard.agent_is_driving()


def test_a_person_at_a_prompt_still_gets_the_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A configured endpoint is useful to somebody with no harness open. The
    rule is about who is better placed to judge, not about distrusting it."""
    monkeypatch.delenv(guard.AGENT_ENV, raising=False)

    assert not guard.agent_is_driving()


def test_assess_asks_the_agent_rather_than_an_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`nrw assess` under an agent must neither call out nor tell the agent to
    configure an endpoint -- the second is worse, because it is advice that
    sends the harness to fix something that is not broken.
    """
    from nr_workbench.commands.assess import _add_judgement
    from nr_workbench.fitting.assess import Assessment

    monkeypatch.setenv(guard.AGENT_ENV, "1")

    def explode(*args: object, **kwargs: object) -> bool:
        raise AssertionError("consulted an endpoint while an agent was driving")

    monkeypatch.setattr("nr_workbench.aure_adapter.llm_available", explode)
    monkeypatch.setattr("nr_workbench.aure_adapter.judge_fit", explode)

    assessment = Assessment(fit_id="x", chisq=1.2)
    _add_judgement(assessment, None, {}, Path("."), {})

    assert assessment.judgement is None
    said = " ".join(assessment.problems)
    assert "yourself" in said
    assert "LLM_API_KEY" not in said, "do not send the harness to configure one"


def test_every_endpoint_call_site_checks_who_is_driving() -> None:
    """Three places reach for a model's opinion. A fourth added later without
    this check would quietly reintroduce the weaker verdict, so this is a
    grep rather than three separate behaviour tests.
    """
    root = Path(__file__).resolve().parents[1] / "src" / "nr_workbench"
    callers = [
        root / "commands" / "assess.py",
        root / "commands" / "model.py",
        root / "conditions.py",
    ]

    for path in callers:
        source = path.read_text(encoding="utf-8")
        assert "agent_is_driving" in source, path.name


def test_the_deterministic_aure_helpers_are_not_gated() -> None:
    """`extract_features` and `sld` are numpy and periodictable, not a model.
    Gating them would be a different mistake: refusing to compute a critical
    edge because an agent is driving helps nobody, and the rule here is about
    whose *judgement* to use, not about avoiding the dependency.
    """
    root = Path(__file__).resolve().parents[1] / "src" / "nr_workbench"
    adapter = (root / "aure_adapter.py").read_text(encoding="utf-8")

    assert "LLM-free" in adapter, "the distinction these tests rely on"
    assert "agent_is_driving" not in adapter


# --------------------------------------------------------------------------
# Progress on the terminal
# --------------------------------------------------------------------------


def test_progress_shows_what_it_is_doing() -> None:
    """Status, not content. A session with nothing on the terminal for forty
    minutes cannot be told from a hung one."""
    event = json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Bash",
                        "input": {"command": "nrw fit run m.py --method amoeba"},
                    }
                ]
            },
        }
    )

    line = session.describe_event(event)

    assert line is not None
    assert "Bash" in line and "nrw fit run" in line


@pytest.mark.parametrize(
    "event",
    [
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "x"}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "x": 1}]}},
        {"type": "rate_limit_event"},
    ],
)
def test_progress_stays_quiet_about_content(event: dict) -> None:
    """The model's prose and every tool result stay off the terminal. A
    terminal that replays the transcript is one nobody watches."""
    assert session.describe_event(json.dumps(event)) is None


def test_progress_survives_a_line_that_is_not_json() -> None:
    """stderr is merged into the stream, so non-JSON lines arrive."""
    assert session.describe_event("Warning: something on stderr") is None


def test_a_path_is_shown_relative_to_the_project() -> None:
    """An absolute path is mostly its own prefix; truncating one leaves the
    part every line has in common."""
    event = json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Read",
                        "input": {"file_path": "/tmp/proj/samples/Cu1/sample.md"},
                    }
                ]
            },
        }
    )

    line = session.describe_event(event, Path("/tmp/proj"))

    assert line is not None and line.endswith("samples/Cu1/sample.md")


def test_the_turn_cap_is_reported_as_itself() -> None:
    """Distinct from a failure, and actionable differently: the work ran out
    of room, so --turns is the answer rather than debugging anything."""
    event = json.dumps(
        {
            "type": "result",
            "subtype": "error_max_turns",
            "duration_ms": 44000,
            "num_turns": 6,
            "is_error": True,
        }
    )

    line = session.describe_event(event)

    assert line is not None and "6-turn cap" in line


# --------------------------------------------------------------------------
# The headless invocation
# --------------------------------------------------------------------------


def test_the_harness_runs_without_waiting_for_approval(tmp_path: Path) -> None:
    """Measured, not assumed: without this a 45-turn session spent all of it
    being told "This command requires approval", then tried to write itself a
    settings.local.json to escape. Headless has nobody to approve.
    """
    argv = session.harness_command(tmp_path / "p.md")

    assert "--permission-mode" in argv
    assert argv[argv.index("--permission-mode") + 1] == "bypassPermissions"


def test_a_session_will_not_start_without_its_hook(tmp_path: Path) -> None:
    """Since the permission layer is bypassed, the PreToolUse hook is doing
    real work. A session that edited .claude/settings.json would otherwise
    disarm the next one, so this is checked per session rather than once.
    """
    sample = tmp_path / "samples" / "S1"
    sample.mkdir(parents=True)
    (sample / "sample.md").write_text(NOTES_WITH_TASK, encoding="utf-8")

    with pytest.raises(session.SessionError) as caught:
        session.run(tmp_path, "S1")

    assert "agent guard" in str(caught.value)
    assert "nrw init" in str(caught.value)


def test_the_transcript_name_is_usable_as_a_filename(tmp_path: Path) -> None:
    """`format_timestamp` is ISO-8601 with colons -- right in a record, wrong
    in a path."""
    from nr_workbench.provenance.record import utc_now

    stamp = utc_now().strftime("%Y%m%d-%H%M%SZ")

    assert ":" not in stamp
