"""``nrw init`` -- scaffold or upgrade a workbench project."""

from __future__ import annotations

import sys
from pathlib import Path

import click

from nr_workbench.project.render import RenderContext, render_tree
from nr_workbench.project.scaffold import (
    Outcome,
    PlannedFile,
    ScaffoldReport,
    apply_scaffold,
)
from nr_workbench.skills_install import discover_skills, plan_skill_files

#: Skills installed by default.
#:
#: The bar is "applies to almost any task on this beamline", not "is good".
#: The retriever scores a query against every installed skill's tags, so a
#: skill that is only sometimes relevant costs attention every time it is not.
#: The material-specific ones -- metal oxides, polymers, solvents -- and the
#: strategy one are bundled but left to `nrw skills sync`, because which of
#: them matters depends on the sample.
SEED_SKILLS = (
    "nr-workbench-project",
    "analysis-provenance",
    "neutron-reflectometry",
    "refl-bl4b-instrument",
    "refl-reduced-headers",
    "refl1d-script-review",
    "thin-layer-degeneracy",
    "steady-state-corefinement",
    "tnr-change-assessment",
    "tnr-amplitude",
    "tnr-variogram",
    "tnr-chi2",
    "tnr-pca-kl",
    "nrw-model-spec",
    "tnr-functional-constraints",
)

#: Created empty so the layout is legible before any data arrives. Needs a
#: .gitkeep because git cannot track an empty directory. `docs/` is not listed
#: -- ground_truths.md already creates it.
SEED_DIRS = ("samples",)


def plan_project_files(
    context: RenderContext,
    *,
    include_skills: bool = True,
    skill_names: tuple[str, ...] = SEED_SKILLS,
) -> list[PlannedFile]:
    """Build the full list of files `nrw init` would install.

    Args:
        context: Template substitution values.
        include_skills: Install the bundled skills and their dispatchers.
        skill_names: Which bundled skills to install.

    Returns:
        Planned files, project templates first, then skills.

    Raises:
        SkillError: If a requested skill is not bundled.
        TemplateError: If the packaged template tree is missing.
    """
    planned = render_tree("project", context)

    for relpath in (f"{name}/.gitkeep" for name in SEED_DIRS):
        planned.append(
            PlannedFile(
                relpath=relpath,
                content=b"",
                template_id=f"dir/{relpath}",
            )
        )

    if include_skills:
        available = {skill.name: skill for skill in discover_skills()}
        for name in skill_names:
            skill = available.get(name)
            if skill is None:
                from nr_workbench.skills_install import SkillError

                raise SkillError(
                    f"Bundled skill '{name}' not found. Available: {sorted(available)}"
                )
            for relpath, content in plan_skill_files(skill):
                planned.append(
                    PlannedFile(
                        relpath=relpath,
                        content=content,
                        template_id=f"skill/{skill.name}/{relpath}",
                    )
                )

    return planned


def run_init(
    *,
    path: str = ".",
    project_name: str | None = None,
    beamtime: str | None = None,
    ipts: str | None = None,
    sample_ids: tuple[str, ...] = (),
    check: bool = False,
    show_diff: bool = False,
    force: bool = False,
    no_skills: bool = False,
) -> None:
    """Scaffold or upgrade a project, then report what changed.

    Args:
        path: Directory to scaffold. Created if it does not exist.
        project_name: Project name. Defaults to the directory name.
        beamtime: Optional beamtime label.
        ipts: Optional IPTS proposal identifier.
        sample_ids: Sample directories to create as part of the scaffold.
        check: Plan only, write nothing, and exit non-zero if anything would
            change. Intended for CI.
        show_diff: Print unified diffs instead of a summary table.
        force: Overwrite user-edited files, backing them up first.
        no_skills: Skip installing the bundled skills.

    Raises:
        SystemExit: With code 1 on a scaffold error, or 2 when ``check`` finds
            pending changes.
    """
    root = Path(path).resolve()
    context = _build_context(
        root,
        project_name=project_name,
        beamtime=beamtime,
        ipts=ipts,
    )

    try:
        planned = plan_project_files(context, include_skills=not no_skills)
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc

    for sample_id in sample_ids:
        planned.extend(_plan_sample(context, sample_id))

    diffs: list[str] = []
    report = apply_scaffold(
        root,
        planned,
        dry_run=check,
        show_diff=show_diff,
        force=force,
        diff_sink=diffs if show_diff else None,
    )

    if show_diff:
        for diff in diffs:
            click.echo(diff, nl=False)

    _report(report, root, check=check)

    if check and report.changed:
        sys.exit(2)


def _build_context(
    root: Path,
    *,
    project_name: str | None,
    beamtime: str | None,
    ipts: str | None,
) -> RenderContext:
    """Build the render context, preserving existing project identity.

    Re-running `init` must be a genuine no-op when nothing has changed. Two
    things would otherwise break that:

    * ``created`` is stamped into ``nrw.toml`` and ``README.md``. Regenerating
      it every run makes those files differ on every invocation, so `init`
      reports an upgrade forever -- and the field would come to mean "last
      init" rather than "created", which is not what a provenance record wants.
    * Omitting ``--beamtime`` on a later run would silently blank a value the
      user set on the first one.

    So existing values win unless explicitly overridden on the command line.

    Args:
        root: Project root, which may or may not already hold an ``nrw.toml``.
        project_name: Explicit project name, or None to keep/derive it.
        beamtime: Explicit beamtime label, or None to keep the existing one.
        ipts: Explicit IPTS identifier, or None to keep the existing one.

    Returns:
        The render context to scaffold with.
    """
    from nr_workbench.project.config import ProjectConfigError, load_config

    existing = None
    if (root / "nrw.toml").is_file():
        try:
            existing = load_config(root)
        except ProjectConfigError:
            # A malformed nrw.toml must not block a repair run; fall back to
            # defaults and let the scaffold offer a fresh copy alongside it.
            existing = None

    created = ""
    if existing is not None:
        created = str(existing.raw.get("project", {}).get("created", "") or "")

    return RenderContext(
        project_name=project_name or (existing.name if existing else root.name),
        facility=existing.facility if existing else "SNS",
        instrument=existing.instrument if existing else "REF_L",
        beamtime=beamtime or (existing.beamtime if existing else None),
        ipts=ipts or (existing.ipts if existing else None),
        created=created,
    )


def _plan_sample(context: RenderContext, sample_id: str) -> list[PlannedFile]:
    """Plan the files for one sample directory."""
    from nr_workbench.commands.sample import plan_sample_files

    return plan_sample_files(context, sample_id)


def _report(report: ScaffoldReport, root: Path, *, check: bool) -> None:
    """Print a human summary of a scaffold run."""
    created = report.count(Outcome.CREATE)
    upgraded = report.count(Outcome.UPGRADE)
    unchanged = report.count(Outcome.UNCHANGED)
    drifted = report.drifted
    untracked = report.count(Outcome.UNTRACKED)

    verb = "would " if check else ""
    click.echo(f"{'Checked' if check else 'Scaffolded'} {root}")

    if created:
        click.echo(f"  {verb}create   {created} file(s)")
    if upgraded:
        click.echo(f"  {verb}upgrade  {upgraded} file(s)")
    if unchanged:
        click.echo(f"  unchanged  {unchanged} file(s)")
    if untracked:
        click.echo(
            f"  left alone {untracked} pre-existing file(s) not installed by nrw"
        )

    if drifted:
        click.echo()
        click.echo(f"  {len(drifted)} file(s) you have edited were NOT overwritten:")
        for result in drifted:
            side = f" -> {result.wrote_alongside}" if result.wrote_alongside else ""
            click.echo(f"    {result.relpath}{side}")
        click.echo("  Review the .nrw-new copies, then merge or delete them.")
        click.echo(
            "  Use --force to overwrite (originals are backed up under .nrw/backups/)."
        )

    if check:
        if report.changed:
            click.echo()
            click.echo(
                f"{len(report.changed)} file(s) pending. Run `nrw init` to apply."
            )
        else:
            click.echo("  up to date")
        return

    if created and not upgraded:
        click.echo()
        click.echo("Next:")
        click.echo("  nrw doctor              check the environment")
        click.echo("  nrw sample new <ID>     create a sample and its data folders")
