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

    # A skill that is bundled and absent looks, from inside the project, exactly
    # like a topic nobody wrote a skill for. Naming them is the whole fix: the
    # material-specific ones are the ones a given sample most needs, and the ones
    # most likely to be missing.
    try:
        available = {skill.name for skill in discover_skills()}
    except SkillError:
        return
    absent = sorted(available - {skill.name for skill in installed})
    if absent:
        click.echo()
        click.secho(f"  {len(absent)} bundled skill(s) not installed here:", dim=True)
        click.secho(f"    {', '.join(absent)}", dim=True)
        click.secho(f"    nrw skills add {absent[0]}", dim=True)


def _print_skills(rows) -> None:
    """Print a name/domain/description table."""
    rows = list(rows)
    width = max((len(name) for name, _, _ in rows), default=4)
    for name, domain, description in rows:
        summary = " ".join(description.split())
        if len(summary) > 80:
            summary = summary[:79] + "…"
        click.echo(f"  {name:<{width}}  [{domain}]  {summary}")


def run_skills_add(*, names: tuple[str, ...], force: bool = False) -> None:
    """Install named bundled skills into the project.

    ``nrw skills sync`` installs *every* bundled skill, which is the wrong tool
    when the point is to add the one this sample needs: the material-specific
    ones are deliberately not seeded, because each costs attention on every query
    that is not about it. This adds what was asked for and nothing else.

    Args:
        names: Bundled skill names.
        force: Overwrite locally edited files, backing the originals up.

    Raises:
        click.ClickException: If there is no project, or a name is not bundled.
    """
    try:
        layout = ProjectLayout.discover()
    except ProjectNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc

    try:
        bundled = {skill.name: skill for skill in discover_skills()}
    except SkillError as exc:
        raise click.ClickException(str(exc)) from exc

    unknown = [name for name in names if name not in bundled]
    if unknown:
        raise click.ClickException(
            f"Not bundled: {', '.join(unknown)}.\n"
            f"Available: {', '.join(sorted(bundled))}.\n"
            "`nrw skills list --bundled` says what each one is for."
        )

    planned = [
        PlannedFile(
            relpath=relpath,
            content=content,
            template_id=f"skill/{bundled[name].name}/{relpath}",
        )
        for name in names
        for relpath, content in plan_skill_files(bundled[name])
    ]
    report = apply_scaffold(layout.root, planned, force=force)
    click.echo(
        f"{report.count(Outcome.CREATE)} added, "
        f"{report.count(Outcome.UPGRADE)} updated, "
        f"{report.count(Outcome.UNCHANGED)} unchanged"
    )
    for name in names:
        click.echo(f"  skills/{bundled[name].domain}/{name}/SKILL.md")


def run_skills_sync(*, force: bool = False) -> None:
    """Install or re-install every bundled skill.

    Note that this adds the ones a project does not have as well as refreshing
    the ones it does --- including the material-specific skills `nrw init`
    deliberately leaves out. `nrw skills add <name>` is the targeted form.

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
