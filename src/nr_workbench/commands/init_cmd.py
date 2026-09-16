"""``nrw init`` -- scaffold or upgrade a workbench project."""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

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

if TYPE_CHECKING:
    from nr_workbench.project.toolpath import ToolPathReport

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

#: Project templates installed as a marked block inside a file the user also
#: owns, instead of as the whole file.
#:
#: Only `.gitignore`, and it is not a stylistic choice. A repository created on
#: GitHub arrives with a language `.gitignore` already in it, which made ours
#: `UNTRACKED` and silently skipped -- and the rules it withheld are the ones
#: that keep `.nrw/bin/nrw` and `.claude/settings.local.json`, both of which
#: hold this machine's absolute paths, out of a shared repository. See
#: `nr_workbench.project.ignore` for the incident that established this.
MERGED_TEMPLATES = frozenset({".gitignore"})


def _as_merged_block(planned: PlannedFile) -> PlannedFile:
    """Re-plan a template as a marked block rather than a whole file.

    Args:
        planned: The rendered template, whose content becomes the block body.

    Returns:
        The same file with its content wrapped in the managed markers and
        ``merge`` set, so the scaffold engine splices it in.
    """
    from nr_workbench.project.ignore import wrap

    body = planned.content.decode("utf-8")
    return replace(planned, content=wrap(body).encode("utf-8"), merge=True)


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

    planned = [
        _as_merged_block(file) if file.relpath in MERGED_TEMPLATES else file
        for file in render_tree("project", context)
    ]
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

    _warn_if_committable(report)
    _warn_if_path_is_someone_elses(report)

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


def _warn_if_committable(report: ToolPathReport) -> None:
    """Say so when the files we just wrote could be committed.

    Belt and braces beside the merged `.gitignore`, which covers the common
    case but not every one: the managed block can be deleted, a global ignore
    file can contradict it, and -- the state the field failure was actually in
    -- the files can already be *tracked*, which no ignore rule affects.

    Without this the failure is invisible. The files work perfectly for the
    person who wrote them and break only for whoever clones next.

    Args:
        report: The :class:`~nr_workbench.project.toolpath.ToolPathReport`.
    """
    if not report.unignored:
        return

    click.echo()
    click.secho(
        "  ! These hold this machine's absolute paths, and git would let them "
        "be committed:",
        fg="red",
    )
    for path in report.unignored:
        suffix = "  (already tracked)" if path in report.tracked else ""
        click.echo(f"      {path}{suffix}")

    click.echo(
        "    Committing them breaks the next person to clone this project: "
        "their sessions\n"
        "    get this machine's paths, and on a shared filesystem that "
        "resolves instead\n"
        "    of failing."
    )
    if report.tracked:
        # An ignore rule has no effect on a path already in the index, so
        # "add it to .gitignore" is advice that appears not to work. Say the
        # other half of it.
        click.echo("    Already tracked, so an ignore rule alone will not help:")
        click.echo(f"      git rm --cached {' '.join(report.tracked)}")
    rest = [path for path in report.unignored if path not in report.tracked]
    if rest:
        click.echo("    Add to .gitignore, inside the nr-workbench managed block:")
        for path in rest:
            click.echo(f"      {path}")


def _warn_if_path_is_someone_elses(report: ToolPathReport) -> None:
    """Say so when the settings ``PATH`` leads with another machine's install.

    `install` refreshes ``NRW_BIN`` but leaves an existing ``PATH`` alone by
    design, so a committed ``PATH`` survives a clone intact and silently wins
    over the new machine's environment.

    Args:
        report: The :class:`~nr_workbench.project.toolpath.ToolPathReport`.
    """
    stale = report.stale_path
    if not stale:
        return

    from nr_workbench.project import toolpath

    click.echo()
    click.secho(
        f"  ! {toolpath.LOCAL_SETTINGS} sets a PATH that starts with:", fg="yellow"
    )
    click.echo(f"      {stale}")
    click.echo(
        "    That is not where this machine's `nrw` lives, which usually means "
        "the file\n"
        "    was committed on someone else's machine and cloned here. `nrw "
        "init` does not\n"
        "    rewrite a PATH you may have set deliberately.\n"
        "    To replace it with this machine's: `nrw doctor --fix-path`. To drop "
        "it, delete\n"
        f"    the PATH entry from {toolpath.LOCAL_SETTINGS}."
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
    untracked = [f for f in report.files if f.outcome is Outcome.UNTRACKED]
    merged = [f for f in report.files if f.outcome is Outcome.MERGE]

    verb = "would " if check else ""
    click.echo(f"{'Checked' if check else 'Scaffolded'} {root}")

    if harnesses:
        titles = ", ".join(h.title for h in resolve(harnesses))
        click.echo(f"  for        {titles}")

    if created:
        click.echo(f"  {verb}create   {created} file(s)")
    if upgraded:
        click.echo(f"  {verb}upgrade  {upgraded} file(s)")
    if merged:
        names = ", ".join(f.relpath for f in merged)
        click.echo(f"  {verb}merge    {names} (the nr-workbench block only)")
    if unchanged:
        click.echo(f"  unchanged  {unchanged} file(s)")
    if untracked:
        # Named, not merely counted. The bare count was the whole problem: with
        # `.gitignore` in this list the run said "left alone 1 pre-existing
        # file(s)", and nothing told the reader that the file keeping
        # machine-local absolute paths out of the repository was the one being
        # skipped.
        names = ", ".join(f.relpath for f in untracked)
        click.echo(f"  left alone {names} (yours, not installed by nrw)")

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
