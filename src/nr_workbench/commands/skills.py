"""``nrw skills`` -- inspect and re-install the project's skills."""

from __future__ import annotations

import click

from nr_workbench.project.layout import ProjectLayout, ProjectNotFoundError
from nr_workbench.project.scaffold import Outcome, PlannedFile, apply_scaffold
from nr_workbench.skills_install import (
    SkillError,
    bundled_skills_root,
    discover_skills,
    plan_skill_files,
)


def run_skills_list(*, bundled: bool = False) -> None:
    """List skills, either installed in this project or bundled in the package.

    Args:
        bundled: List what nr-workbench ships instead of what is installed.

    Raises:
        click.ClickException: If the bundled skills cannot be read, or there is
            no project and ``bundled`` was not requested.
    """
    if bundled:
        try:
            skills = discover_skills()
        except SkillError as exc:
            raise click.ClickException(str(exc)) from exc
        _print_skills((s.name, s.domain, s.description) for s in skills)
        return

    try:
        layout = ProjectLayout.discover()
    except ProjectNotFoundError as exc:
        raise click.ClickException(
            f"{exc}\nUse --bundled to list what nr-workbench ships."
        ) from exc

    try:
        installed = discover_skills(layout.skills_dir)
    except SkillError as exc:
        raise click.ClickException(str(exc)) from exc

    if not installed:
        click.echo("No skills installed. Run `nrw init` or `nrw skills sync`.")
        return
    _print_skills((s.name, s.domain, s.description) for s in installed)


def _print_skills(rows) -> None:
    """Print a name/domain/description table."""
    rows = list(rows)
    width = max((len(name) for name, _, _ in rows), default=4)
    for name, domain, description in rows:
        summary = " ".join(description.split())
        if len(summary) > 80:
            summary = summary[:79] + "…"
        click.echo(f"  {name:<{width}}  [{domain}]  {summary}")


def run_skills_sync(*, force: bool = False) -> None:
    """Re-install the bundled skills into the project.

    Locally edited skills are never silently replaced -- the updated copy is
    written alongside as ``.nrw-new`` so a scientist's addition to a skill is
    not lost to a package upgrade.

    Args:
        force: Overwrite locally edited skills, backing the originals up.

    Raises:
        click.ClickException: If there is no project here or a skill fails to load.
    """
    try:
        layout = ProjectLayout.discover()
    except ProjectNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc

    try:
        skills = discover_skills()
    except SkillError as exc:
        raise click.ClickException(str(exc)) from exc

    planned = [
        PlannedFile(
            relpath=relpath,
            content=content,
            template_id=f"skill/{skill.name}/{relpath}",
        )
        for skill in skills
        for relpath, content in plan_skill_files(skill)
    ]

    report = apply_scaffold(layout.root, planned, force=force)

    click.echo(
        f"{report.count(Outcome.CREATE)} added, "
        f"{report.count(Outcome.UPGRADE)} updated, "
        f"{report.count(Outcome.UNCHANGED)} unchanged"
    )
    if report.drifted:
        click.echo(f"  {len(report.drifted)} locally edited file(s) left alone:")
        for result in report.drifted:
            side = f" -> {result.wrote_alongside}" if result.wrote_alongside else ""
            click.echo(f"    {result.relpath}{side}")
        click.echo("  Use --force to overwrite them.")


def run_skills_path() -> None:
    """Print the directory of the skills bundled with nr-workbench.

    Raises:
        click.ClickException: If the bundled skills directory is missing, which
            means the wheel was built without its package-data.
    """
    try:
        click.echo(str(bundled_skills_root()))
    except SkillError as exc:
        raise click.ClickException(str(exc)) from exc
