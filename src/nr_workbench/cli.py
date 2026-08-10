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
    # Every forcing flag in this tool exists because a check said no, so an
    # unattended run may not use any of them. Checked here rather than in each
    # command: there are eight of them today and the next one would be added
    # without remembering this, which is precisely how a limit becomes a
    # limit-shaped comment.
    import os
    import sys

    if os.environ.get("NRW_AGENT") and any(
        arg == "--force" or arg.startswith("--force=") for arg in sys.argv[1:]
    ):
        from nr_workbench.agent.guard import refuse_if_agent

        refuse_if_agent("force")


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


@sample_group.command("scan")
@click.argument("sample_id", required=False)
@click.option(
    "--no-write",
    "write",
    flag_value=False,
    default=True,
    help="Report only; do not update sample.yaml.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def sample_scan_command(sample_id: str | None, write: bool, as_json: bool) -> None:
    """Register the data on disk for SAMPLE_ID, or every sample.

    Also reports where sample.md and the files disagree -- a run written up
    with no data, or data nobody wrote up.
    """
    from nr_workbench.commands.sample import run_sample_scan

    run_sample_scan(sample_id=sample_id, as_json=as_json, write=write)


@main.group("model")
def model_group() -> None:
    """Write, check, and generate fit scripts from a model spec."""


@model_group.command("validate")
@click.argument("spec", type=click.Path(exists=True, dir_okay=False))
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
@click.option(
    "--result-out", default=None, help="Write an ndip-tool-result/1 manifest here."
)
def model_validate_command(**kwargs: object) -> None:
    """Check SPEC against the project: paths, layers, bounds, degrees of freedom."""
    from nr_workbench.commands.model import run_validate

    run_validate(**kwargs)  # type: ignore[arg-type]


@model_group.command("preview")
@click.argument("spec", type=click.Path(exists=True, dir_okay=False))
@click.option(
    "--build", is_flag=True, help="Also build the problem and report the initial chisq."
)
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def model_preview_command(**kwargs: object) -> None:
    """Show the parameter table SPEC resolves to, without writing anything.

    The check worth running before a long fit: it says how many parameters will
    vary and how they are grouped.
    """
    from nr_workbench.commands.model import run_preview

    run_preview(**kwargs)  # type: ignore[arg-type]


@model_group.command("generate")
@click.argument("spec", type=click.Path(exists=True, dir_okay=False))
@click.option(
    "--out", default=None, help="Output path [default: the spec with a .py suffix]."
)
@click.option("--force", is_flag=True, help="Overwrite a hand-edited script.")
def model_generate_command(**kwargs: object) -> None:
    """Write the standalone refl1d script for SPEC."""
    from nr_workbench.commands.model import run_generate

    run_generate(**kwargs)  # type: ignore[arg-type]


@model_group.command("new")
@click.argument("sample")
@click.option("--name", required=True, help="Model name; also the filename.")
@click.option("--out", default=None, help="Explicit output path.")
@click.option("--force", is_flag=True, help="Overwrite an existing spec.")
@click.option(
    "--from-notes",
    is_flag=True,
    help="Ask a configured LLM endpoint to propose the stack from sample.md.",
)
@click.option(
    "--print-prompt",
    is_flag=True,
    help="Print the instruction to hand a coding assistant, and write nothing.",
)
def model_new_command(**kwargs: object) -> None:
    """Scaffold a spec for SAMPLE from the data found on disk.

    The stack is a placeholder for you to correct; everything else -- states,
    series, file discovery -- is filled in from what `nrw sample scan` sees.
    """
    from nr_workbench.commands.model import run_new

    run_new(**kwargs)  # type: ignore[arg-type]


@model_group.command("fork")
@click.argument("spec", type=click.Path(exists=True, dir_okay=False))
@click.option("--name", default=None, help="Name for the forked script.")
@click.option("--out", default=None, help="Explicit output path.")
def model_fork_command(**kwargs: object) -> None:
    """Take manual ownership of SPEC's generated script.

    The supported way to hand-edit. The fork is yours to change, and stays
    fully tracked by `nrw fit run` -- escaping the generator must not mean
    escaping provenance.
    """
    from nr_workbench.commands.model import run_fork

    run_fork(**kwargs)  # type: ignore[arg-type]


@model_group.command("schema")
@click.option("--out", default=None, help="Where to write it; '-' for stdout.")
def model_schema_command(out: str | None) -> None:
    """Emit the JSON Schema for nrw-model/1."""
    from nr_workbench.commands.model import run_schema

    run_schema(out=out)


@model_group.command("forms")
def model_forms_command() -> None:
    """List the constraint forms available for a series."""
    from nr_workbench.commands.model import run_forms

    run_forms()


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
@click.option(
    "--parallel",
    type=int,
    default=0,
    show_default=True,
    help="CPUs to use; 0 means all of them, 1 forces serial.",
)
@click.option(
    "--plots",
    is_flag=True,
    help="Let bumps render its PNGs (off by default; it runs before the chain "
    "is saved, so a failure costs the uncertainty output).",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit the fit record as JSON. The write path had no machine-readable "
    "output, so a driver could not tell whether a fit succeeded except by "
    "re-reading the index.",
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


@main.group("tnr")
def tnr_group() -> None:
    """Assess temporal change in a time-resolved run."""


def _reference_options(func):
    """Attach the block-selection options every tNR command shares.

    Defined here rather than imported so that `nrw --help` still touches
    nothing heavy: importing anything under `nr_workbench.tnr` pulls in numpy
    and every metric through the package __init__.
    """
    options = (
        click.option(
            "--min-duration",
            type=float,
            default=5.0,
            show_default=True,
            help="Skip intervals shorter than this many seconds.",
        ),
        click.option(
            "--json-path",
            "json_path",
            type=click.Path(exists=True, dir_okay=False),
            default=None,
            help="Reduction JSON [default: discovered in DATA_DIR].",
        ),
        click.option(
            "--ref-seconds",
            type=float,
            default=None,
            help="Reference block: intervals within N s of the run start.",
        ),
        click.option(
            "--ref-labels",
            default=None,
            help="Reference block: comma-separated labels or globs.",
        ),
        click.option(
            "--late-seconds",
            type=float,
            default=None,
            help="Late block: intervals within N s of the run end.",
        ),
        click.option(
            "--late-labels",
            default=None,
            help="Late block: comma-separated labels or globs.",
        ),
        click.option(
            "--min-ref-snr",
            type=float,
            default=0.0,
            show_default=True,
            help="Drop reference Q bins below this many sigma above zero.",
        ),
        click.option(
            "--ref-loo/--no-ref-loo",
            default=True,
            show_default=True,
            help="Exact leave-one-out variance for reference members; "
            "disabling it biases their chi-squared low by about 10%.",
        ),
    )
    for option in reversed(options):
        func = option(func)
    return func


@tnr_group.command("assess")
@click.argument("data_dir", type=click.Path(exists=True, file_okay=False))
@click.option(
    "--out", default=None, help="Output directory [default: ../assessments/<label>]."
)
@click.option("--label", default="", help="Filename prefix and plot-title label.")
@click.option(
    "--template",
    type=click.Choice(["late", "pca"]),
    default="late",
    show_default=True,
    help="Template source for the amplitude projection.",
)
@click.option(
    "--template-smooth",
    type=int,
    default=9,
    show_default=True,
    help="Boxcar width for the template, in Q points.",
)
@click.option(
    "--qbands", type=int, default=4, show_default=True, help="Number of Q bands."
)
@click.option(
    "--qband-edges", default=None, help="Explicit band edges, comma-separated."
)
@click.option("--variogram-bins", type=int, default=6, show_default=True)
@click.option("--variogram-min-pairs", type=int, default=3, show_default=True)
@click.option(
    "--variogram-lag-scale",
    type=click.Choice(["log", "linear"]),
    default="log",
    show_default=True,
)
@click.option(
    "--delta2-clip",
    type=click.Choice(["mean", "element"]),
    default="mean",
    show_default=True,
)
@click.option(
    "--heatmap-coadd-seconds",
    type=float,
    default=None,
    help="Coadd the residual heatmap into blocks of this many seconds.",
)
@click.option("--no-plots", is_flag=True, help="Write tables only; skip the PNGs.")
@click.option(
    "--result-out", default=None, help="Write an ndip-tool-result/1 manifest here."
)
@click.option("--json", "as_json", is_flag=True, help="Emit the assessment as JSON.")
@_reference_options
def tnr_assess_command(**kwargs: object) -> None:
    """Run every metric on DATA_DIR, in the documented reading order.

    Writes the plots and tables under their original names, plus an
    assessment.json carrying the machine-readable verdict.
    """
    from nr_workbench.commands.tnr_cmd import run_assess

    run_assess(**kwargs)  # type: ignore[arg-type]


@tnr_group.command("manifest")
@click.argument("data_dir", type=click.Path(exists=True, file_okay=False))
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
@_reference_options
def tnr_manifest_command(**kwargs: object) -> None:
    """Show DATA_DIR's interval structure without computing any metric."""
    from nr_workbench.commands.tnr_cmd import run_manifest

    run_manifest(**kwargs)  # type: ignore[arg-type]


def _add_metric_command(name: str, help_text: str) -> None:
    """Register a per-metric subcommand sharing the assess options."""

    @tnr_group.command(name, help=help_text)
    @click.argument("data_dir", type=click.Path(exists=True, file_okay=False))
    @click.option("--out", default=None, help="Output directory.")
    @click.option("--label", default="", help="Filename prefix.")
    @click.option("--no-plots", is_flag=True, help="Write tables only.")
    @click.option("--json", "as_json", is_flag=True, help="Emit JSON.")
    @_reference_options
    def _command(
        data_dir: str,
        out: str | None,
        label: str,
        no_plots: bool,
        as_json: bool,
        **shared: object,
    ) -> None:
        from nr_workbench.commands.tnr_cmd import run_metric

        run_metric(
            name,
            data_dir=data_dir,
            out=out,
            label=label,
            as_json=as_json,
            extra={"no_plots": no_plots},
            **shared,
        )

    _command.__name__ = f"tnr_{name}_command"


for _name, _help in (
    ("amplitude", "The change amplitude a(t) +- sigma: the primary metric."),
    ("variogram", "The lag variogram: is anything changing, and on what timescale?"),
    ("qbands", "Where in Q the change lives."),
    ("chi2", "Running chi-squared, the fractional change delta, and significance."),
    ("pca", "PCA of the R(Q,t) matrix."),
    ("kl", "Symmetric KL divergence per interval."),
):
    _add_metric_command(_name, _help)


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


@main.group("isaac")
def isaac_group() -> None:
    """Publish results as ISAAC AI-Ready Records."""


@isaac_group.command("export")
@click.argument("fit_id")
@click.option(
    "--out", "-o", default=None, help="Where to write [default: the fit's isaac/]."
)
@click.option(
    "--context",
    default=None,
    help="Notes for the record [default: the fit's NOTES.md].",
)
@click.option("--upload", is_flag=True, help="Push the records to the ISAAC Portal.")
@click.option(
    "--validate-only",
    is_flag=True,
    help="With --upload, ask the API to validate without persisting.",
)
@click.option("--yes", is_flag=True, help="Skip the upload confirmation.")
@click.option(
    "--force",
    is_flag=True,
    help="Replace an output directory nrw did not write.",
)
@click.option(
    "--no-llm",
    is_flag=True,
    help="Do not ask a language model to read sample.md for conditions.",
)
def isaac_export_command(**kwargs: object) -> None:
    """Export FIT_ID as ISAAC AI-Ready Records.

    Each state becomes one record, with its angle segments assembled into a
    single measurement rather than mistaken for separate ones. A co-refinement
    therefore yields one record per condition, all sharing a sample id so the
    portal reads them as one experiment.

    Needs `data-assembler` and `nr-isaac-format`, which own the schema
    mapping: pip install 'nr-workbench[isaac]'.
    """
    from nr_workbench.commands.isaac_cmd import run_export

    run_export(**kwargs)  # type: ignore[arg-type]


@main.group("agent")
def agent_group() -> None:
    """Run and constrain an unattended analysis harness."""


@agent_group.command("guard")
@click.option(
    "--command",
    default=None,
    help="Judge this command [default: read a PreToolUse payload from stdin].",
)
def agent_guard_command(command: str | None) -> None:
    """Refuse the commands an unattended agent must not run.

    Wired into a project's `.claude/settings.json` as a PreToolUse hook, so
    the refusal happens before the command executes rather than depending on
    the model agreeing. Exits 2 to block, 0 to allow.
    """
    from nr_workbench.agent.guard import run_guard

    run_guard(command)


@agent_group.command("run")
@click.argument("sample")
@click.option(
    "--dry-run",
    is_flag=True,
    help="Compose the prompt and print it; start no session.",
)
@click.option(
    "--turns",
    default=None,
    type=int,
    help="Cap on harness turns [default: 60].",
)
@click.option("--model", default=None, help="Model to run [default: the harness's].")
@click.option(
    "--timeout",
    default=None,
    type=int,
    help="Seconds before the session is killed [default: none].",
)
def agent_run_command(
    sample: str,
    dry_run: bool,
    turns: int | None,
    model: str | None,
    timeout: int | None,
) -> None:
    """Run one unattended analysis session over SAMPLE.

    The task comes from `## Fits to perform` in the sample's notes; with
    nothing written there this refuses to start, because deciding what is
    worth fitting is the one thing an unattended session must not do.
    """
    from nr_workbench.agent.session import DEFAULT_TURNS, SessionError, compose
    from nr_workbench.agent.session import run as run_session
    from nr_workbench.project.layout import ProjectLayout, ProjectNotFoundError

    try:
        root = ProjectLayout.discover().root
    except ProjectNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc

    try:
        if dry_run:
            session = compose(root, sample)
            click.echo(session.prompt)
            return
        session = run_session(
            root,
            sample,
            turns=DEFAULT_TURNS if turns is None else turns,
            model=model,
            timeout=timeout,
        )
    except SessionError as exc:
        raise click.ClickException(str(exc)) from exc

    where = session.transcript.relative_to(root) if session.transcript else "?"
    click.echo(f"Session finished (exit {session.returncode}); transcript {where}")
    escalations = root / "ESCALATIONS.md"
    if escalations.is_file():
        click.echo(
            f"  ! {escalations.name} exists -- read it before promoting anything"
        )


@agent_group.command("watch")
@click.argument("samples", nargs=-1)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Report what each measurement is waiting for; start nothing.",
)
@click.option(
    "--settle",
    default=None,
    type=int,
    help="Seconds a measurement's files must be unchanged [default: 300].",
)
@click.option(
    "--poll", default=None, type=int, help="Seconds between polls [default: 60]."
)
@click.option(
    "--max-sessions",
    default=None,
    type=int,
    help="Stop after this many sessions [default: run until interrupted].",
)
@click.option(
    "--session-timeout",
    default=None,
    type=int,
    help="Seconds before one session is killed [default: 7200].",
)
@click.option("--turns", default=None, type=int, help="Cap on turns per session.")
@click.option("--model", default=None, help="Model to run [default: the harness's].")
def agent_watch_command(
    samples: tuple[str, ...],
    dry_run: bool,
    settle: int | None,
    poll: int | None,
    max_sessions: int | None,
    session_timeout: int | None,
    turns: int | None,
    model: str | None,
) -> None:
    """Watch SAMPLES for settled measurements and analyse each once.

    A scheduler, not a second decision-maker: it decides *when* a session
    starts and over what, and `nrw agent run` does the rest. A measurement is
    started only when its files have stopped changing, its segments are
    coherent, and nothing has fitted it yet.

    With no SAMPLES, every sample in the project is watched.
    """
    from nr_workbench.agent import watch as watcher
    from nr_workbench.project.layout import ProjectLayout, ProjectNotFoundError

    try:
        layout = ProjectLayout.discover()
    except ProjectNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc

    chosen = list(samples) or layout.list_samples()
    if not chosen:
        raise click.ClickException("No samples yet; run `nrw sample new <ID>`.")

    settle_seconds = watcher.DEFAULT_SETTLE_SECONDS if settle is None else settle

    if dry_run:
        found = watcher.once(
            layout.root, chosen, watcher.WatchState(), settle_seconds=settle_seconds
        )
        for sample, verdicts in found.items():
            click.echo(sample)
            for verdict in verdicts:
                mark = "\u2192" if verdict.ready else " "
                click.echo(
                    f"  {mark} run {verdict.run} ({verdict.kind})  "
                    f"{verdict.state:<12} {verdict.reason}"
                )
        return

    try:
        started = watcher.watch(
            layout.root,
            chosen,
            settle_seconds=settle_seconds,
            poll_seconds=watcher.DEFAULT_POLL_SECONDS if poll is None else poll,
            max_sessions=max_sessions,
            session_timeout=(
                watcher.DEFAULT_SESSION_TIMEOUT
                if session_timeout is None
                else session_timeout
            ),
            turns=turns,
            model=model,
            on_event=click.echo,
        )
    except KeyboardInterrupt:
        # Ctrl-C is how this is meant to be stopped, so it reports rather than
        # printing a traceback over whatever the last session said.
        click.echo("\nStopped.")
        return
    click.echo(f"{started} session(s) run.")


@main.command("assess")
@click.argument("fit_id")
@click.option(
    "--no-write",
    "write",
    flag_value=False,
    default=True,
    help="Print only; do not append to the fit's NOTES.md.",
)
@click.option(
    "--no-llm",
    "use_llm",
    flag_value=False,
    default=True,
    help="Skip the language-model reading even if an endpoint is configured.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def assess_command(fit_id: str, write: bool, use_llm: bool, as_json: bool) -> None:
    """Check FIT_ID and record what the checks found.

    Always runs the arithmetic: parameters on their bounds, posteriors that
    span most of their prior, a best fit outside its own credible interval,
    BIC. With an LLM endpoint configured it also asks whether the values are
    physically sensible, given sample.md and the installed skills.

    The result is appended to the fit's NOTES.md, so it travels in a bundle
    and shows on the fit page.
    """
    from nr_workbench.commands.assess import run_assess

    run_assess(fit_id=fit_id, write=write, use_llm=use_llm, as_json=as_json)


@main.command("note")
@click.argument("target", required=False)
@click.option("-m", "--message", default=None, help="Text to append.")
@click.option(
    "--sample",
    default=None,
    help="Write a report about a SAMPLE instead of one fit.",
)
@click.option("--title", default=None, help="Title for a new sample report.")
@click.option("--edit", is_flag=True, help="Open it in $EDITOR afterwards.")
def note_command(
    target: str | None,
    message: str | None,
    sample: str | None,
    title: str | None,
    edit: bool,
) -> None:
    """Write or read the notes attached to a fit or a sample.

    With no -m, shows every note that mentions TARGET -- the fit's own
    NOTES.md and any sample report that cites it.

    \b
    nrw note 0103d9c7 -m "oxide sits on its floor; conditional, not measured"
    nrw note 0103d9c7
    nrw note --sample expt11 --title "why the tNR is fitted alone" -m "..."
    """
    from nr_workbench.commands.note import run_note

    run_note(target=target, message=message, title=title, sample=sample, edit=edit)


@main.command("pack")
@click.argument("fit_id")
@click.option(
    "--out",
    "-o",
    default=None,
    help="Where to write it [default: ./<fit_id>.zip].",
)
@click.option(
    "--dir",
    "as_dir",
    is_flag=True,
    help="Leave a directory instead of archiving it.",
)
@click.option(
    "--with-chain",
    is_flag=True,
    help="Include the MCMC chain, usually the largest file in the result.",
)
@click.option(
    "--force",
    is_flag=True,
    help="Pack even though an input no longer matches its recorded hash.",
)
def pack_command(
    fit_id: str, out: str | None, as_dir: bool, with_chain: bool, force: bool
) -> None:
    """Package FIT_ID so a collaborator can reproduce it with only refl1d.

    A result directory records the hashes of its data, not the data. A bundle
    carries the measurements themselves, at the paths the frozen script
    expects, plus a `verify.py` that recomputes chi-squared and checks it
    against the recorded value.
    """
    from nr_workbench.commands.pack import run_pack

    run_pack(
        fit_id=fit_id,
        out=out,
        archive=not as_dir,
        with_chain=with_chain,
        force=force,
    )


@main.command("diff")
@click.argument("fit_a")
@click.argument("fit_b")
@click.option(
    "--script", is_flag=True, help="Also print a unified diff of the scripts."
)
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def diff_command(**kwargs: object) -> None:
    """Compare two fits: what changed, and whether it explains the result.

    The verdict separates "the fit got better" from "the data changed
    underneath me" -- indistinguishable in a chi-squared column.
    """
    from nr_workbench.commands.provenance_cmd import run_diff

    run_diff(**kwargs)  # type: ignore[arg-type]


@main.command("check")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def check_command(as_json: bool) -> None:
    """Verify project integrity: stale results, missing inputs, broken pointers."""
    from nr_workbench.commands.provenance_cmd import run_check

    run_check(as_json=as_json)


@main.group("data")
def data_group() -> None:
    """Check reduced data before modelling it."""


@data_group.command("reconcile")
@click.argument("sample")
@click.option("--root", default=None, help="Project root [default: discovered].")
@click.option("--result-out", default=None, help="Write the full result here.")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def data_reconcile_command(**kwargs: object) -> None:
    """Compare what SAMPLE's files record against what sample.md claims.

    Every reduced file carries the run title, the direct beam it was divided
    by and the angle. Nothing has ever compared those to the measurement
    table. Exits non-zero when they disagree.
    """
    from nr_workbench.commands.data import run_reconcile

    run_reconcile(**kwargs)  # type: ignore[arg-type]


@data_group.command("overlap")
@click.argument("sample")
@click.option("--run", type=int, help="Restrict to one run number.")
@click.option("--root", type=click.Path(file_okay=False), help="Project root.")
@click.option("--result-out", type=click.Path(), help="Write JSON here.")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def data_overlap_command(**kwargs: object) -> None:
    """Check that a run's angle segments agree where they overlap in Q."""
    from nr_workbench.commands.data import run_overlap

    run_overlap(**kwargs)  # type: ignore[arg-type]


@data_group.command("features")
@click.argument("path", type=click.Path(exists=True, dir_okay=False))
@click.option("--result-out", type=click.Path(), help="Write JSON here.")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def data_features_command(**kwargs: object) -> None:
    """Report critical edges, Kiessig fringes and thickness for one curve."""
    from nr_workbench.commands.data import run_features

    run_features(**kwargs)  # type: ignore[arg-type]


@data_group.command("check")
@click.argument("sample", required=False)
@click.option("--root", type=click.Path(file_okay=False), help="Project root.")
@click.option("--result-out", type=click.Path(), help="Write JSON here.")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def data_check_command(**kwargs: object) -> None:
    """Validate every reduced steady-state file for a sample."""
    from nr_workbench.commands.data import run_check

    run_check(**kwargs)  # type: ignore[arg-type]


@main.command("import")
@click.argument("source", type=click.Path(exists=True, file_okay=False))
@click.option("--root", type=click.Path(file_okay=False), help="Project root.")
@click.option(
    "--sample",
    default="Sample1",
    show_default=True,
    help="Sample for files that name none of their own.",
)
@click.option("--write", is_flag=True, help="Create the links; off by default.")
@click.option("--verbose", is_flag=True, help="List every planned file.")
@click.option("--result-out", type=click.Path(), help="Write the plan here as JSON.")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def import_command(**kwargs: object) -> None:
    """Import an existing beamtime directory, symlinking its data."""
    from nr_workbench.commands.import_cmd import run_import

    run_import(**kwargs)  # type: ignore[arg-type]


@main.command("serve")
@click.option("--root", type=click.Path(file_okay=False), help="Project root.")
@click.option("--host", default="127.0.0.1", show_default=True, help="Interface.")
@click.option("--port", default=8765, show_default=True, type=int, help="Port.")
@click.option("--debug", is_flag=True, help="Enable the Flask reloader.")
def serve_command(**kwargs: object) -> None:
    """Browse the project: every measurement, fit, and SLD curve on one page."""
    from nr_workbench.commands.serve import run_serve

    run_serve(**kwargs)  # type: ignore[arg-type]


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
