"""``nrw report`` -- scaffold a sample's closing narrative from its fit chain.

A sample ends up with a dozen immutable result directories, each with its own
note, and no file that says what the sequence was *for*. The individual notes
cannot supply that: each one is written looking forward, without knowing which
branch turned out to matter. So the record ends as a pile of fits where a reader
cannot tell the keeper from the control from the abandoned attempt --- and an
oxide-free control run deliberately as a negative test looks identical to one run
by mistake.

What this writes is the ordered chain plus the questions only the analyst can
answer. The table is generated because it is derivable and nobody should retype
it; the prose is left blank because it is the part that is actually the analysis.

Ordering comes from ``started_at`` in the fit index rather than from filenames or
mtimes: mtimes are destroyed by the first edit, and model names carry no ordinal.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import click

from nr_workbench.project.layout import ProjectLayout, ProjectNotFoundError
from nr_workbench.provenance.index import FitIndex
from nr_workbench.provenance.summary import annotate

#: A fit id is ``YYYYMMDD-HHMMSSZ-<hash>``, so its *leading* characters are the
#: date and identical for every fit of a session. The trailing hash is the part
#: that identifies one, and it is what `nrw note` and `nrw whence` accept.
_TRAILING_HASH = re.compile(r"-([0-9a-f]+)$")

#: How much of a note's first line to carry into the table.
NOTE_EXCERPT = 96

#: Fences the generated table so a refresh can replace it without touching prose.
_BEGIN = "<!-- nrw:sequence -->"
_END = "<!-- /nrw:sequence -->"

#: The sections a report has to answer. Blank on purpose: this is the analysis,
#: and a generator that guessed at it would be inventing conclusions.
PROMPTS: tuple[tuple[str, str], ...] = (
    (
        "The question",
        "What was this sample measured to find out? One or two sentences, in the "
        "language of the experiment rather than the model.",
    ),
    (
        "What the data support",
        "The answer, with the fit ids it rests on. Quote intervals inflated by "
        "sqrt(chi2_red), and invariants rather than slab parameters where a layer "
        "is under the resolution limit.",
    ),
    (
        "What the data do not support",
        "The readings someone would plausibly take from these numbers and should "
        "not. A parameter that is conditional rather than measured, a difference "
        "that does not survive inflated intervals, a trend that was an optimiser "
        "artifact.",
    ),
    (
        "Why the sequence went the way it did",
        "The table above says what ran. This says why: which fit answered a "
        "question, which was a control and what it controlled for, which was "
        "abandoned and on what evidence. A fit you abandoned still needs its "
        "sentence -- it is the first thing forgotten and often the most useful.",
    ),
    (
        "What would change the answer",
        "The measurement, the reduction fix, or the contrast that would settle "
        "what is still open.",
    ),
)


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


def _slug(text: str) -> str:
    """A filesystem-safe stem from a title."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:60] or "report"


def _short(fit_id: str) -> str:
    """The identifying tail of a fit id, which is what the CLI accepts."""
    match = _TRAILING_HASH.search(fit_id)
    return match.group(1) if match else fit_id


def _first_line(text: str) -> str:
    """The first line of a note that the analyst actually typed.

    The template is mostly scaffolding -- a heading, a quoted echo of the run,
    and multi-line HTML comments prompting each section -- and `nrw assess`
    appends a generated block. All of it has to go, and the comments have to be
    stripped as *spans* rather than as lines: a line inside a multi-line comment
    does not itself begin with a marker, so a line-wise filter reads the prompt
    text back out as if the scientist had written it.
    """
    # The generated block first, and by its fences -- stripping comments would
    # remove the very markers that identify it, after which its chi-squared line
    # reads exactly like a sentence someone wrote.
    without_generated = re.sub(
        r"<!--\s*nrw:generated\s*-->.*?<!--\s*/nrw:generated\s*-->",
        "",
        text,
        flags=re.DOTALL,
    )
    cleaned = re.sub(r"<!--.*?-->", "", without_generated, flags=re.DOTALL)
    for raw in cleaned.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ">", "-", "*", "|", "<")):
            continue
        return line
    return ""


def _note_excerpt(layout: ProjectLayout, row: dict[str, Any]) -> str:
    """What the analyst wrote about a fit, trimmed for a table cell."""
    from nr_workbench.provenance.lookup import fit_dir

    directory = fit_dir(layout, row)
    if directory is None:
        return ""
    notes = directory / "NOTES.md"
    if not notes.is_file():
        return ""
    try:
        line = _first_line(notes.read_text(encoding="utf-8"))
    except OSError:
        return ""
    if len(line) > NOTE_EXCERPT:
        line = line[: NOTE_EXCERPT - 1].rstrip() + "…"
    # A stack written as `D2O|Cu|Ti|Si` is the most natural thing to put in a
    # note about a co-refinement, and unescaped it silently splits the row.
    return line.replace("|", "\\|")


def sequence_markdown(layout: ProjectLayout, sample: str) -> tuple[str, int]:
    """Render the sample's fits as a numbered chain, oldest first.

    Oldest first because the sequence is an argument and an argument reads
    forward. ``nrw ls`` is newest-first, which is right for "what did I just
    run" and wrong for "how did this analysis get here".

    Args:
        layout: The project layout.
        sample: Sample identifier.

    Returns:
        ``(markdown, undocumented_count)``.
    """
    index = FitIndex(layout.index_file)
    rows = list(reversed(annotate(index.fits(sample=sample))))
    if not rows:
        return ("_No fits recorded for this sample yet._\n", 0)

    promoted: dict[str, str] = {}
    for label in {str(e.get("label")) for e in index.promotions()}:
        entry = index.current_label(label)
        if entry is not None:
            promoted[str(entry.get("fit_id"))] = label

    lines = [
        "| # | fit | model | method | χ² | note |",
        "|---|---|---|---|---|---|",
    ]
    undocumented = 0
    details: list[str] = []
    for position, row in enumerate(rows, start=1):
        fit_id = str(row.get("fit_id", ""))
        chisq = row.get("chisq")
        chisq_text = f"{chisq:.3g}" if isinstance(chisq, int | float) else "–"
        if row.get("converged") is False:
            chisq_text += " (not converged)"
        method = str(row.get("method") or "–")
        label = promoted.get(fit_id)
        model = str(row.get("model", ""))
        if label:
            model += f" **[{label.upper()}]**"
        excerpt = _note_excerpt(layout, row)
        if not excerpt:
            undocumented += 1
            excerpt = "_nothing written down_"
        lines.append(
            f"| {position} | `{_short(fit_id)}` | {model} | {method} | "
            f"{chisq_text} | {excerpt} |"
        )
        details.append(
            f"{position}. `{fit_id}` — {row.get('change', '')}".rstrip().rstrip("—")
        )

    lines.append("")
    lines.append(
        "<details><summary>Full fit ids and what changed at each step</summary>"
    )
    lines.append("")
    lines.extend(details)
    lines.append("")
    lines.append("</details>")
    lines.append("")
    return ("\n".join(lines), undocumented)


def run_report(
    *,
    sample: str,
    title: str | None = None,
    root: str | None = None,
    force: bool = False,
    stdout: bool = False,
) -> None:
    """Write the closing report for a sample, with its fit chain filled in.

    Args:
        sample: Sample identifier.
        title: Report title; defaults to the sample's own name.
        root: Project root; discovered if omitted.
        force: Rewrite the generated sequence in an existing report.
        stdout: Print instead of writing, for a look before committing to a file.

    Raises:
        click.ClickException: If the sample does not exist, or the report exists
            and neither ``force`` nor ``stdout`` was given.
    """
    layout = _layout(root)
    directory = layout.sample(sample)
    if not directory.is_dir():
        raise click.ClickException(
            f"No sample {sample!r}. `nrw sample new {sample}` creates one."
        )

    heading = title or f"{sample}: what the fits show"
    sequence, undocumented = sequence_markdown(layout, sample)
    today = datetime.now(UTC).strftime("%Y-%m-%d")

    body = [f"# {heading}", "", f"<!-- {today} -->", ""]
    body += [
        "**In one line:** _the answer, before the reasoning. Write this last._",
        "",
        "## The sequence",
        "",
        "Oldest first. This table is generated by `nrw report`; the prose below it",
        "is not. `nrw report --force` refreshes the table and leaves the prose alone.",
        "",
        _BEGIN,
        sequence + _END,
        "",
    ]
    for section, prompt in PROMPTS:
        body += [f"## {section}", "", f"<!-- {prompt} -->", ""]

    text = "\n".join(body)

    if stdout:
        click.echo(text)
        return

    reports = directory / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    path = reports / f"{_slug(heading)}.md"

    if path.exists() and not force:
        raise click.ClickException(
            f"{path.relative_to(layout.root)} already exists. Use --force to "
            "refresh its generated sequence table, or --stdout to see the new "
            "one without touching the file."
        )
    if path.exists():
        text = _refresh(path.read_text(encoding="utf-8"), sequence)

    path.write_text(text, encoding="utf-8")
    click.echo(f"  {path.relative_to(layout.root)}")
    if undocumented:
        click.secho(
            f"  ! {undocumented} fit(s) in the table have nothing written down. A "
            "sequence\n    with gaps in it is not a narrative -- `nrw note <fit_id> "
            '-m "..."`.',
            fg="yellow",
        )
    click.echo("  Name fit ids in the prose and they become links.")


def _refresh(existing: str, sequence: str) -> str:
    """Replace the generated sequence in a report, leaving the prose alone.

    Without the fences an older report has no machine-findable table, so the new
    one is appended under a dated heading rather than guessing which lines were
    generated. Deleting someone's prose to save them a paste is not a trade this
    should make.
    """
    if _BEGIN in existing and _END in existing:
        head, _, rest = existing.partition(_BEGIN)
        _, _, tail = rest.partition(_END)
        return f"{head}{_BEGIN}\n{sequence}{_END}{tail}"
    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    return (
        f"{existing.rstrip()}\n\n## The sequence, as of {stamp}\n\n"
        f"{_BEGIN}\n{sequence}{_END}\n"
    )
