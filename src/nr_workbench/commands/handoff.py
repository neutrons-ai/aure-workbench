"""``nrw handoff`` -- put an interactive session where an unattended one left off.

The unattended agent gets a composed prompt: every offline check run before it
starts, the fit chain, the reports, the skills it should read. An interactive
session gets none of that. It is opened from an editor by a scientist who says
"pick up where the agent left off", and it then rediscovers the project by
hand.

Measured on the reference beamtime project, that rediscovery was 28 tool calls
before the first substantive action, ten of them spent finding the ``nrw``
binary. All of it was derivable. :func:`nr_workbench.agent.session.observe` had
already computed most of it -- and there was no command that would print it.

So this is mostly plumbing, and deliberately so. It reuses ``observe()`` rather
than reimplementing it, because two renderings of project state that drift
apart is a worse failure than either one being incomplete.

What it adds beyond ``observe()`` is the part specific to taking over from
someone: the escalations in full and first, the integrity check, what the last
session did and whether it finished, who is reading, and the reading order.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import click

from nr_workbench.project.layout import ProjectLayout, ProjectNotFoundError

#: How much of ``ESCALATIONS.md`` to print. Generous: it is the highest-value
#: file in a project that has had an unattended session, and the reference one
#: ran to 11k characters holding two live tooling bugs and a retracted
#: conclusion. Truncating it to save a screen is a false economy.
ESCALATION_CHARS = 20000

#: How many transcript lines to scan when working out how a session ended.
TRANSCRIPT_TAIL = 400

#: How many instances of one integrity problem to name before summarising.
PROBLEMS_PER_KIND = 6

#: How much of a problem's detail to show. A contradiction's detail is a
#: paragraph of advice repeated for every fit sharing the spec.
PROBLEM_DETAIL_CHARS = 140


def _layout(root: str | None = None) -> ProjectLayout:
    """Discover the project, or fail with guidance."""
    try:
        return (
            ProjectLayout(root=Path(root).resolve())
            if root
            else ProjectLayout.discover()
        )
    except ProjectNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc


def _invocation_block(layout: ProjectLayout) -> list[str]:
    """How to run ``nrw`` here, said before anything else needs it.

    First, because every other line in this report suggests a command. A
    session that reads "run `nrw ls`" and cannot has to go and solve this
    before it can use any of the rest.
    """
    from nr_workbench.project import toolpath

    lines = ["## Running nrw here", ""]
    if toolpath.resolvable_in_fresh_shell():
        lines.append("`nrw` is on your PATH. Use it directly.")
        return lines

    executable = toolpath.nrw_executable()
    shim = layout.root / toolpath.SHIM_RELPATH
    lines.append(
        "`nrw` is NOT on the PATH of a shell started here, so a bare `nrw` "
        "will fail.\nDo not go looking for it -- use one of these:"
    )
    lines.append("")
    if shim.is_file():
        lines.append(f"  ./{toolpath.SHIM_RELPATH} <args>     (from the project root)")
    if os.environ.get(toolpath.NRW_BIN_ENV):
        lines.append(f"  ${toolpath.NRW_BIN_ENV} <args>")
    if executable is not None:
        lines.append(f"  {executable} <args>")
    lines.append("")
    lines.append(
        "`nrw doctor --fix-path` makes a bare `nrw` work in sessions here, if "
        "the scientist wants that."
    )
    return lines


def _escalations_block(layout: ProjectLayout) -> list[str]:
    """``ESCALATIONS.md``, in full, first.

    This is the file an unattended session writes when it hits something a
    person has to decide. In the reference project it recorded two live
    generator bugs, a normalisation offset worked around in the model, a
    quarantined run, and a conclusion the session itself retracted. A takeover
    that writes a spec before reading it walks into the bug.
    """
    path = layout.root / "ESCALATIONS.md"
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        return ["## Escalations", "", f"(ESCALATIONS.md could not be read: {exc})"]
    if not text:
        return []

    truncated = text[:ESCALATION_CHARS]
    if len(text) > len(truncated):
        truncated += f"\n\n[...truncated; read {path.name} in full]"

    # Quoted rather than spliced. The file is written by a previous session and
    # is full of its own `##` headings; inlined, they read as sections of this
    # document, and the reader loses track of which text is the handoff talking
    # and which is the escalation being reported.
    quoted_body = "\n".join(f"  | {line}" for line in truncated.splitlines())

    return [
        "## Escalations -- READ THIS FIRST",
        "",
        "Written by an unattended session when it hit something a person has "
        "to decide.",
        "Items here may still be live: a tooling bug worked around, a run "
        "quarantined, a",
        "conclusion retracted. Read it before you write a spec, not after a "
        "fit disagrees",
        "with you.",
        "",
        quoted_body,
    ]


def _last_session_block(layout: ProjectLayout, sample: str) -> list[str]:
    """What the last unattended session on this sample did, and how it ended.

    "How it ended" is the part a takeover cannot get anywhere else and most
    needs: a session stopped by its turn cap left work half-done and its last
    thought unwritten, which is a different situation from one that finished.
    """
    directory = layout.root / ".nrw" / "agent"
    if not directory.is_dir():
        return []

    transcripts = sorted(directory.glob(f"*-{sample}.jsonl"))
    if not transcripts:
        return []
    latest = transcripts[-1]

    lines = ["## The last unattended session", ""]
    stamp = latest.name.split("-")[0]
    try:
        when = datetime.strptime(stamp, "%Y%m%d").replace(tzinfo=UTC)
        lines.append(
            f"Ran {when:%Y-%m-%d}. Transcript: {latest.relative_to(layout.root)}"
        )
    except ValueError:
        lines.append(f"Transcript: {latest.relative_to(layout.root)}")

    prompt = latest.with_name(latest.name.replace(".jsonl", "-prompt.md"))
    if prompt.is_file():
        lines.append(f"The prompt it was given: {prompt.relative_to(layout.root)}")

    ending = _session_ending(latest)
    if ending:
        lines.append(ending)

    lines.append("")
    lines.append(
        "Its reasoning is in the fit notes and the reports, not in the "
        "transcript. Read those\nfirst; open the transcript only to answer a "
        "question they leave open."
    )
    return lines


def _session_ending(transcript: Path) -> str:
    """One line on how a session terminated, from its own final record."""
    try:
        with transcript.open(encoding="utf-8") as handle:
            tail = handle.readlines()[-TRANSCRIPT_TAIL:]
    except OSError:
        return ""

    for line in reversed(tail):
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("type") != "result":
            continue
        subtype = str(record.get("subtype", ""))
        turns = record.get("num_turns")
        if record.get("is_error") or subtype not in ("success", ""):
            reason = subtype or "error"
            return (
                f"It ended on `{reason}`"
                + (f" after {turns} turns" if turns else "")
                + " -- so it may have stopped mid-thought. Check whether its "
                "last fit has a note."
            )
        return "It ran to completion" + (f" in {turns} turns." if turns else ".")
    return ""


def _clip(detail: str, limit: int = PROBLEM_DETAIL_CHARS) -> str:
    """One line of a problem's detail.

    A contradiction's detail is a full paragraph of advice, repeated verbatim
    for every fit that shares the spec. Printed whole, twenty-five of them are
    twelve thousand characters saying one thing. The full text is in the
    offline-check digest further down, which reports them deduplicated.
    """
    flattened = " ".join(str(detail).split())
    if len(flattened) <= limit:
        return flattened
    return flattened[: limit - 1].rstrip() + "…"


def _integrity_block(layout: ProjectLayout) -> list[str]:
    """What `nrw check` would say, without making anyone run it."""
    from nr_workbench.commands.provenance_cmd import collect_problems
    from nr_workbench.provenance.index import FitIndex

    try:
        checked, problems = collect_problems(layout, FitIndex(layout.index_file))
    except Exception as exc:  # noqa: BLE001 - a broken check must not block a handoff
        return ["## Project integrity", "", f"(`nrw check` could not run: {exc})"]

    lines = ["## Project integrity", "", f"{checked} fit(s) checked."]
    if not problems:
        lines.append("No problems found.")
        return lines

    # Grouped and capped. A busy sample can carry thirty `stale-input` lines --
    # one per fit whose spec was iterated afterwards -- and printing them all
    # buries the one `missing-input` that actually needs attention under a wall
    # of a problem that is usually benign.
    by_kind: dict[str, list[dict[str, str]]] = {}
    for problem in problems:
        by_kind.setdefault(problem["kind"], []).append(problem)

    lines.append("")
    for kind, found in sorted(by_kind.items(), key=lambda item: -len(item[1])):
        lines.append(f"  {kind} ({len(found)}):")
        for problem in found[:PROBLEMS_PER_KIND]:
            lines.append(f"    {problem['fit_id']}  {_clip(problem['detail'])}")
        if len(found) > PROBLEMS_PER_KIND:
            lines.append(
                f"    ... and {len(found) - PROBLEMS_PER_KIND} more "
                f"({kind}); `nrw check` lists them all."
            )

    if "stale-input" in by_kind:
        lines.append("")
        lines.append(
            "A `stale-input` is usually benign: it means a spec was iterated "
            "after the fit ran,\nwhich is what exploring looks like. It is not "
            "a reason to re-run anything. What it\n*does* mean is that the "
            "script in that result directory is the one that produced it and\n"
            "the spec in `models/` is not -- so read the frozen copy, not the "
            "live spec, when you\nwant to know what was actually fitted."
        )
    return lines


def _undocumented_block(layout: ProjectLayout, sample: str) -> list[str]:
    """Fits with nothing written down, which are the ones you cannot build on."""
    from nr_workbench.commands.report import undocumented_fits

    missing = undocumented_fits(layout, sample)
    if not missing:
        return []
    return [
        "## Fits with nothing written down",
        "",
        "A fit whose reasoning was never recorded is a number nobody can "
        "defend, including\nthe session that produced it. These are the gaps:",
        "",
        *(f"  {fit_id}" for fit_id in missing),
        "",
        "`nrw note <fit-id> --why ... --showed ... --caveat ...` closes one. If "
        "you cannot\nreconstruct why a fit was run, say that in the note -- it "
        "is still worth more than\nsilence.",
    ]


def _audience_block(layout: ProjectLayout) -> list[str]:
    """Who is reading, and what that asks of you."""
    from nr_workbench.project import audience as audience_mod

    audience = audience_mod.load(layout.root)
    lines = ["## Who you are working with", ""]
    for axis in audience_mod.AXES:
        lines.append(f"  {axis:<14} {audience.as_dict()[axis]}")
    lines.append("")
    for instruction in audience_mod.guidance(audience):
        lines.append(f"- {instruction}")
    return lines


def _reading_order(layout: ProjectLayout, sample: str) -> list[str]:
    """The order to read things in, and why that order.

    Explicit because the natural order is wrong. `results/` is the biggest and
    most inviting directory in the project and the least informative per byte:
    it holds every abandoned attempt at equal weight with the answer.
    """
    from nr_workbench.spec.authoring import find_skills, relevant_skills

    notes = layout.sample(sample) / "sample.md"
    skills: list[str] = []
    if notes.is_file():
        try:
            skills = relevant_skills(
                notes.read_text(encoding="utf-8"), find_skills(layout.root)
            )
        except OSError:
            skills = []

    lines = [
        "## Read these, in this order",
        "",
        "1. ESCALATIONS.md (above) -- what a person still has to decide.",
        f"2. samples/{sample}/reports/ -- the conclusions, and which branches "
        "were abandoned.",
        f"3. samples/{sample}/sample.md -- the sample, and the declared task.",
        "4. `nrw ls` -- the fit chain. Then the NOTES.md of only the fits you "
        "intend to build on.",
        "",
        "Not `results/` first. It is the largest directory here and the least "
        "informative per\nbyte: an abandoned exploration and the reportable fit "
        "look identical from outside.",
    ]
    if skills:
        lines += [
            "",
            "The skills this sample calls for:",
            *(f"  skills/reflectometry/{name}/SKILL.md" for name in skills),
        ]
    return lines


def _rules_block() -> list[str]:
    """The constraints that are not obvious from the file tree."""
    return [
        "## Before you change anything",
        "",
        "- **Every recorded fit is immutable.** Never overwrite a result; a new "
        "fit is a new\n  directory. `nrw ls` shows what exists.",
        "- **Never hand-edit a generated `models/*.py`.** Edit the `.yaml` and "
        "regenerate, or\n  `nrw model fork` to take ownership with provenance "
        "intact.",
        "- **A report already written is a finished artefact.** New work goes "
        "in a new report:\n  `nrw report <sample> --topic <slug>`. Do not edit "
        "someone else's report unless you\n  are asked to.",
        "- **Do not silently re-run a fit that exists to get a different "
        "answer.** If you\n  disagree with a recorded conclusion, say so in "
        "writing, with the evidence.",
        "- **`nrw promote` is the scientist's decision**, not yours.",
    ]


def handoff_markdown(layout: ProjectLayout, sample: str, *, brief: bool = False) -> str:
    """Render the full state of play for one sample.

    Args:
        layout: The project layout.
        sample: Sample identifier.
        brief: Omit the long blocks -- escalations in full, and the observation
            digest -- keeping the orientation and the pointers.

    Returns:
        Markdown, ready to print.
    """
    from nr_workbench.agent.session import declared_task, observe

    blocks: list[list[str]] = [
        [f"# Handoff: {sample}", "", f"Project: {layout.root}"],
        _invocation_block(layout),
    ]

    notes = layout.sample(sample) / "sample.md"
    if notes.is_file():
        try:
            task = declared_task(notes.read_text(encoding="utf-8"))
        except OSError:
            task = ""
        if task:
            blocks.append(["## What the scientist asked for", "", task])

    if not brief:
        blocks.append(_escalations_block(layout))

    blocks.append(_last_session_block(layout, sample))
    blocks.append(_integrity_block(layout))
    blocks.append(_undocumented_block(layout, sample))

    if not brief:
        observations = observe(layout.root, sample)
        if observations:
            blocks.append(
                [
                    "## What the offline checks find right now",
                    "",
                    "Recomputed just now from the file headers, the specs and "
                    "the recorded fits --\nnot copied from the last session.",
                    "",
                    "\n\n".join(observations),
                ]
            )

    blocks.append(_audience_block(layout))
    blocks.append(_reading_order(layout, sample))
    blocks.append(_rules_block())

    return "\n\n".join("\n".join(block) for block in blocks if block) + "\n"


def _sample_states(layout: ProjectLayout) -> list[dict[str, Any]]:
    """One row per sample: what is there, for the no-argument listing."""
    from nr_workbench.commands.report import undocumented_fits
    from nr_workbench.provenance.index import FitIndex

    index = FitIndex(layout.index_file)
    rows = []
    for sample in layout.list_samples():
        fits = index.fits(sample=sample)
        reports = layout.sample(sample) / "reports"
        written = len(list(reports.glob("*.md"))) if reports.is_dir() else 0
        rows.append(
            {
                "sample": sample,
                "fits": len(fits),
                "reports": written,
                "undocumented": len(undocumented_fits(layout, sample)),
            }
        )
    return rows


def run_handoff(
    *,
    sample: str | None = None,
    root: str | None = None,
    brief: bool = False,
    as_json: bool = False,
) -> None:
    """Print the state of play for a sample, for a session taking over.

    Args:
        sample: Sample identifier. With none, list the samples and their state.
        root: Project root; discovered if omitted.
        brief: Drop the long blocks.
        as_json: Emit the same content as JSON.

    Raises:
        click.ClickException: If the project or the sample does not exist.
    """
    layout = _layout(root)

    if sample is None:
        rows = _sample_states(layout)
        if as_json:
            click.echo(json.dumps(rows, indent=2))
            return
        if not rows:
            click.echo("No samples yet. `nrw sample new <ID>` creates one.")
            return
        click.echo(f"Project: {layout.root}")
        click.echo()
        for row in rows:
            gap = f", {row['undocumented']} undocumented" if row["undocumented"] else ""
            click.echo(
                f"  {row['sample']:<16} {row['fits']} fit(s), "
                f"{row['reports']} report(s){gap}"
            )
        click.echo()
        click.echo("  nrw handoff <sample>   the full state of play")
        return

    if not layout.sample(sample).is_dir():
        raise click.ClickException(layout.missing_sample_message(sample))

    text = handoff_markdown(layout, sample, brief=brief)

    if as_json:
        click.echo(json.dumps({"sample": sample, "markdown": text}, indent=2))
        return

    click.echo(text, nl=False)
