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
