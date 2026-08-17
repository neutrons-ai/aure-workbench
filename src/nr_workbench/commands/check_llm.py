"""``nrw check-llm`` -- make a real call, and say what answered.

`nrw doctor` reports what is *configured*: the harness binary it found on
PATH, the endpoint variables that are set. Neither of those is a connection. A
Foundry deployment name that was never created, an expired key, a gateway that
resolves but refuses, a daemon whose unit file is missing the provider
variables -- all four look exactly like a working setup right up to the first
request, which during a beamtime is at 2am inside an unattended session that
then does nothing until morning.

This makes the requests. Two of them, because this package talks to two
different kinds of language model and they fail independently:

* the **harness** -- a tool-using loop (``claude`` by default, or whatever
  ``NRW_HARNESS`` names) that ``nrw agent run`` drives. This is the one a
  Microsoft Foundry, Bedrock or Vertex configuration applies to, and the only
  one an unattended session needs.
* the **endpoint** -- the ``LLM_BASE_URL``/``LLM_API_KEY`` completions API AuRE
  uses for ``nrw assess``, ``nrw model new --from-notes`` and ``nrw isaac
  export`` when a person is driving.

They are not substitutes for each other and having only one is a normal state;
:doc:`docs/agent.md` says why at length. So an absent endpoint is reported as
absent rather than as a failure, and only something that was asked for and
did not answer sets the exit status.

**The harness probe costs money.** It is one real turn against whatever model
your configuration resolves to, which on a large model is of the order of ten
cents. That is the point -- a probe against a cheap model would not catch an
undeployed ``ANTHROPIC_DEFAULT_OPUS_MODEL``, which is the failure this exists
to find. The measured cost is reported so it is never a surprise.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import click

from nr_workbench.commands.doctor import STATUS_MARKERS

#: What a working round-trip returns. Short, so a model cannot get it subtly
#: right, and distinctive enough that it will not appear in a refusal or an
#: error page a misconfigured gateway hands back with a 200.
PROBE_TOKEN = "NRW-OK"

#: The whole prompt. It has to work under `--permission-mode bypassPermissions`
#: -- the flag `nrw agent run` uses, and therefore the one worth testing -- so
#: it says plainly that no tool is wanted rather than relying on a permission
#: layer that is switched off.
PROBE_PROMPT = (
    "This is a connectivity check, not a task. Do not use any tool and do not "
    "read or write any file.\n\n"
    f"Reply with exactly {PROBE_TOKEN} and nothing else."
)

#: Seconds before the harness probe is given up on. Generous: a cold harness
#: start behind a corporate proxy is slow, and a probe that times out on a
#: working setup is worse than one that takes a minute.
DEFAULT_TIMEOUT = 180

#: Environment variables that select and configure the harness's provider,
#: grouped by the provider they belong to. Reported alongside the result
#: because "it failed" is not actionable without "and this is what it tried".
#:
#: These names come from Claude Code's own documentation, which is the
#: authority and will move first -- see the links in `docs/agent.md`. An
#: unrecognised variable costs nothing here; a stale one only means a line is
#: missing from a diagnostic.
PROVIDER_VARS: dict[str, tuple[str, ...]] = {
    "microsoft foundry": (
        "CLAUDE_CODE_USE_FOUNDRY",
        "ANTHROPIC_FOUNDRY_RESOURCE",
        "ANTHROPIC_FOUNDRY_BASE_URL",
        "ANTHROPIC_FOUNDRY_API_KEY",
    ),
    "amazon bedrock": (
        "CLAUDE_CODE_USE_BEDROCK",
        "AWS_REGION",
        "AWS_PROFILE",
        "ANTHROPIC_BEDROCK_BASE_URL",
        "CLAUDE_CODE_SKIP_BEDROCK_AUTH",
    ),
    "google vertex": (
        "CLAUDE_CODE_USE_VERTEX",
        "CLOUD_ML_REGION",
        "ANTHROPIC_VERTEX_PROJECT_ID",
        "ANTHROPIC_VERTEX_BASE_URL",
        "CLAUDE_CODE_SKIP_VERTEX_AUTH",
    ),
    "anthropic api": (
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
    ),
}

#: Set whichever provider is in play, so they are reported either way. The
#: model pins matter most on Foundry, where an alias that is not deployed in
#: your account fails at the first request rather than at launch.
SHARED_VARS = (
    "ANTHROPIC_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "ANTHROPIC_SMALL_FAST_MODEL",
    "HTTPS_PROXY",
    "NRW_HARNESS",
)

#: Variables whose value must never be printed. Matched by name because a
#: provider's key variable is not in `nr_workbench.env.SECRET_VARS` -- that
#: list is about the AuRE endpoint, and these are about the harness.
SECRET_SUFFIXES = ("_API_KEY", "_AUTH_TOKEN", "_ACCESS_TOKEN", "_SECRET")


@dataclass
class Probe:
    """One live call, and what came back.

    Attributes:
        name: ``harness`` or ``endpoint``.
        status: ``ok``, ``warn``, ``error``, or ``missing`` when nothing was
            configured to call.
        detail: One human-readable line.
        model: What actually answered, where the transport reports it. On a
            third-party provider this is the deployment name, which is the
            single most useful thing a probe can return.
        seconds: Wall-clock time for the round-trip.
        cost_usd: What the call cost, where the transport reports it.
        reply: The text that came back, trimmed.
    """

    name: str
    status: str
    detail: str
    model: str = ""
    seconds: float | None = None
    cost_usd: float | None = None
    reply: str = ""

    def as_dict(self) -> dict[str, Any]:
        """Return the JSON form."""
        return {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "model": self.model or None,
            "seconds": round(self.seconds, 2) if self.seconds is not None else None,
            "cost_usd": self.cost_usd,
            "reply": self.reply or None,
        }


@dataclass
class Report:
    """Everything one run of the command found.

    Attributes:
        provider: The harness provider the environment selects.
        settings: Provider variables that are set, with secrets redacted.
        probes: The calls that were made.
    """

    provider: str
    settings: dict[str, str] = field(default_factory=dict)
    probes: list[Probe] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """Return the JSON form."""
        return {
            "provider": self.provider,
            "settings": self.settings,
            "probes": [probe.as_dict() for probe in self.probes],
        }


def selected_provider(environment: dict[str, str] | None = None) -> str:
    """Which provider the harness will use, from the environment alone.

    The three ``CLAUDE_CODE_USE_*`` switches are checked in the order Claude
    Code documents them. With none set, the harness uses its own credentials --
    an API key, or a subscription logged in interactively -- and this cannot
    tell those apart without asking it, which is what the probe does.

    Args:
        environment: Variables to read. Defaults to the real environment.

    Returns:
        A provider label for display.
    """
    env = os.environ if environment is None else environment

    for label, switch in (
        ("microsoft foundry", "CLAUDE_CODE_USE_FOUNDRY"),
        ("amazon bedrock", "CLAUDE_CODE_USE_BEDROCK"),
        ("google vertex", "CLAUDE_CODE_USE_VERTEX"),
    ):
        if _is_on(env.get(switch)):
            return label

    if env.get("ANTHROPIC_API_KEY") or env.get("ANTHROPIC_AUTH_TOKEN"):
        return "anthropic api"
    return "the harness's own credentials"


def _is_on(value: str | None) -> bool:
    """Whether a ``CLAUDE_CODE_USE_*`` switch is enabled.

    ``0`` and ``false`` are treated as off. Claude Code's own parsing is what
    counts, but a variable explicitly set to ``0`` almost certainly means "not
    this one", and reporting it as the selected provider would send someone
    debugging the wrong thing.
    """
    return bool(value) and str(value).strip().lower() not in {"0", "false", "no"}


def provider_settings(environment: dict[str, str] | None = None) -> dict[str, str]:
    """The provider variables that are set, with secrets redacted.

    Args:
        environment: Variables to read. Defaults to the real environment.

    Returns:
        Variable name to value, in the order of :data:`PROVIDER_VARS` then
        :data:`SHARED_VARS`. Absent variables are omitted rather than shown
        empty -- the list is long and the set ones are the story.
    """
    from nr_workbench.env import redact

    env = os.environ if environment is None else environment
    names = [name for group in PROVIDER_VARS.values() for name in group]
    names += list(SHARED_VARS)

    found: dict[str, str] = {}
    for name in names:
        value = env.get(name)
        if not value:
            continue
        found[name] = redact(value) if name.endswith(SECRET_SUFFIXES) else str(value)
    return found


# --------------------------------------------------------------------------
# The harness
# --------------------------------------------------------------------------


def probe_harness(*, model: str | None = None, timeout: int = DEFAULT_TIMEOUT) -> Probe:
    """Run one turn of the harness and report what answered.

    Deliberately invoked through :func:`nr_workbench.agent.session.harness_command`
    rather than with a hand-written argv, so this tests the exact invocation
    ``nrw agent run`` uses -- including ``NRW_HARNESS`` and the documented
    flag contract a site's own wrapper has to meet. A probe that passed while
    the real thing failed would be worse than no probe.

    Runs in a scratch directory, so no project ``CLAUDE.md``, settings file or
    hook is loaded and nothing it might do can land in the project.

    Args:
        model: Model or deployment name to test, or ``None`` for whatever the
            environment's pinning resolves to.
        timeout: Seconds before giving up.

    Returns:
        The probe result. Never raises for a failed call -- a diagnostic that
        traces back is not a diagnostic.
    """
    import subprocess
    import tempfile
    import time

    from nr_workbench.agent.guard import AGENT_ENV, agent_is_driving
    from nr_workbench.agent.session import SessionError, harness_command

    if agent_is_driving():
        return Probe(
            "harness",
            "missing",
            f"{AGENT_ENV} is set, so a harness is already running this. "
            "Probing would start a second one inside the first; run this from "
            "your own shell instead.",
        )

    with tempfile.TemporaryDirectory(prefix="nrw-check-llm-") as scratch:
        prompt_file = Path(scratch) / "probe.md"
        prompt_file.write_text(PROBE_PROMPT, encoding="utf-8")
        try:
            argv = harness_command(prompt_file, turns=1, model=model)
        except SessionError as exc:
            # A missing harness reported as itself: `harness_command` already
            # explains what to install and how to point at your own.
            return Probe("harness", "error", str(exc))

        started = time.monotonic()
        try:
            completed = subprocess.run(  # noqa: S603 - argv built from resolve_harness
                argv,
                cwd=scratch,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return Probe(
                "harness",
                "error",
                f"no answer within {timeout}s. A cold start behind a proxy is "
                "slow, but this is usually an endpoint that accepts the "
                "connection and never replies -- check HTTPS_PROXY and the "
                "provider's base URL. --timeout raises the limit.",
                seconds=time.monotonic() - started,
            )
        except OSError as exc:
            return Probe("harness", "error", f"could not start {argv[0]}: {exc}")

        seconds = time.monotonic() - started
        # stderr is where a provider misconfiguration says what it is; the
        # events go to stdout, so both are needed and neither replaces the other.
        return _read_harness_output(
            completed.stdout, completed.stderr, completed.returncode, seconds
        )


def _read_harness_output(
    stdout: str, stderr: str, returncode: int, seconds: float
) -> Probe:
    """Turn a finished harness run into a probe result.

    Split out from :func:`probe_harness` because this is the part with cases in
    it, and the part a test can drive without spending a real API call.

    Args:
        stdout: The harness's ``stream-json`` events.
        stderr: Anything it wrote to standard error.
        returncode: Its exit status.
        seconds: Wall-clock time for the call.

    Returns:
        The probe result.
    """
    event = _result_event(stdout)

    if event is None:
        # No result event at all: the harness died before it reached the model,
        # which is what a bad provider configuration usually looks like.
        detail = _tail(stderr) or _tail(stdout) or "it printed nothing"
        return Probe(
            "harness",
            "error",
            f"exited {returncode} without answering: {detail}",
            seconds=seconds,
        )

    reply = str(event.get("result") or "").strip()
    model = ", ".join(sorted(event.get("modelUsage") or {}))
    cost = event.get("total_cost_usd")
    cost_usd = float(cost) if isinstance(cost, int | float) else None

    if event.get("is_error") or returncode != 0:
        return Probe(
            "harness",
            "error",
            f"the call failed: {_tail(reply) or _tail(stderr) or 'no reason given'}",
            model=model,
            seconds=seconds,
            cost_usd=cost_usd,
            reply=_tail(reply),
        )

    if PROBE_TOKEN not in reply:
        # A round-trip happened, so the transport works. Worth a line rather
        # than a pass: a gateway that rewrites or summarises replies is a real
        # configuration, and it will mangle a session's tool calls too.
        return Probe(
            "harness",
            "warn",
            f"answered, but not with {PROBE_TOKEN} -- something between here "
            "and the model may be rewriting replies",
            model=model,
            seconds=seconds,
            cost_usd=cost_usd,
            reply=_tail(reply),
        )

    return Probe(
        "harness",
        "ok",
        "answered",
        model=model,
        seconds=seconds,
        cost_usd=cost_usd,
        reply=reply,
    )


def _result_event(stdout: str) -> dict[str, Any] | None:
    """The final ``result`` event out of a ``stream-json`` stream, or None.

    The last one, not the first: the stream carries a line per event and only
    the terminal one reports cost, model usage and whether it errored.
    """
    found: dict[str, Any] | None = None
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(event, dict) and event.get("type") == "result":
            found = event
    return found


#: How much of a failure message to quote. Enough for a stack trace's last
#: useful line; not enough to turn a diagnostic into a transcript.
TAIL_CHARS = 400


def _tail(text: str) -> str:
    """The informative end of an error message, flattened to one line."""
    flat = " ".join((text or "").split())
    if len(flat) <= TAIL_CHARS:
        return flat
    return "…" + flat[-TAIL_CHARS:]


# --------------------------------------------------------------------------
# The endpoint
# --------------------------------------------------------------------------


def probe_endpoint(*, required: bool = False) -> Probe:
    """Send one completion to the AuRE endpoint and report what answered.

    Args:
        required: Whether an unconfigured endpoint is a failure. True when the
            user asked for this probe by name -- testing something that is not
            set up is a question with an answer, and the answer is no.

    Returns:
        The probe result.
    """
    import time

    from nr_workbench import aure_adapter
    from nr_workbench.env import load_env

    load_env()

    if not aure_adapter.is_available():
        return Probe(
            "endpoint",
            "error" if required else "missing",
            "AuRE is not installed, so there is no endpoint client. "
            "`pip install -e .` pulls it from git.",
        )

    if not aure_adapter.llm_available():
        return Probe(
            "endpoint",
            "error" if required else "missing",
            "no endpoint configured. Set LLM_PROVIDER and LLM_API_KEY, or "
            "LLM_BASE_URL for an OpenAI-compatible one. Nothing an unattended "
            "session does needs this.",
        )

    info = aure_adapter.llm_info()
    where = f"{info.get('provider')}/{info.get('model')}"
    if info.get("base_url"):
        where += f" @ {info['base_url']}"

    started = time.monotonic()
    try:
        reply = aure_adapter.complete(
            "You are a connectivity check. Reply with exactly what is asked "
            "for and nothing else.",
            f"Reply with exactly {PROBE_TOKEN} and nothing else.",
        )
    except aure_adapter.AureUnavailableError as exc:
        return Probe(
            "endpoint",
            "error",
            _tail(str(exc)),
            model=where,
            seconds=time.monotonic() - started,
        )

    seconds = time.monotonic() - started
    trimmed = reply.strip()
    if PROBE_TOKEN not in trimmed:
        return Probe(
            "endpoint",
            "warn",
            f"answered, but not with {PROBE_TOKEN}",
            model=where,
            seconds=seconds,
            reply=_tail(trimmed),
        )
    return Probe(
        "endpoint", "ok", "answered", model=where, seconds=seconds, reply=trimmed
    )


# --------------------------------------------------------------------------
# The command
# --------------------------------------------------------------------------


def collect(
    *,
    harness: bool,
    endpoint: bool,
    explicit: bool,
    model: str | None,
    timeout: int,
) -> Report:
    """Run the requested probes.

    Args:
        harness: Probe the coding harness.
        endpoint: Probe the AuRE completions endpoint.
        explicit: Whether the caller named which probes to run, which makes an
            unconfigured one a failure rather than a normal absence.
        model: Model or deployment name for the harness probe.
        timeout: Seconds allowed for the harness probe.

    Returns:
        The report.
    """
    report = Report(provider=selected_provider(), settings=provider_settings())
    if harness:
        report.probes.append(probe_harness(model=model, timeout=timeout))
    if endpoint:
        report.probes.append(probe_endpoint(required=explicit))
    return report


def run_check_llm(
    *,
    harness: bool = False,
    endpoint: bool = False,
    model: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    as_json: bool = False,
) -> None:
    """Print the results of the live calls.

    Args:
        harness: Probe only the harness.
        endpoint: Probe only the endpoint.
        model: Model or deployment name for the harness probe.
        timeout: Seconds allowed for the harness probe.
        as_json: Emit machine-readable JSON instead of a table.

    Raises:
        SystemExit: With code 1 if any probe that was asked for failed.
    """
    explicit = harness or endpoint
    report = collect(
        harness=harness or not explicit,
        endpoint=endpoint or not explicit,
        explicit=explicit,
        model=model,
        timeout=timeout,
    )

    if as_json:
        click.echo(json.dumps(report.as_dict(), indent=2))
    else:
        _print(report)

    if any(probe.status == "error" for probe in report.probes):
        raise SystemExit(1)


def _print(report: Report) -> None:
    """Render the report for a terminal."""
    click.echo(f"  provider  {report.provider}")
    for name, value in report.settings.items():
        click.echo(f"            {name}={value}")
    if not report.settings:
        click.echo(
            "            no provider variables set; the harness is using "
            "whatever it is logged in as"
        )
    click.echo()

    for probe in report.probes:
        marker = STATUS_MARKERS.get(probe.status, "?")
        click.echo(f"  {marker} {probe.name:<9} {probe.detail}")
        if probe.model:
            click.echo(f"    {'':<9} model    {probe.model}")
        if probe.seconds is not None:
            timing = f"{probe.seconds:.1f}s"
            if probe.cost_usd is not None:
                timing += f", ${probe.cost_usd:.4f}"
            click.echo(f"    {'':<9} took     {timing}")
        if probe.reply and probe.status != "ok":
            click.echo(f"    {'':<9} said     {probe.reply}")
