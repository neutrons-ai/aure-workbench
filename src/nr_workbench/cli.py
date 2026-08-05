"""Command-line entry point for nr-workbench.

A single Click group, exposed as both ``nr-workbench`` (readable in docs) and
``nrw`` (typeable at a prompt).

Every subcommand module is imported *lazily*, inside the callback that needs
it. That is not stylistic: importing ``aure`` costs roughly 1.5-3 seconds
because ``aure/__init__.py`` eagerly pulls in langchain-core, periodictable and
scipy, and refl1d/matplotlib are not cheap either. ``nrw --help`` must stay
instant, so nothing heavy may be imported at module scope here.
``tests/test_cli.py::test_help_does_not_import_heavy_modules`` enforces it.
"""

from __future__ import annotations

import click

from nr_workbench import __version__

CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}


@click.group(context_settings=CONTEXT_SETTINGS)
@click.version_option(__version__, "-V", "--version", prog_name="nr-workbench")
def main() -> None:
    """Neutron reflectometry workbench for SNS REF_L (BL-4B).

    Start with `nrw init` in an empty directory (or on top of an existing
    beamtime folder), then `nrw sample new <ID>` and copy your reduced data
    into the sample's data/ folders.
    """


@main.command("init")
@click.argument(
    "path",
    type=click.Path(file_okay=False, path_type=str),
    default=".",
    required=False,
)
@click.option(
    "--name",
    "project_name",
    default=None,
    help="Project name [default: directory name].",
)
@click.option("--beamtime", default=None, help="Beamtime label, e.g. 'jen-june2026'.")
@click.option("--ipts", default=None, help="IPTS proposal number, e.g. 'IPTS-34567'.")
@click.option(
    "--sample",
    "sample_ids",
    multiple=True,
    help="Create a sample directory (repeatable).",
)
@click.option(
    "--check",
    is_flag=True,
    help="Report what would change and exit non-zero if anything would.",
)
@click.option(
    "--diff", "show_diff", is_flag=True, help="Print unified diffs instead of writing."
)
@click.option(
    "--force",
    is_flag=True,
    help="Overwrite user-edited files (backed up under .nrw/backups/).",
)
@click.option("--no-skills", is_flag=True, help="Skip installing the bundled skills.")
def init_command(**kwargs: object) -> None:
    """Scaffold (or upgrade) a workbench project at PATH."""
    from nr_workbench.commands.init_cmd import run_init

    run_init(**kwargs)  # type: ignore[arg-type]


@main.command("doctor")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def doctor_command(as_json: bool) -> None:
    """Check the environment: versions, optional extras, project integrity."""
    from nr_workbench.commands.doctor import run_doctor

    run_doctor(as_json=as_json)


@main.group("sample")
def sample_group() -> None:
    """Create and inspect samples."""


@sample_group.command("new")
@click.argument("sample_id")
@click.option("--title", default=None, help="Human-readable sample title.")
@click.option(
    "--beamtime", default=None, help="Beamtime label [default: the project's]."
)
def sample_new_command(sample_id: str, title: str | None, beamtime: str | None) -> None:
    """Create samples/SAMPLE_ID/ with the standard layout and a sample.md stub."""
    from nr_workbench.commands.sample import run_sample_new

    run_sample_new(sample_id=sample_id, title=title, beamtime=beamtime)


@main.group("fit")
def fit_group() -> None:
    """Run fits and record what produced every result."""


@fit_group.command("run")
@click.argument("script", type=click.Path(exists=True, dir_okay=False))
@click.option(
    "--sample",
    default=None,
    help="Sample to record under [default: inferred from the path].",
)
@click.option(
    "--method",
    default="amoeba",
    show_default=True,
    help="Bumps fitter: amoeba, dream, lm, de, newton.",
)
@click.option("--steps", type=int, default=None, help="Maximum optimizer steps.")
@click.option("--samples", type=int, default=None, help="DREAM sample count.")
@click.option("--burn", type=int, default=None, help="DREAM burn-in.")
@click.option("--pop", type=int, default=None, help="Population size.")
@click.option(
    "--seed", type=int, default=None, help="Random seed, for a reproducible run."
)
@click.option("--note", default=None, help="Free-text note stored in the record.")
@click.option(
    "--name", "model_name", default=None, help="Model name [default: the script stem]."
)
@click.option(
    "--force", is_flag=True, help="Run even if an identical run already exists."
)
@click.option("--dry-run", is_flag=True, help="Report what would run; write nothing.")
def fit_run_command(**kwargs: object) -> None:
    """Run SCRIPT and write an immutable record of the fit.

    Works on hand-written refl1d scripts as they are: no migration, no schema.
    The script must define a module-level `problem = FitProblem(...)`.
    """
    from nr_workbench.commands.fit import run_fit_command

    run_fit_command(**kwargs)  # type: ignore[arg-type]


@main.command("whence")
@click.argument("path")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def whence_command(path: str, as_json: bool) -> None:
    """Trace PATH back to the fit that produced or consumed it.

    Accepts a figure, an artifact, a data file, a fit directory, or a fit id.
    """
    from nr_workbench.commands.provenance_cmd import run_whence

    run_whence(path=path, as_json=as_json)


@main.command("ls")
@click.option("--sample", default=None, help="Restrict to one sample.")
@click.option("--limit", type=int, default=50, show_default=True, help="Maximum rows.")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def ls_command(sample: str | None, limit: int, as_json: bool) -> None:
    """List recorded fits, newest first."""
    from nr_workbench.commands.provenance_cmd import run_ls

    run_ls(sample=sample, as_json=as_json, limit=limit)


@main.command("promote")
@click.argument("fit_id")
@click.option(
    "--as", "label", default="final", show_default=True, help="Label to apply."
)
@click.option("--reason", required=True, help="Why this fit is the answer. Required.")
@click.option("--force", is_flag=True, help="Promote even if the inputs have changed.")
def promote_command(fit_id: str, label: str, reason: str, force: bool) -> None:
    """Mark FIT_ID as the answer, recording who decided and why."""
    from nr_workbench.commands.provenance_cmd import run_promote

    run_promote(fit_id=fit_id, label=label, reason=reason, force=force)


@main.command("check")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def check_command(as_json: bool) -> None:
    """Verify project integrity: stale results, missing inputs, broken pointers."""
    from nr_workbench.commands.provenance_cmd import run_check

    run_check(as_json=as_json)


@main.group("skills")
def skills_group() -> None:
    """Manage the project's skills/ directory."""


@skills_group.command("list")
@click.option(
    "--bundled", is_flag=True, help="List the skills shipped with nr-workbench instead."
)
def skills_list_command(bundled: bool) -> None:
    """List skills installed in this project."""
    from nr_workbench.commands.skills import run_skills_list

    run_skills_list(bundled=bundled)


@skills_group.command("sync")
@click.option("--force", is_flag=True, help="Overwrite locally edited skills.")
def skills_sync_command(force: bool) -> None:
    """Re-install bundled skills, leaving locally edited ones alone."""
    from nr_workbench.commands.skills import run_skills_sync

    run_skills_sync(force=force)


@skills_group.command("path")
def skills_path_command() -> None:
    """Print the directory of the skills bundled with nr-workbench."""
    from nr_workbench.commands.skills import run_skills_path

    run_skills_path()


if __name__ == "__main__":  # pragma: no cover
    main()
