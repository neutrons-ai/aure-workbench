"""``nrw sample`` -- create and inspect samples."""

from __future__ import annotations

import click

from nr_workbench.project.config import ProjectConfigError, load_config
from nr_workbench.project.layout import (
    SAMPLE_SUBDIRS,
    ProjectLayout,
    ProjectNotFoundError,
)
from nr_workbench.project.render import RenderContext, render_tree
from nr_workbench.project.scaffold import Outcome, PlannedFile, apply_scaffold

#: Sample IDs become directory names and appear in generated scripts, so keep
#: them to characters that are safe in both.
_ALLOWED = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")


def validate_sample_id(sample_id: str) -> str:
    """Check a sample identifier is usable as a directory and script token.

    Args:
        sample_id: The proposed identifier.

    Returns:
        The identifier, unchanged.

    Raises:
        ValueError: If it is empty or contains characters outside
            ``[A-Za-z0-9_-]``.
    """
    if not sample_id:
        raise ValueError("Sample ID must not be empty")
    bad = sorted(set(sample_id) - _ALLOWED)
    if bad:
        raise ValueError(
            f"Sample ID {sample_id!r} contains disallowed character(s) {bad}. "
            "Use letters, digits, hyphen, and underscore only."
        )
    return sample_id


def plan_sample_files(
    context: RenderContext,
    sample_id: str,
    *,
    title: str | None = None,
) -> list[PlannedFile]:
    """Plan every file for one sample directory.

    Args:
        context: The project's render context, used for facility and beamtime.
        sample_id: The sample identifier.
        title: Human-readable title. Defaults to the sample ID.

    Returns:
        Planned files: the rendered sample templates plus a ``.gitkeep`` in each
        standard subdirectory, so the layout is visible before data arrives.

    Raises:
        ValueError: If the sample ID is not usable.
    """
    validate_sample_id(sample_id)
    prefix = f"samples/{sample_id}"

    sample_context = RenderContext(
        project_name=context.project_name,
        facility=context.facility,
        instrument=context.instrument,
        beamtime=context.beamtime,
        ipts=context.ipts,
        sample_id=sample_id,
        title=title or sample_id,
        created=context.created,
    )

    planned = render_tree("sample", sample_context, prefix=prefix)

    for subdir in SAMPLE_SUBDIRS:
        planned.append(
            PlannedFile(
                relpath=f"{prefix}/{subdir}/.gitkeep",
                content=b"",
                template_id=f"dir/sample/{subdir}",
            )
        )

    return planned


def run_sample_new(
    *,
    sample_id: str,
    title: str | None = None,
    beamtime: str | None = None,
) -> None:
    """Create ``samples/<sample_id>/`` with the standard layout.

    Args:
        sample_id: The sample identifier.
        title: Human-readable title for ``sample.md``.
        beamtime: Beamtime label. Defaults to the project's.

    Raises:
        click.ClickException: If there is no project here, or the ID is invalid.
    """
    try:
        layout = ProjectLayout.discover()
        config = load_config(layout.root)
    except (ProjectNotFoundError, ProjectConfigError) as exc:
        raise click.ClickException(str(exc)) from exc

    try:
        validate_sample_id(sample_id)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    context = RenderContext(
        project_name=config.name,
        facility=config.facility,
        instrument=config.instrument,
        beamtime=beamtime or config.beamtime,
        ipts=config.ipts,
    )

    planned = plan_sample_files(context, sample_id, title=title)
    report = apply_scaffold(layout.root, planned)

    created = report.count(Outcome.CREATE)
    if created == 0:
        click.echo(f"Sample '{sample_id}' already exists at {layout.sample(sample_id)}")
    else:
        click.echo(f"Created sample '{sample_id}' ({created} file(s))")

    if report.drifted:
        click.echo("  files you have edited were left alone:")
        for result in report.drifted:
            click.echo(f"    {result.relpath}")

    click.echo()
    click.echo("Next:")
    click.echo(f"  1. Describe the sample in samples/{sample_id}/sample.md")
    click.echo(
        f"  2. Copy reduced data into samples/{sample_id}/data/steady/ and data/tnr/"
    )


def run_sample_scan(
    *, sample_id: str | None = None, as_json: bool = False, write: bool = True
) -> None:
    """Register the data on disk for one sample, or all of them.

    ``sample.md`` stays authoritative for intent; this maintains the machine
    register and, more usefully, reports where the two disagree.

    Args:
        sample_id: The sample to scan. All of them if omitted.
        as_json: Emit machine-readable JSON.
        write: Update ``sample.yaml``. Off makes it a read-only report.

    Raises:
        click.ClickException: If there is no project, or the sample is unknown.
    """
    import json as _json

    import yaml

    from nr_workbench.project.scan import scan_sample

    try:
        layout = ProjectLayout.discover()
        config = load_config(layout.root)
    except (ProjectNotFoundError, ProjectConfigError) as exc:
        raise click.ClickException(str(exc)) from exc

    samples = [sample_id] if sample_id else layout.list_samples()
    if not samples:
        raise click.ClickException(
            "No samples yet. Create one with `nrw sample new <ID>`."
        )

    payloads = []
    for name in samples:
        try:
            result = scan_sample(layout.root, name)
        except FileNotFoundError as exc:
            raise click.ClickException(str(exc)) from exc

        register = layout.sample(name) / "sample.yaml"
        existing = {}
        if register.is_file():
            existing = yaml.safe_load(register.read_text(encoding="utf-8")) or {}

        document = result.as_dict(
            title=existing.get("title", name),
            beamtime=existing.get("beamtime") or config.beamtime,
            created=existing.get("created", ""),
        )
        if write:
            register.write_text(
                yaml.safe_dump(document, sort_keys=False, default_flow_style=False),
                encoding="utf-8",
            )

        payloads.append(
            {
                "sample": name,
                "steady_runs": sorted(result.steady),
                "series": [s.as_dict() for s in result.series],
                "documented_but_absent": result.documented_but_absent,
                "present_but_undocumented": result.present_but_undocumented,
                "unreadable": result.unreadable,
            }
        )

        if not as_json:
            _echo_scan(name, result, register, layout.root, write)

    if as_json:
        click.echo(_json.dumps(payloads, indent=2))


def _echo_scan(name, result, register, root, write) -> None:
    """Print one sample's scan."""
    click.echo(f"{name}")
    if result.steady:
        for run in sorted(result.steady):
            entry = result.steady[run]
            parts = []
            if entry.combined:
                parts.append("combined")
            if entry.partials:
                parts.append(f"{len(entry.partials)} segment(s)")
            click.echo(f"  steady  {run}  {', '.join(parts)}")
    for series in result.series:
        span = ""
        if series.t_step is not None:
            span = f", t = {series.t_start:g}..{series.t_stop:g} s every {series.t_step:g} s"
        click.echo(
            f"  series  {series.run or '?'}  {series.n_slices} slice(s) "
            f"[{series.kind}]{span}"
        )
    if not result.steady and not result.series:
        click.echo("  no data found; copy reduced files into data/steady and data/tnr")

    # The interesting part: where prose and disk disagree.
    if result.documented_but_absent:
        click.echo(
            f"  ! sample.md mentions {result.documented_but_absent} with no data on disk"
        )
    if result.present_but_undocumented:
        click.echo(
            f"  ! data on disk for {result.present_but_undocumented}, "
            "not mentioned in sample.md"
        )
    if result.unreadable:
        click.echo(
            f"  ! {len(result.unreadable)} file(s) did not match any known convention"
        )
        for path in result.unreadable[:3]:
            click.echo(f"      {path}")

    if write:
        click.echo(f"  wrote {register.relative_to(root)}")


def run_sample_reset(
    *, sample_id: str, dry_run: bool = False, yes: bool = False
) -> None:
    """Clear a sample's fits, models and index entries together.

    Deleting a result directory by hand does not work, and the failure is
    silent: the index still records the fit, `nrw ls` reports it BROKEN
    forever, and an unattended session -- which reads the index rather than the
    directory -- keeps numbering from models that are no longer there. A sample
    whose output had been deleted still started its next model at `corefine6`.

    So reset is one operation over all three, or it is not a reset.

    Data and notes are deliberately untouched: `data/` is the measurement, and
    `sample.md` and `reports/` are what a person wrote. Only the derived work
    goes.

    Args:
        sample_id: The sample to reset.
        dry_run: Report what would go and change nothing.
        yes: Skip the confirmation prompt.

    Raises:
        click.ClickException: If there is no project, no such sample, or the
            sample holds a promoted fit.
    """
    import shutil

    from nr_workbench.provenance.index import FitIndex

    try:
        layout = ProjectLayout.discover()
    except ProjectNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc
    directory = layout.sample(sample_id)
    if not directory.is_dir():
        raise click.ClickException(layout.missing_sample_message(sample_id))

    index = FitIndex(layout.index_file)
    entries = index.fits(sample=sample_id)

    # A promoted fit is a citable result; resetting past one silently unpublishes
    # it. Refusing is the whole reason `nrw promote` is a separate decision.
    promoted = {
        str(entry.get("fit_id"))
        for label in {str(e.get("label")) for e in index.promotions()}
        if (entry := index.current_label(label)) is not None
        and entry.get("sample") == sample_id
    }
    if promoted:
        raise click.ClickException(
            f"{sample_id} holds a promoted fit ({', '.join(sorted(promoted))}). "
            "Resetting would delete a result something may already cite.\n"
            "Promotion is a separate decision on purpose; undo it deliberately "
            "before resetting."
        )

    results = sorted(p for p in (directory / "results").glob("*") if p.is_dir())
    models = sorted((directory / "models").glob("*"))
    models = [p for p in models if p.name != ".gitkeep"]

    if not results and not models and not entries:
        click.echo(f"{sample_id} has nothing to reset.")
        return

    click.echo(f"  {len(results)} result director(ies)")
    click.echo(f"  {len(models)} model file(s)")
    click.echo(f"  {len(entries)} index entr(ies)")
    click.secho(
        "  data/, sample.md and reports/ are left alone.",
        dim=True,
    )

    if dry_run:
        click.echo("\n  --dry-run: nothing changed.")
        return

    if not yes:
        click.confirm(f"\nReset {sample_id}? This cannot be undone", abort=True)

    for path in results:
        shutil.rmtree(path, ignore_errors=True)
    for path in models:
        path.unlink(missing_ok=True)
    forgotten = index.forget(sample_id)

    click.echo(
        f"  removed {len(results)} result(s), {len(models)} model file(s), "
        f"and forgot {forgotten} index entr(ies)."
    )
    click.echo(f"  {sample_id} is back to its data and its notes.")
