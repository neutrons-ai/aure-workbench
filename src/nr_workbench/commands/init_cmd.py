"""``nrw init`` -- scaffold or upgrade a workbench project."""

from __future__ import annotations

import sys
from pathlib import Path

import click

from nr_workbench.harness import DEFAULT_HARNESSES, resolve
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
    "aure-first-fit",
    "analyst-handoff",
    "analysis-provenance",
    "neutron-reflectometry",
    "refl-bl4b-instrument",
    "refl-reduced-headers",
    "refl1d-script-review",
    "sample-broadening",
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

    The harness set comes from ``context``: the shared project tree is planned
    first, then one subtree per selected harness, so a project scaffolded for
    two assistants gets both their instruction files and neither's is special.

    Args:
        context: Template substitution values, including the harness set.
        include_skills: Install the bundled skills and their dispatchers.
        skill_names: Which bundled skills to install.

    Returns:
        Planned files: project templates, harness templates, then skills.

    Raises:
        HarnessError: If the context names an unknown harness.
        SkillError: If a requested skill is not bundled.
        TemplateError: If the packaged template tree is missing.
    """
    harnesses = resolve(context.harnesses)

    planned = render_tree("project", context)
    for harness in harnesses:
        if harness.template_subdir is None:
            continue
        planned.extend(render_tree(f"harness/{harness.template_subdir}", context))
    planned.append(_plan_schema())

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
            for relpath, content in plan_skill_files(
                skill, harnesses=context.harnesses
            ):
                planned.append(
                    PlannedFile(
                        relpath=relpath,
                        content=content,
                        template_id=f"skill/{skill.name}/{relpath}",
                    )
                )

    return planned


def _plan_schema() -> PlannedFile:
    """Plan the project's copy of the `nrw-model/1` JSON Schema.

    It is not a template: its content is generated from the pydantic models,
    so it changes when they do. Routing it through the scaffold anyway is what
    makes it *maintained* rather than merely written once -- re-running `nrw
    init` after a package upgrade replaces a stale copy, `--check` reports it
    as pending, and a copy the user has edited is left alone like any other.

    Without it, `.vscode/settings.json` points `samples/*/models/*.yaml` at a
    file that does not exist, and every spec in the project shows "Unable to
    load schema" with no editor validation at all.

    Returns:
        The planned file for ``.nrw/schema/nrw-model-1.json``.
    """
    from nr_workbench.spec.schema import PROJECT_SCHEMA_RELPATH, schema_bytes

    return PlannedFile(
        relpath=PROJECT_SCHEMA_RELPATH,
        content=schema_bytes(),
        template_id="schema/nrw-model-1.json",
    )


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
    nested: bool = False,
    harnesses: tuple[str, ...] = (),
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
        nested: Scaffold here even if an ancestor is already a project.
        harnesses: Coding assistants to scaffold for. Empty keeps whatever the
            project records, or the default set for a new one.

    Raises:
        click.ClickException: If this would nest one project inside another
            and ``nested`` was not passed.
        SystemExit: With code 1 on a scaffold error, or 2 when ``check`` finds
            pending changes.
    """
    root = Path(path).resolve()
    if not (root / "nrw.toml").is_file():
        _refuse_if_nested(root, allow=nested)

    try:
        context = _build_context(
            root,
            project_name=project_name,
            beamtime=beamtime,
            ipts=ipts,
            harnesses=harnesses,
        )
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

    _report(report, root, check=check, harnesses=context.harnesses)

    if not check:
        _install_toolpath(root)

    if check and report.changed:
        sys.exit(2)


def _install_toolpath(root: Path) -> None:
    """Record where ``nrw`` lives on this machine, and say so when it matters.

    Outside the scaffold engine on purpose: the content is machine-specific, so
    a lock entry for it would report a pending upgrade on every other machine,
    and `nrw init --check` in CI would never be clean.

    Args:
        root: Project root, already scaffolded.
    """
    from nr_workbench.project import toolpath

    report = toolpath.install(root)
    if report.executable is None:
        return
    if report.settings_note:
        click.secho(f"  ! {report.settings_note}", fg="yellow")

    if toolpath.resolvable_in_fresh_shell():
        return

    # The failure this catches is specific and was expensive: an interactive
    # assistant session started from an editor inherits none of the environment
    # that put `nrw` on PATH, and spends its first several turns looking for it.
    click.echo()
    click.secho("  ! `nrw` is not on the PATH of a freshly started shell.", fg="yellow")
    click.echo(
        "    An assistant session started from your editor will not find it.\n"
        f"    Written for them: {toolpath.SHIM_RELPATH} and "
        f"${toolpath.NRW_BIN_ENV} in {toolpath.LOCAL_SETTINGS}.\n"
        "    To make bare `nrw` work in those sessions too: `nrw doctor --fix-path`."
    )


def _refuse_if_nested(root: Path, *, allow: bool) -> None:
    """Refuse to scaffold a project inside another project's tree.

    ``root`` has no ``nrw.toml`` of its own -- callers check that first, since
    re-running ``init`` in place on an existing project is the supported
    upgrade path, not the case this guards against. What this catches is the
    other way in: `cd` into a sample's data directory out of habit (or a
    session composing a path wrong) and run `nrw init` there. Nothing here
    would stop it from succeeding -- it would just produce a second
    ``nrw.toml``, a second ``.nrw/`` state directory and a second ``samples/``
    tree, nested inside the first, with no code anywhere that reconciles them.

    Args:
        root: The directory about to be scaffolded.
        allow: Skip the refusal. There is almost never a reason to.

    Raises:
        click.ClickException: If an ancestor of ``root`` is already a
            project and ``allow`` is False.
    """
    if allow:
        return

    from nr_workbench.project.layout import ProjectNotFoundError, find_project_root

    try:
        existing = find_project_root(root.parent)
    except ProjectNotFoundError:
        return

    raise click.ClickException(
        f"{root} is inside an existing project at {existing}.\n"
        "Scaffolding here would nest one project inside another: a second "
        "nrw.toml, a second .nrw/, a second samples/ tree, none of it "
        "reconciled with the first.\n"
        "If you meant to add a sample to the existing project, use "
        "`nrw sample new <ID>` instead.\n"
        "If you really want a project here, pass --nested."
    )


def _build_context(
    root: Path,
    *,
    project_name: str | None,
    beamtime: str | None,
    ipts: str | None,
    harnesses: tuple[str, ...] = (),
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
        harnesses: Explicit harness names from ``--harness``, or empty to keep
            what the project records.

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

    # --harness wins, then what the project already records, then the default.
    # Resolving here rather than at the call site normalises order and case, so
    # `--harness copilot --harness claude` and a reordered nrw.toml both plan
    # the same files in the same sequence.
    selected = harnesses or (existing.harnesses if existing else DEFAULT_HARNESSES)

    return RenderContext(
        project_name=project_name or (existing.name if existing else root.name),
        facility=existing.facility if existing else "SNS",
        instrument=existing.instrument if existing else "REF_L",
        beamtime=beamtime or (existing.beamtime if existing else None),
        ipts=ipts or (existing.ipts if existing else None),
        created=created,
        harnesses=tuple(h.name for h in resolve(selected)),
    )


def _plan_sample(context: RenderContext, sample_id: str) -> list[PlannedFile]:
    """Plan the files for one sample directory."""
    from nr_workbench.commands.sample import plan_sample_files

    return plan_sample_files(context, sample_id)


def _report(
    report: ScaffoldReport,
    root: Path,
    *,
    check: bool,
    harnesses: tuple[str, ...] = (),
) -> None:
    """Print a human summary of a scaffold run."""
    created = report.count(Outcome.CREATE)
    upgraded = report.count(Outcome.UPGRADE)
    unchanged = report.count(Outcome.UNCHANGED)
    drifted = report.drifted
    untracked = report.count(Outcome.UNTRACKED)

    verb = "would " if check else ""
    click.echo(f"{'Checked' if check else 'Scaffolded'} {root}")

    if harnesses:
        titles = ", ".join(h.title for h in resolve(harnesses))
        click.echo(f"  for        {titles}")

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
