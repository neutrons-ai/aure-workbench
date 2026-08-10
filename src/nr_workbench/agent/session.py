"""One bounded, unattended session of the coding harness.

The harness makes the decisions --- that is the finding this whole package
rests on. What it needs from us is a *bounded* invocation: one sample, one
declared task, one transcript, and a prompt built from files that already
exist rather than from a second description of the project that would drift.

Three rules shape it.

**No declared task, no session.** ``## Fits to perform`` in ``sample.md`` is
the scientist's statement of what they want. When it is empty, this refuses to
start instead of inventing work. An agent that decides for itself what is
interesting is the failure mode the whole design is arranged against, and the
only reliable place to stop it is before it begins.

**Observations first.** The stage-1 checks run here, offline, and their output
goes into the prompt. The errors that cost a week of the reference experiment
were visible in file headers before the first fit ran; a session that starts by
reading them starts ahead of where a person usually does.

**The limits are elsewhere.** Nothing here asks the harness to behave. The
prompt says what is refused so the agent does not waste a turn discovering it,
but the refusal itself is :mod:`nr_workbench.agent.guard` --- a hook and an
environment variable, both of which work whether or not the model cooperates.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nr_workbench.agent.guard import AGENT_ENV

#: The heading in ``sample.md`` that states what the scientist wants fitted.
TASK_HEADING = "Fits to perform"

#: Where a session's transcript lands, under the project's ``.nrw``.
SESSION_DIR = "agent"

#: What the harness is asked to append when it hits a limit. One file at the
#: project root, not one per sample: a scientist coming in at 8am reads one
#: thing, and anything that splits it defeats the purpose.
ESCALATIONS = "ESCALATIONS.md"

#: What a sample may be called. The name reaches the filesystem twice --- the
#: notes it reads and the transcript it writes --- so it is validated rather
#: than trusted.
SAFE_SAMPLE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")

#: How much of a recorded free-text field to quote in the prompt. Fit notes
#: and model names are written by whoever ran the fit --- including a previous
#: unattended session --- so they are data, not instructions, and a note
#: containing its own headings must not read as part of this prompt.
QUOTED_FIELD_CHARS = 200

#: Default cap on harness turns. A beamtime session is one task; a run that
#: needs more than this has usually lost the thread rather than found a hard
#: problem, and the cost of stopping early is one more session.
DEFAULT_TURNS = 60


class SessionError(RuntimeError):
    """A session could not be composed or run."""


@dataclass
class Session:
    """A composed session, before or after it ran.

    Attributes:
        sample: The sample it is about.
        prompt: The full text handed to the harness.
        task: The declared task, as written in ``sample.md``.
        transcript: Where the harness's output was written.
        returncode: The harness's exit status, or ``None`` if not run.
    """

    sample: str
    prompt: str
    task: str
    transcript: Path | None = None
    returncode: int | None = None
    observations: list[str] = field(default_factory=list)


def declared_task(notes: str) -> str:
    """The scientist's stated intent for this sample.

    The scaffolded ``sample.md`` fills every section with HTML-comment
    guidance. Those comments are instructions to the human, not to the agent,
    and a section containing only guidance means the human has not written
    anything yet --- so they are stripped before deciding whether the section
    is empty.

    Args:
        notes: The text of ``sample.md``.

    Returns:
        The prose under ``## Fits to perform``, or an empty string.
    """
    pattern = re.compile(
        rf"^##\s+{re.escape(TASK_HEADING)}\s*$(.*?)(?=^##\s|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(notes or "")
    if not match:
        return ""
    body = re.sub(r"<!--.*?-->", "", match.group(1), flags=re.DOTALL)
    return body.strip()


def observe(root: Path, sample: str) -> list[str]:
    """Run the offline checks and render them for the prompt.

    Every check here is deterministic and needs no model. A failure in one must
    not take the session down --- a session that starts with fewer observations
    is worse than one that starts with all of them, but a session that does not
    start is worse than both.

    Args:
        root: Project root.
        sample: Sample identifier.

    Returns:
        One block of text per check that had something to say.
    """
    blocks: list[str] = []

    for render in (
        _observe_arriving,
        _observe_quarantine,
        _observe_data,
        _observe_specs,
        _observe_fits,
    ):
        try:
            block = render(root, sample)
        except Exception as exc:  # noqa: BLE001 - see the docstring
            block = f"({render.__name__} could not run: {type(exc).__name__}: {exc})"
        if block:
            blocks.append(block)

    return blocks


def quoted(value: Any) -> str:
    """Render a recorded free-text field as one inert line.

    Fit notes, model names and spec descriptions are all written by whoever
    ran the fit, and during a beamtime that can be a previous unattended
    session. Splicing them into the prompt unchanged gives one session a way
    to leave instructions for the next --- so newlines and headings are
    flattened and the result is capped.
    """
    text = re.sub(r"(?m)^\s*#+\s*", "", str(value or ""))
    text = " ".join(text.split())
    if len(text) > QUOTED_FIELD_CHARS:
        text = text[: QUOTED_FIELD_CHARS - 1] + "\u2026"
    return text


def _observe_arriving(root: Path, sample: str) -> str:
    """Measurements whose files have not stopped changing.

    `nrw agent watch` will not start a session over these at all. But a person
    running `nrw agent run` by hand during a beamtime is very often doing it
    *because* a measurement just finished, and a steady-state measurement is
    three angle segments written minutes apart --- so the third can still be
    on its way. Fitting two segments of three gives a perfectly plausible
    answer from two-thirds of the data.

    This warns rather than refuses. The person asked for the session, and they
    may well know something the mtimes do not.
    """
    from nr_workbench.agent.watch import DEFAULT_SETTLE_SECONDS, WatchState, assess

    unsettled = [
        v
        for v in assess(
            root, sample, WatchState(), settle_seconds=DEFAULT_SETTLE_SECONDS
        )
        if v.state in {"arriving", "settling"}
    ]
    if not unsettled:
        return ""

    lines = [f"  run {v.run} ({v.kind}): {v.reason}" for v in unsettled]
    return (
        "STILL ARRIVING. These measurements' files changed recently, so what "
        "is on disk may not be all of it --- a steady-state run is three angle "
        "segments written minutes apart. Fitting a partial measurement returns "
        "a plausible number, not an error. Prefer the measurements that are "
        "complete, and say in ESCALATIONS.md if the task needs one of "
        "these:\n" + "\n".join(lines)
    )


def _observe_quarantine(root: Path, sample: str) -> str:
    """Runs that must not be fitted at all.

    The same check `nrw agent watch` uses to decide what to start. It runs here
    too, because a session is per-sample while the quarantine is per-run: the
    daemon can hold one measurement back and still start a session over the
    sample containing it, and the agent has to be told which run to leave
    alone. Somebody running `nrw agent run` by hand gets it for the same
    reason.
    """
    from nr_workbench.agent.watch import quarantine_reason
    from nr_workbench.project.scan import scan_sample

    scan = scan_sample(root, sample)
    held = [
        f"  run {run}: {reason}"
        for run in sorted(scan.steady)
        if (reason := quarantine_reason(scan, run))
    ]
    if not held:
        return ""
    return (
        "DO NOT FIT these runs. Their files are inconsistent in a way that "
        "needs a person; say so in ESCALATIONS.md and work on the rest:\n"
        + "\n".join(held)
    )


def _observe_data(root: Path, sample: str) -> str:
    """Headers against the notes: mislabelled runs, stray reductions."""
    from nr_workbench.instrument.header import read_header
    from nr_workbench.project.config import load_config
    from nr_workbench.project.scan import scan_sample
    from nr_workbench.reconcile import reconcile

    scan = scan_sample(root, sample)
    # Every header the command reads, including the combined file, and each in
    # its own try: one unreadable file must cost one finding, not all of them.
    paths = [
        path
        for measurement in scan.steady.values()
        for path in [*measurement.partials.values(), measurement.combined]
        if path
    ]
    headers = []
    for path in paths:
        try:
            headers.append(read_header(root / path))
        except (OSError, ValueError):
            continue
    if not headers:
        return ""

    notes_path = root / "samples" / sample / "sample.md"
    notes = notes_path.read_text(encoding="utf-8") if notes_path.is_file() else ""
    try:
        standard = list(getattr(load_config(root), "standard_thetas", []) or [])
    except Exception:  # noqa: BLE001 - a missing config only costs one check
        standard = None
    found = reconcile(
        sample,
        headers,
        notes,
        series_runs={s.run for s in scan.series if s.run},
        # Without this the non-standard-angle check silently does nothing, and
        # the block would claim to be `nrw data reconcile` while being less.
        standard_thetas=standard,
    )
    if not found.findings:
        return ""

    lines = [f"  [{f.severity}] run {f.run}: {f.message}" for f in found.findings]
    return "Data vs sample.md (`nrw data reconcile`):\n" + "\n".join(lines)


def _observe_specs(root: Path, sample: str) -> str:
    """Specs against themselves: contradictions and silently-held parameters."""
    from nr_workbench.contradictions import check as contradictions
    from nr_workbench.spec.models import load_spec
    from nr_workbench.spec.validate import HELD_AT_DEFAULTS, validate_spec

    models = root / "samples" / sample / "models"
    if not models.is_dir():
        return ""

    # Without the tNR assessment, `_form_against_trajectory`,
    # `_paths_against_implied_change` and `_constraint_on_a_flat_run` all
    # return nothing --- three of the five checks, including the one the
    # skills call the most-repeated Red Flag. The block would still be
    # labelled `nrw check --contradictions` while being a fraction of it.
    assessment = _latest_assessment(root / "samples" / sample)

    # Grouped by finding, not by file. A sample accumulates near-identical
    # specs -- the reference experiment has twelve -- and listing the same
    # three findings twelve times buries whatever is different about one of
    # them, which is the only reason to read the list at all.
    found: dict[str, list[str]] = {}
    for path in sorted(models.glob("*.yaml")):
        try:
            spec = load_spec(path)
        except Exception:  # noqa: BLE001 - an unparseable spec is `nrw check`'s job
            continue
        report = contradictions(spec, name=path.stem, tnr=assessment)
        messages = [f"[{i.kind}] {quoted(i.message)}" for i in report.contradictions]
        validation = validate_spec(spec, root)
        messages += [f"[error] {quoted(e)}" for e in validation.errors]
        messages += [
            quoted(i) for i in validation.info if i.startswith(HELD_AT_DEFAULTS)
        ]
        for message in messages:
            found.setdefault(message, []).append(path.name)

    if not found:
        return ""

    lines = []
    for message, specs in found.items():
        where = specs[0] if len(specs) == 1 else f"{len(specs)} specs incl. {specs[0]}"
        lines.append(f"  {where}: {message}")
    return "Specs (`nrw check --contradictions`):\n" + "\n".join(lines)


def _latest_assessment(sample_dir: Path) -> dict[str, Any] | None:
    """The most recent tNR assessment for a sample, or None.

    Mirrors `provenance_cmd._latest_assessment`; the contradiction checks that
    need it are inert without it.
    """
    import json

    found = sorted((sample_dir / "assessments").glob("*/*assessment.json"))
    for path in reversed(found):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            return payload
    return None


def _observe_fits(root: Path, sample: str) -> str:
    """What has been fitted already, newest first, with what changed."""
    from nr_workbench.project.layout import ProjectLayout
    from nr_workbench.provenance.index import FitIndex
    from nr_workbench.provenance.summary import annotate

    layout = ProjectLayout(root)
    entries = FitIndex(layout.index_file).fits(sample=sample)
    if not entries:
        return "No fits recorded for this sample yet."

    lines = []
    for entry in annotate(entries)[:10]:
        note = entry.get("change") or entry.get("description") or ""
        chisq = entry.get("chisq")
        shown = f"{chisq:.4g}" if isinstance(chisq, int | float) else "?"
        lines.append(
            f"  {entry['fit_id']}  {quoted(entry.get('model', '?'))}  "
            f"chisq {shown}  {quoted(note)}".rstrip()
        )
    return f"Recorded fits ({len(entries)} total, newest first):\n" + "\n".join(lines)


def compose(root: Path, sample: str) -> Session:
    """Build the session prompt for one sample.

    Args:
        root: Project root.
        sample: Sample identifier.

    Returns:
        The composed session, not yet run.

    Raises:
        SessionError: If the sample does not exist or declares no task.
    """
    if not SAFE_SAMPLE.fullmatch(sample):
        raise SessionError(
            f"{sample!r} is not a sample name. It is used to build paths, and "
            "a value with a slash or '..' in it reads notes from outside the "
            "project and writes the transcript outside .nrw/agent/."
        )

    notes_path = Path(root) / "samples" / sample / "sample.md"
    if not notes_path.is_file():
        raise SessionError(f"No sample notes at {notes_path}")

    notes = notes_path.read_text(encoding="utf-8")
    task = declared_task(notes)
    if not task:
        raise SessionError(
            f"samples/{sample}/sample.md declares no task under "
            f"'## {TASK_HEADING}'.\n"
            "An unattended session needs to be told what to fit; deciding that "
            "for itself is exactly what it must not do.\n"
            "Write what you want out of this sample there, then run again."
        )

    from nr_workbench.spec.authoring import find_skills, relevant_skills

    skills = relevant_skills(notes, find_skills(Path(root)))
    observations = observe(Path(root), sample)

    return Session(
        sample=sample,
        task=task,
        observations=observations,
        prompt=_prompt(sample, task, skills, observations),
    )


def _prompt(sample: str, task: str, skills: list[str], observations: list[str]) -> str:
    """Assemble the text handed to the harness."""
    skill_lines = "\n".join(f"  - skills/reflectometry/{n}/SKILL.md" for n in skills)
    observed = "\n\n".join(observations) if observations else "(nothing to report)"

    return f"""\
You are running unattended during a neutron beamtime, analysing sample \
`{sample}` in this nr-workbench project. Nobody will answer a question until \
morning.

## What the scientist asked for

This is from `samples/{sample}/sample.md`, and it is the whole of your task. \
Do this and nothing else.

{task}

## What the offline checks already found

Run before you started, from the file headers, the specs and the recorded \
fits. Read these before deciding anything --- most of them are the kind of \
thing that is cheap to see now and expensive to discover after five fits.

{observed}

## Read these first

{skill_lines or "  (no skills installed; run `nrw skills install`)"}

Also `samples/{sample}/sample.md` in full, for what the sample is and what was \
done to it.

## How to work

- `nrw --help` is the tool surface; every subcommand has `--help`.
- Fit with `--method amoeba` while the beam is running, so results keep pace \
with the data. Use DREAM only once a fit is good and you want uncertainties.
- One fit at a time.
- After each fit, `nrw assess <fit-id>` and act on what it says.
- Record your reasoning as you go with `nrw note <fit-id>` --- for a fit you \
abandon as much as one you keep. Why a model was rejected is the part nobody \
can reconstruct later.
- `nrw ls` shows what is already recorded, and what changed between fits.

## What you must not do

These are refused by a hook, so trying them wastes a turn:

- `nrw promote` --- marking a fit as the answer is the scientist's decision.
- `nrw isaac export --upload` --- publishing outside the project.
- any `--force` --- every one of them exists because a check said no.

When you reach one of these, or hit anything you would have asked about, \
append to `{ESCALATIONS}` at the project root: what you were doing, the fit \
id, the evidence, and what you would have done. Then continue with the rest of \
the task, or stop if there is no rest.

## When you are done

Leave `samples/{sample}/reports/` holding what you learned, and stop. Write \
for a scientist who has five minutes and has not read any of this. Do not \
summarise every fit; say what the data supports, what it does not, and what \
you would do next.
"""


def harness_command(
    prompt_file: Path, *, turns: int = DEFAULT_TURNS, model: str | None = None
) -> list[str]:
    """The headless harness invocation.

    Args:
        prompt_file: File holding the composed prompt.
        turns: Cap on agent turns.
        model: Model name to pass through, or ``None`` for the default.

    Returns:
        The argv for ``claude -p``.

    Raises:
        SessionError: If the harness is not installed.
    """
    binary = shutil.which("claude")
    if not binary:
        raise SessionError(
            "The `claude` CLI is not on PATH, so there is no harness to run.\n"
            "Install Claude Code, or use --dry-run to see the composed prompt."
        )

    argv = [
        binary,
        "-p",
        f"@{prompt_file}",
        "--max-turns",
        str(turns),
        "--output-format",
        "stream-json",
        "--verbose",
    ]
    if model:
        argv += ["--model", model]
    return argv


def run(
    root: Path,
    sample: str,
    *,
    turns: int = DEFAULT_TURNS,
    model: str | None = None,
    timeout: int | None = None,
) -> Session:
    """Compose and run one unattended session.

    Args:
        root: Project root.
        sample: Sample identifier.
        turns: Cap on harness turns.
        model: Model to run, or ``None`` for the harness default.
        timeout: Seconds before the session is killed, or ``None``.

    Returns:
        The session, with its transcript path and exit status.

    Raises:
        SessionError: If the session cannot be composed, the harness is
            missing, or the session outlives ``timeout``.
    """
    from nr_workbench.provenance.record import format_timestamp, utc_now

    session = compose(Path(root), sample)

    stamp = format_timestamp(utc_now())
    directory = Path(root) / ".nrw" / SESSION_DIR
    directory.mkdir(parents=True, exist_ok=True)
    prompt_file = directory / f"{stamp}-{sample}-prompt.md"
    prompt_file.write_text(session.prompt, encoding="utf-8")
    transcript = directory / f"{stamp}-{sample}.jsonl"

    # NRW_AGENT is the second of the two limits, and it has to be set here
    # rather than left to the user's shell: a session started by the daemon has
    # no shell, and the hook alone is one misconfigured file away from nothing.
    environment = dict(os.environ)
    environment[AGENT_ENV] = "1"

    try:
        with transcript.open("w", encoding="utf-8") as handle:
            completed = subprocess.run(  # noqa: S603 - argv built here, no shell
                harness_command(prompt_file, turns=turns, model=model),
                cwd=str(root),
                env=environment,
                stdout=handle,
                stderr=subprocess.STDOUT,
                timeout=timeout,
                check=False,
                # Its own process group, so a timeout kills the fits it
                # started too. Killing only the harness leaves refl1d running
                # and writing into the project after the session is over.
                start_new_session=True,
            )
    except subprocess.TimeoutExpired as exc:
        _kill_tree(exc)
        raise SessionError(
            f"The session for {sample} passed {timeout}s and was stopped. "
            f"What it managed is in {transcript}."
        ) from exc

    session.transcript = transcript
    session.returncode = completed.returncode
    return session


def _kill_tree(expired: subprocess.TimeoutExpired) -> None:
    """Kill everything the timed-out session started.

    ``subprocess.run`` kills only its direct child. A harness that had a
    ``refl1d`` fit running leaves it writing into the project after the
    session is nominally over, which is how two fits end up interleaved in one
    directory.
    """
    import contextlib
    import os
    import signal

    pid = getattr(expired, "pid", None)
    if pid is None:
        return
    with contextlib.suppress(OSError, ProcessLookupError):
        os.killpg(os.getpgid(pid), signal.SIGKILL)
