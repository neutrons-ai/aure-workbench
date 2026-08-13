"""``nrw audience`` -- read and set who this project is written for."""

from __future__ import annotations

import click

from nr_workbench.project import audience as audience_mod
from nr_workbench.project.layout import ProjectLayout, ProjectNotFoundError


def _layout() -> ProjectLayout:
    """Discover the project, or fail with guidance."""
    try:
        return ProjectLayout.discover()
    except ProjectNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc


def run_audience(
    *,
    assignments: tuple[str, ...] = (),
    ask: bool = False,
    notes: str | None = None,
    show_guidance: bool = False,
) -> None:
    """Show or change the ``[audience]`` block.

    Args:
        assignments: ``axis=value`` strings, as from repeated ``--set``.
        ask: Walk through the axes interactively.
        notes: Replace the free-text note.
        show_guidance: Print the instructions these settings imply.

    Raises:
        click.ClickException: If there is no project here, or an assignment
            names an unknown axis or value.
    """
    layout = _layout()
    current = audience_mod.load(layout.root)
    values = current.as_dict()

    changed = False

    for assignment in assignments:
        axis, separator, value = assignment.partition("=")
        if not separator:
            raise click.ClickException(
                f"--set takes axis=value, not {assignment!r}. "
                f"Axes: {', '.join(audience_mod.AXES)}."
            )
        try:
            values[axis.strip()] = audience_mod.validate(axis.strip(), value)
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc
        changed = True

    if ask:
        click.echo(
            "Who reads what comes out of this project? These change how much "
            "is explained,\nand in what order. Enter keeps the current value.\n"
        )
        for axis, (allowed, help_text) in audience_mod.AXES.items():
            click.echo(f"  {help_text}")
            answer = click.prompt(
                f"  {axis} ({'/'.join(allowed)})",
                default=values[axis],
                show_default=True,
            )
            try:
                values[axis] = audience_mod.validate(axis, answer)
            except ValueError as exc:
                raise click.ClickException(str(exc)) from exc
            click.echo()
        values["notes"] = click.prompt(
            "  Anything the axes cannot express",
            default=values["notes"],
            show_default=bool(values["notes"]),
        )
        changed = True

    if notes is not None:
        values["notes"] = notes
        changed = True

    updated = audience_mod.Audience(
        reflectometry=values["reflectometry"],
        statistics=values["statistics"],
        domain=values["domain"],
        role=values["role"],
        notes=values["notes"],
        declared=True,
    )

    if changed:
        try:
            audience_mod.write(layout.root, updated)
        except OSError as exc:
            raise click.ClickException(f"Could not write nrw.toml: {exc}") from exc
        click.echo("  nrw.toml updated.")
        click.echo()

    shown = updated if changed else current
    width = max(len(axis) for axis in audience_mod.AXES)
    for axis in audience_mod.AXES:
        click.echo(f"  {axis:<{width}}  {shown.as_dict()[axis]}")
    if shown.notes:
        click.echo(f"  {'notes':<{width}}  {shown.notes}")

    if not shown.declared and not changed:
        click.echo()
        click.secho(
            "  These are defaults -- nobody has said. `nrw audience --ask` "
            "walks through them.",
            fg="yellow",
        )

    if show_guidance:
        click.echo()
        click.echo("  What this asks an assistant to do:")
        for line in audience_mod.guidance(shown):
            click.echo(f"    - {line}")
