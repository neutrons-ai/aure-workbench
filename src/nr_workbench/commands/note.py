"""``nrw note`` -- write and read the analysis notebook.

The command exists because the alternative is a path. Telling someone to edit
``samples/expt11/results/20260807-163359Z-0103d9c7/NOTES.md`` is telling them
to copy a fit id, and that is enough friction to lose the thought. ``nrw note
0103 -m "..."`` is not.

Reading matters as much as writing. ``nrw note <fit_id>`` with no message shows
every note that mentions that fit --- its own, and any sample report that cites
it --- which is the question "what do I already know about this run?" that
previously had no answer short of grepping.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

import click

from nr_workbench.notes import (
    NOTES_FILENAME,
    Note,
    fit_note,
    notes_about,
    sample_notes,
)
from nr_workbench.project.layout import ProjectLayout, ProjectNotFoundError
from nr_workbench.provenance.index import FitIndex
from nr_workbench.provenance.lookup import FitNotFoundError, resolve_fit


def run_note(
    *,
    target: str | None,
    message: str | None = None,
    title: str | None = None,
    sample: str | None = None,
    edit: bool = False,
    why: str | None = None,
    showed: str | None = None,
    caveat: str | None = None,
) -> None:
    """Append to, create, or show a note.

    Args:
        target: A fit id or prefix. Omit with ``--sample`` for a sample report.
        message: Text to append. Without it, the note is shown instead.
        title: Title for a new sample report.
        sample: Write a sample-level report instead of a fit note.
        edit: Open the note in ``$EDITOR`` after writing.
        why: Fill the fit note's "Why this run" section.
        showed: Fill the fit note's "What it showed" section.
        caveat: Fill the fit note's "Caveats" section.

    Raises:
        click.ClickException: If there is no project, or the target is
            ambiguous, or neither a fit nor a sample was named.
    """
    try:
        layout = ProjectLayout.discover()
    except ProjectNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc

    if sample and target:
        raise click.ClickException(
            "Name either a fit or --sample, not both. A note about how several "
            "fits relate belongs to the sample; a note about one run belongs "
            "to that run."
        )

    sections = {"why": why, "showed": showed, "caveat": caveat}
    if sample and any(sections.values()):
        raise click.ClickException(
            "--why/--showed/--caveat fill in a fit note's template sections, and "
            "a sample report has no such sections. Use --title and -m for a "
            "report about how the fits relate."
        )

    if sample:
        _sample_note(layout, sample, message, title, edit)
    elif target:
        _fit_note(layout, target, message, edit, sections)
    else:
        raise click.ClickException(
            "Nothing named. Give a fit id, or --sample <id> for a report about "
            "how the fits relate.\n"
            '  nrw note 0103d9c7 -m "oxide pinned at its floor; not measured"\n'
            '  nrw note --sample expt11 --title "why the tNR is fitted alone"'
        )


def _fit_note(
    layout: ProjectLayout,
    target: str,
    message: str | None,
    edit: bool,
    sections: dict[str, str | None] | None = None,
) -> None:
    """Append to, fill in, or show one fit's NOTES.md."""
    from nr_workbench.notes import SECTIONS, write_section

    index = FitIndex(layout.index_file)
    try:
        entry, directory = resolve_fit(layout, index, target)
    except FitNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc

    fit_id = str(entry["fit_id"])
    path = directory / NOTES_FILENAME
    filled = {key: value for key, value in (sections or {}).items() if value}

    if message is None and not filled:
        _show(layout, fit_id, entry.get("sample"), directory)
        if edit:
            click.edit(filename=str(path))
        return

    if not path.is_file():
        from nr_workbench.provenance.record import FitDirectory

        FitDirectory(directory).write_notes_stub(fit_id=fit_id)

    # Sections first, so a call giving both still leaves a free-text message
    # exactly where a bare `-m` would have put it.
    if filled:
        text = path.read_text(encoding="utf-8")
        for key, heading in SECTIONS.items():  # template order, not argument order
            if key in filled:
                text = write_section(text, heading, filled[key])
        path.write_text(text, encoding="utf-8")

    if message:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"\n{message.strip()}\n")

    click.echo(f"  {path.relative_to(layout.root)}")
    for key, heading in SECTIONS.items():
        if key in filled:
            click.secho(f"      under '{heading}'", dim=True)
    if edit:
        click.edit(filename=str(path))


def _sample_note(
    layout: ProjectLayout,
    sample: str,
    message: str | None,
    title: str | None,
    edit: bool,
) -> None:
    """Create or append to a report under ``samples/<id>/reports/``."""
    directory = layout.sample(sample)
    if not directory.is_dir():
        raise click.ClickException(layout.missing_sample_message(sample))
    reports = directory / "reports"
    reports.mkdir(parents=True, exist_ok=True)

    if message is None and title is None:
        notes = [n for n in sample_notes(layout.root, sample) if not n.blank]
        if not notes:
            click.echo(f"No reports yet for {sample}.")
            click.echo(
                f'  nrw note --sample {sample} --title "what the fits show" -m "..."'
            )
            return
        for note in notes:
            click.echo(f"  {note.path}")
            click.secho(f"      {note.title}", dim=True)
            if note.fits:
                click.secho(
                    f"      fits: {', '.join(note.fits[:6])}"
                    + (f" +{len(note.fits) - 6} more" if len(note.fits) > 6 else ""),
                    fg="cyan",
                    dim=True,
                )
        return

    heading = title or "note"
    path = reports / f"{_slug(heading)}.md"
    if not path.exists():
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        path.write_text(f"# {heading}\n\n<!-- {today} -->\n", encoding="utf-8")
    if message:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"\n{message.strip()}\n")

    click.echo(f"  {path.relative_to(layout.root)}")
    click.echo("  Packed with any fit of this sample. Name fit ids in the text")
    click.echo("  and they become links.")
    if edit:
        click.edit(filename=str(path))


def _show(layout: ProjectLayout, fit_id: str, sample: object, directory: Path) -> None:
    """Print every note that mentions a fit."""
    own = fit_note(layout.root, directory, fit_id, str(sample) if sample else None)
    reports = sample_notes(layout.root, str(sample)) if sample else []
    related = notes_about(reports, fit_id)

    if own is not None and not own.blank:
        click.secho(f"  {own.path}", bold=True)
        click.echo(_indent(own.text))
    elif own is not None:
        click.secho(f"  {own.path} is still the template.", dim=True)
        click.echo(f'    nrw note {fit_id[:8]} -m "what this run was for"')

    if related:
        click.echo()
        click.secho(f"  {len(related)} report(s) mention this fit:", bold=True)
        for note in related:
            click.echo(f"    {note.path}")
            click.secho(f"        {note.title}", dim=True)
    elif sample:
        click.echo()
        click.secho(f"  No report in samples/{sample}/reports/ mentions it.", dim=True)


def _indent(text: str) -> str:
    """Indent a note for display, dropping its HTML comments."""
    cleaned = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    lines = [f"    {line}".rstrip() for line in cleaned.strip().splitlines()]
    return "\n".join(lines)


def _slug(text: str) -> str:
    """A filesystem-safe stem from a title."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:60] or "note"


def collect(layout: ProjectLayout, sample: str | None) -> list[Note]:
    """Every non-blank note in a sample, reports and fit notes together.

    Args:
        layout: The project layout.
        sample: The sample, or ``None`` for every sample.

    Returns:
        Sample reports first, then fit notes.
    """
    samples = [sample] if sample else layout.list_samples()
    found: list[Note] = []
    for name in samples:
        found.extend(n for n in sample_notes(layout.root, name) if not n.blank)
        results = layout.sample(name) / "results"
        if not results.is_dir():
            continue
        for directory in sorted(results.iterdir()):
            if not (directory / "manifest.json").is_file():
                continue
            note = fit_note(layout.root, directory, directory.name, name)
            if note is not None and not note.blank:
                found.append(note)
    return found
