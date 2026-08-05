"""``nrw fit run`` -- run a fit and record everything about it.

M1 deliberately supports **hand-written scripts**. That is the whole adoption
story: an existing `experiments-2025` model can be run through this today, with
no migration, no schema to learn, and it comes out the other side with a
complete provenance record. The generated-spec path arrives later and produces
the same record shape.
"""

from __future__ import annotations

import shlex
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import click

from nr_workbench.project.config import ProjectConfigError, load_config
from nr_workbench.project.layout import ProjectLayout, ProjectNotFoundError
from nr_workbench.provenance.env import capture as capture_env
from nr_workbench.provenance.hashing import HashCache, digest_files, inputs_digest
from nr_workbench.provenance.index import FitIndex
from nr_workbench.provenance.record import (
    FitIdentity,
    FitRecord,
    create_unique,
    env_digest,
    format_timestamp,
    make_fit_id,
    settings_digest,
    utc_now,
)
from nr_workbench.provenance.stamp import stamp_directory

#: Data suffixes worth hashing when we have to infer a script's inputs.
_DATA_SUFFIXES = (".txt", ".dat", ".ort", ".csv", ".json")


@dataclass
class ResolvedTarget:
    """Where a fit's output belongs.

    Attributes:
        sample: Sample the fit belongs to, or None for a project-level run.
        results_dir: Directory that will hold the new fit directory.
    """

    sample: str | None
    results_dir: Path


def resolve_target(
    layout: ProjectLayout, script: Path, sample: str | None
) -> ResolvedTarget:
    """Decide which sample a fit belongs to.

    Args:
        layout: The project layout.
        script: The script being run.
        sample: Explicit sample, or None to infer from the script's location.

    Returns:
        The resolved target.

    Raises:
        click.ClickException: If an explicit sample does not exist.
    """
    if sample is not None:
        sample_dir = layout.sample(sample)
        if not sample_dir.is_dir():
            raise click.ClickException(
                f"No sample '{sample}'. Create it with `nrw sample new {sample}`."
            )
        return ResolvedTarget(sample=sample, results_dir=sample_dir / "results")

    # Infer from the script's path: samples/<id>/... anywhere above it.
    try:
        relative = script.resolve().relative_to(layout.samples_dir)
        inferred = relative.parts[0]
        if (layout.samples_dir / inferred).is_dir():
            return ResolvedTarget(
                sample=inferred, results_dir=layout.sample(inferred) / "results"
            )
    except ValueError:
        pass

    return ResolvedTarget(sample=None, results_dir=layout.root / "results")


def role_for(path: Path, root: Path) -> str:
    """Name an input file's role in a fit.

    The role vocabulary is what makes a reverse lookup readable: seeing
    ``data:tnr:r223921_t000240`` beats seeing a bare path.

    Args:
        path: The input file.
        root: Project root.

    Returns:
        A role string such as ``data:steady:REFL_226642_combined_data_auto``.
    """
    stem = path.stem
    try:
        parts = path.resolve().relative_to(Path(root).resolve()).parts
    except ValueError:
        return f"data:{stem}"

    if "tnr" in parts:
        return f"data:tnr:{stem}"
    if "steady" in parts:
        return f"data:steady:{stem}"
    if path.suffix == ".json":
        return f"metadata:{stem}"
    return f"data:{stem}"


def observed_inputs(
    script: Path, opened: list[Path], root: Path
) -> list[tuple[str, Path]]:
    """Build the role-tagged input list from files the script actually opened.

    Args:
        script: The script that ran.
        opened: Paths observed during execution.
        root: Project root.

    Returns:
        Role-tagged paths, the script itself first.
    """
    entries: list[tuple[str, Path]] = [("script", script.resolve())]
    for path in opened:
        entries.append((role_for(path, root), path))
    return entries


def guess_inputs(script: Path, root: Path) -> list[tuple[str, Path]]:
    """Guess a script's inputs without executing it, for ``--dry-run``.

    Reads string literals from the source and, for any that look like a data
    filename, searches the project for a unique match. Weaker than observing a
    real run -- a filename built at runtime from a run number is invisible here
    -- which is exactly why the real path executes the script instead.

    Args:
        script: The script to scan.
        root: Project root.

    Returns:
        Role-tagged paths, the script itself first.
    """
    import ast

    script = script.resolve()
    entries: list[tuple[str, Path]] = [("script", script)]
    seen: set[Path] = {script}

    try:
        tree = ast.parse(script.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return entries

    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        literal = node.value
        if not literal or not literal.endswith(_DATA_SUFFIXES):
            continue
        if literal.startswith(("http://", "https://")):
            continue

        found = _locate(literal, script, Path(root))
        if found is not None and found not in seen:
            seen.add(found)
            entries.append((role_for(found, root), found))

    return entries


def _locate(literal: str, script: Path, root: Path) -> Path | None:
    """Resolve a filename literal to a real file, searching the project."""
    for candidate in (Path(literal), script.parent / literal, root / literal):
        resolved = candidate.expanduser()
        if resolved.is_file():
            return resolved.resolve()

    # Scripts commonly build paths as dirname(__file__)/../data/..., so the
    # literal is only a basename. A unique match in the project is safe to use;
    # an ambiguous one is not worth guessing at.
    name = Path(literal).name
    matches = [p for p in root.rglob(name) if p.is_file()]
    return matches[0].resolve() if len(matches) == 1 else None


def run_fit_command(
    *,
    script: str,
    sample: str | None = None,
    method: str = "amoeba",
    steps: int | None = None,
    samples: int | None = None,
    burn: int | None = None,
    pop: int | None = None,
    seed: int | None = None,
    note: str | None = None,
    model_name: str | None = None,
    force: bool = False,
    dry_run: bool = False,
) -> None:
    """Run a fit script and write an immutable record of the run.

    Args:
        script: Path to the refl1d script.
        sample: Sample to record the fit under. Inferred from the path if omitted.
        method: Bumps fitter name.
        steps: Maximum optimizer steps.
        samples: DREAM sample count.
        burn: DREAM burn-in.
        pop: Population size.
        seed: Random seed.
        note: Free-text note stored in the record.
        model_name: Model name. Defaults to the script stem.
        force: Run even if an identical run already exists.
        dry_run: Report what would run, write nothing.

    Raises:
        click.ClickException: If there is no project, the script is missing, or
            the fit fails.
    """
    script_path = Path(script).resolve()
    if not script_path.is_file():
        raise click.ClickException(f"Script not found: {script}")

    try:
        layout = ProjectLayout.discover(script_path.parent)
        load_config(layout.root)
    except (ProjectNotFoundError, ProjectConfigError) as exc:
        raise click.ClickException(str(exc)) from exc

    target = resolve_target(layout, script_path, sample)
    index = FitIndex(layout.index_file)

    settings = {
        "method": method,
        "steps": steps,
        "samples": samples,
        "burn": burn,
        "pop": pop,
        "seed": seed,
    }

    # Load the script once, under observation, so the recorded inputs are the
    # files it genuinely read rather than a guess from its source. --dry-run
    # does not execute anything, so it falls back to the weaker scan.
    loaded = None
    if dry_run:
        candidates = guess_inputs(script_path, layout.root)
    else:
        from nr_workbench.fitting.runner import FitError, load_problem

        try:
            loaded = load_problem(script_path, root=layout.root)
        except FitError as exc:
            raise click.ClickException(str(exc)) from exc
        candidates = observed_inputs(script_path, loaded.opened, layout.root)

    cache = HashCache(layout.cache_dir / "hashes.json")
    try:
        inputs = digest_files(candidates, root=layout.root, cache=cache)
    except FileNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc
    cache.save()

    environment = capture_env(layout.root, include_requirements=not dry_run)
    identity = FitIdentity(
        script_sha256=next(d.sha256 for d in inputs if d.role == "script"),
        inputs_digest=inputs_digest(inputs),
        settings_digest=settings_digest(settings),
        env_digest=env_digest(environment),
    )

    duplicates = index.find_by_run_key(identity.run_key)
    if duplicates and not force and not dry_run:
        previous = duplicates[0]
        raise click.ClickException(
            f"An identical run already exists: {previous['fit_id']} "
            f"(status {previous.get('status')}, chisq {previous.get('chisq')}).\n"
            "Nothing changed: script, inputs, settings and environment all match.\n"
            "  --force   run anyway, recorded as a replicate\n"
            f"  nrw whence {previous['fit_id']}   show what it produced"
        )

    started = utc_now()
    fit_id = make_fit_id(started, identity.run_key)

    data_inputs = [d for d in inputs if d.role != "script"]
    if dry_run:
        _report_dry_run(
            fit_id, target.results_dir / fit_id, settings, inputs, environment
        )
        return

    if not data_inputs:
        click.echo(
            "  ! no data files could be inferred from the script; the record "
            "will list only the script itself",
            err=True,
        )

    record = FitRecord(
        fit_id=fit_id,
        sample=target.sample,
        model=model_name or script_path.stem,
        script_origin="script",
        identity=identity,
        inputs=inputs,
        settings=settings,
        environment=environment,
        started_at=format_timestamp(started),
        command=_command_line(),
        note=note,
    )
    if duplicates:
        record.settings["replicate_of"] = duplicates[0]["fit_id"]

    try:
        directory, fit_id = create_unique(target.results_dir, fit_id)
    except FileExistsError as exc:
        raise click.ClickException(str(exc)) from exc
    fit_dir = directory.path
    record.fit_id = fit_id

    directory.freeze_script(script_path)
    directory.write_inputs(inputs)
    directory.write_environment(environment)
    directory.write_notes_stub()

    click.echo(f"Running {method} fit -> {fit_dir.relative_to(layout.root)}")

    from nr_workbench.fitting.runner import FitError, run_fit

    assert loaded is not None  # dry_run returned above
    try:
        outcome = run_fit(
            loaded.problem,
            fit_dir / "fit",
            method=method,
            steps=steps,
            samples=samples,
            burn=burn,
            pop=pop,
            seed=seed,
        )
    except FitError as exc:
        # A failed fit is still recorded. Knowing that a model was tried and
        # did not converge is worth as much as knowing one did.
        record.status = "failed"
        record.error = str(exc)
        record.finished_at = format_timestamp(utc_now())
        directory.write_manifest(record)
        index.append(record.index_entry())
        raise click.ClickException(f"{exc}\nRecorded the failure as {fit_id}.") from exc

    record.finished_at = format_timestamp(utc_now())
    record.chisq = outcome.chisq
    record.n_free = outcome.n_free
    record.n_points = outcome.n_points
    record.artifacts = outcome.artifacts

    stamped = stamp_directory(fit_dir / "figures", fit_id)
    if stamped:
        record.artifacts["figures"] = "figures"

    directory.write_manifest(record)
    index.append(record.index_entry())

    _report_success(record, fit_dir, layout.root, outcome)


def _command_line() -> str:
    """Return the command line that produced this record."""
    return " ".join(shlex.quote(part) for part in sys.argv)


def _report_dry_run(
    fit_id: str,
    fit_dir: Path,
    settings: dict[str, Any],
    inputs: list[Any],
    environment: Any,
) -> None:
    """Print what a fit would do without running it."""
    click.echo(f"Would create {fit_dir}")
    click.echo(f"  fit_id    {fit_id}")
    click.echo(f"  method    {settings['method']}")
    click.echo(f"  inputs    {len(inputs)} file(s)")
    for digest in inputs:
        click.echo(f"    {digest.role:<24} {digest.path}  {digest.sha256[:12]}")
    if environment.git.available:
        state = "dirty" if environment.git.dirty else "clean"
        click.echo(f"  git       {(environment.git.commit or '?')[:12]} ({state})")


def _report_success(record: FitRecord, fit_dir: Path, root: Path, outcome: Any) -> None:
    """Print a summary of a completed fit."""
    click.echo()
    click.echo(f"  fit_id   {record.fit_id}")
    if record.chisq is not None:
        click.echo(f"  chisq    {record.chisq:.4g}")
    if record.n_free is not None:
        click.echo(f"  free     {record.n_free} parameter(s)")
    click.echo(f"  output   {fit_dir.relative_to(root)}")

    if not outcome.export_ok:
        click.echo(f"  ! bumps export incomplete: {outcome.export_error}", err=True)
        click.echo(
            "    Parameters were fitted; uncertainty output may be missing.", err=True
        )

    click.echo()
    click.echo(f"  nrw whence {record.fit_id}      show the full provenance")
    click.echo(f"  nrw promote {record.fit_id} --as final --reason '...'")
