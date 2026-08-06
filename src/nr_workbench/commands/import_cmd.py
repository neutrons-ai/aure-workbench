"""``nrw import`` -- bring an existing beamtime directory into the layout."""

from __future__ import annotations

import json
from pathlib import Path

import click

from nr_workbench.project.layout import ProjectLayout, ProjectNotFoundError


def run_import(
    *,
    source: str,
    root: str | None = None,
    sample: str = "Sample1",
    write: bool = False,
    result_out: str | None = None,
    as_json: bool = False,
    verbose: bool = False,
) -> None:
    """Plan, and optionally perform, an import.

    Args:
        source: The beamtime directory to import from.
        root: Project root; discovered if omitted.
        sample: Sample to assign files that name no sample of their own.
        write: Actually create the links and copies. Off by default.
        result_out: Write the full plan here as JSON.
        as_json: Print JSON instead of a summary.
        verbose: List every planned file.

    Raises:
        click.ClickException: If there is no project, or no source directory.
    """
    from nr_workbench.project.importer import Action, apply_import, plan_import

    try:
        layout = (
            ProjectLayout(root=Path(root).resolve())
            if root
            else ProjectLayout.discover()
        )
    except ProjectNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc

    try:
        plan = plan_import(Path(source), default_sample=sample, root=layout.root)
    except FileNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc

    payload = plan.as_dict()
    if result_out:
        Path(result_out).write_text(
            json.dumps(payload, indent=2, default=str), encoding="utf-8"
        )

    if as_json:
        click.echo(json.dumps(payload, indent=2, default=str))
        return

    click.echo(f"\n  {plan.source_root}")
    click.echo(f"  layout   {plan.layout}")
    click.echo(f"  samples  {', '.join(sorted(plan.samples)) or 'none found'}")
    click.echo()
    click.echo(f"  {plan.links:4d} file(s) to symlink   (data stays where it is)")
    click.echo(f"  {plan.copies:4d} file(s) to copy      (scripts and prose)")
    if plan.skips:
        click.echo(f"  {plan.skips:4d} already present      (never overwritten)")
    if plan.derived:
        click.echo(f"  {len(plan.derived):4d} previous fit output(s) left behind")
    if plan.unclassified:
        click.echo(f"  {len(plan.unclassified):4d} not recognised")

    if verbose:
        for entry in plan.planned:
            mark = {Action.LINK: "->", Action.COPY: "=>", Action.SKIP: "  "}[
                entry.action
            ]
            click.echo(f"    {mark} {entry.destination}")
        for name in plan.unclassified:
            click.echo(f"     ?  {name}")

    if plan.derived and not verbose:
        click.echo(
            "\n  Previous fit outputs are deliberately not imported. A results\n"
            "  directory here carries a manifest with input hashes, the resolved\n"
            "  environment and the exact command; a .dat from an old bumps run has\n"
            "  none of that, and putting it where `nrw whence` promises an answer\n"
            "  would fake provenance nobody recorded. Re-run the script through\n"
            "  `nrw fit run` instead -- that takes minutes and produces the real thing."
        )

    if not write:
        click.echo(
            "\n  Nothing was written. Re-run with --write to create the links.\n"
            "  Use --verbose to see every file first."
        )
        return

    written = apply_import(plan, layout.root)
    click.echo(f"\n  Created {len(written)} entr(ies) under {layout.root}")
    click.echo("\n  Next:")
    for sample_id in sorted(plan.samples):
        click.echo(f"    nrw sample scan {sample_id}")
    click.echo("    nrw data overlap <sample>      check the segments agree")
