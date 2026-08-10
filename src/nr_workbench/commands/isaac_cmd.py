"""``nrw isaac export`` -- publish a fit as ISAAC AI-Ready Records.

The pipeline is the canonical one, driven rather than reimplemented::

    nrw            stage the fit as an ingest contract
    data-assembler ingest-workflow  -> neutral typed records
    nr-isaac-format convert-ingest  -> validated ISAAC records
    nr-isaac-format push            -> the ISAAC Portal   (--upload)

Both tools are invoked as subprocesses, not imported. They pull in httpx,
pyarrow and a schema stack that nothing else here needs, and the CLIs are their
documented contract while the Python API is not. It also means a missing tool
is a clear message rather than an ImportError from three levels down.

Uploading is opt-in and never implied by exporting. A record pushed to a shared
portal is not straightforwardly retractable, so it is a separate flag with a
separate confirmation, and ``--validate-only`` exists to ask the API whether a
record would be accepted without persisting it.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import click

from nr_workbench.isaac import Staged, reset, stage
from nr_workbench.project.layout import ProjectLayout, ProjectNotFoundError
from nr_workbench.provenance.index import FitIndex
from nr_workbench.provenance.lookup import FitNotFoundError, resolve_fit

#: How long each subprocess may take. Assembly reads every data file; the
#: convert step validates against a schema. Neither should approach this.
TIMEOUT = 300


class ToolMissingError(Exception):
    """A required external tool is not installed."""


def run_export(
    *,
    fit_id: str,
    out: str | None = None,
    upload: bool = False,
    validate_only: bool = False,
    context: str | None = None,
    yes: bool = False,
    force: bool = False,
) -> None:
    """Export one fit to ISAAC records, and optionally upload them.

    Args:
        fit_id: The fit, or a unique prefix.
        out: Where to write the records. Defaults to the fit's own directory.
        upload: Push the records to the ISAAC Portal.
        validate_only: With ``upload``, ask the API to validate without
            persisting.
        context: Free-text notes carried into the record.
        yes: Skip the upload confirmation.
        force: Replace an output directory nrw did not write.

    Raises:
        click.ClickException: If the project, fit, or a required tool is
            missing, or a pipeline step fails.
    """
    try:
        layout = ProjectLayout.discover()
    except ProjectNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc

    index = FitIndex(layout.index_file)
    try:
        entry, fit_dir = resolve_fit(layout, index, fit_id)
    except FitNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc

    resolved = str(entry["fit_id"])
    destination = Path(out).resolve() if out else fit_dir / "isaac"
    ingest = destination / "ingest"
    records = destination / "records"

    notes = context or _notes_for(layout, fit_dir, entry)

    try:
        staged = stage(
            fit_dir,
            layout.root,
            ingest,
            sample_description=_sample_description(layout, entry),
            force=force,
        )
        # Same reason as the ingest dir: a record left over from a previous
        # export is indistinguishable from one this run produced.
        reset(records, force=force)
    except FileNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc
    except FileExistsError as exc:
        raise click.ClickException(str(exc)) from exc

    _report_staged(staged, resolved)

    try:
        _assemble(ingest)
        _convert(ingest, records, notes)
        _validate(records)
    except ToolMissingError as exc:
        raise click.ClickException(str(exc)) from exc

    written = sorted(records.glob("*.json"))
    click.echo()
    click.echo(f"  records   {len(written)} written to {_relative(records, layout)}")
    for path in written:
        click.echo(f"            {path.name}")
    if len(staged.states) > 1:
        click.echo(
            f"  linked    {len(staged.states)} records share one sample id, so "
            "the portal reads them as conditions of one experiment"
        )

    if upload:
        _upload(records, validate_only=validate_only, yes=yes)
    else:
        click.echo()
        click.echo("  Not uploaded. Add --upload to push these to the ISAAC Portal.")


def _report_staged(staged: Staged, fit_id: str) -> None:
    """Say what was assembled, in the terms that matter."""
    click.echo(f"  fit       {fit_id}")
    for state in staged.states:
        segments = len(state.files)
        click.echo(
            f"  state     {state.name}: {segments} "
            f"{'angle segment' if segments == 1 else 'angle segments'} "
            "-> one measurement"
        )
    if staged.chisq is not None:
        click.echo(f"  chisq     {staged.chisq:.6g}")
    for problem in staged.problems:
        click.secho(f"  ! {problem}", fg="yellow")


def _sample_description(layout: ProjectLayout, entry: dict[str, Any]) -> str | None:
    """The sample's own prose, for the sample record."""
    sample = entry.get("sample")
    if not sample:
        return None
    path = layout.sample(str(sample)) / "sample.md"
    if not path.is_file():
        return None
    from nr_workbench.web.prose import strip_comments

    return strip_comments(path.read_text(encoding="utf-8")).strip() or None


def _notes_for(
    layout: ProjectLayout, fit_dir: Path, entry: dict[str, Any]
) -> str | None:
    """Use the analysis notes as the record's measurement notes.

    The record is read downstream by people who will never see this project,
    so the reasoning is worth more there than anywhere else it is stored.
    """
    from nr_workbench.notes import fit_note

    sample = entry.get("sample")
    note = fit_note(
        layout.root, fit_dir, str(entry.get("fit_id")), str(sample) if sample else None
    )
    if note is None or note.blank:
        return None
    return note.text


def _find(names: tuple[str, ...], install: str) -> list[str]:
    """Locate a CLI, preferring one beside the running interpreter."""
    for name in names:
        local = Path(sys.executable).parent / name
        if local.is_file():
            return [str(local)]
        found = shutil.which(name)
        if found:
            return [found]
    module = names[0].replace("-", "_")
    try:
        importable = (
            subprocess.run(
                [sys.executable, "-c", f"import {module}"],
                capture_output=True,
                check=False,
            ).returncode
            == 0
        )
    except OSError:
        # No usable interpreter to ask. Not being able to check is the same
        # answer as "not installed" here, and is not worth a traceback.
        importable = False
    if importable:
        return [sys.executable, "-m", module]
    raise ToolMissingError(
        f"{names[0]} is not installed.\n"
        f"  pip install '{install}'\n"
        "The ISAAC schema mapping lives in those tools rather than here, so "
        "the export cannot run without them."
    )


def _run(cmd: list[str], step: str) -> subprocess.CompletedProcess[str]:
    """Run one pipeline step, turning failure into a readable error."""
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=TIMEOUT, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise click.ClickException(f"{step} timed out after {TIMEOUT}s") from exc
    if result.returncode != 0:
        detail = (result.stderr.strip() or result.stdout.strip())[:2000]
        raise click.ClickException(f"{step} failed:\n{detail}")
    return result


def _assemble(ingest: Path) -> None:
    """``data-assembler ingest-workflow`` over the staged contract."""
    cmd = _find(
        ("data-assembler",),
        "nr-workbench[isaac]",
    )
    _run(
        [*cmd, "ingest-workflow", str(ingest), "-o", str(ingest), "--json"],
        "data-assembler ingest-workflow",
    )


def _convert(ingest: Path, records: Path, notes: str | None) -> None:
    """``nr-isaac-format convert-ingest`` into one record per state."""
    cmd = _find(("nr-isaac-format",), "nr-workbench[isaac]")
    records.mkdir(parents=True, exist_ok=True)
    # A directory, always: with several states an explicit .json target is
    # rejected, and passing a directory for one state is accepted.
    args = [*cmd, "convert-ingest", str(ingest), "-o", str(records)]
    if notes:
        args += ["--context", notes]
    _run(args, "nr-isaac-format convert-ingest")


def _validate(records: Path) -> None:
    """Validate every record locally before anyone is offered an upload."""
    cmd = _find(("nr-isaac-format",), "nr-workbench[isaac]")
    for path in sorted(records.glob("*.json")):
        _run([*cmd, "validate", str(path)], f"validating {path.name}")


def _upload(records: Path, *, validate_only: bool, yes: bool) -> None:
    """Push to the ISAAC Portal, after saying what is about to leave."""
    written = sorted(records.glob("*.json"))
    if not written:
        raise click.ClickException("No records to upload.")

    click.echo()
    if validate_only:
        click.echo(f"  Asking the ISAAC API to validate {len(written)} record(s).")
    else:
        click.secho(
            f"  About to publish {len(written)} record(s) to the ISAAC Portal.",
            bold=True,
        )
        click.echo("  This shares the data and the fitted model outside this project.")
        if not yes and not click.confirm("  Continue?", default=False):
            click.echo("  Not uploaded.")
            return

    cmd = _find(("nr-isaac-format",), "nr-workbench[isaac]")
    args = [*cmd, "push", str(records)]
    if validate_only:
        args.append("--validate-only")
    result = _run(args, "nr-isaac-format push")
    click.echo(result.stdout.strip() or "  done")


def _relative(path: Path, layout: ProjectLayout) -> str:
    """Path relative to the project root where possible."""
    try:
        return str(path.relative_to(layout.root))
    except ValueError:
        return str(path)


def read_records(records: Path) -> list[dict[str, Any]]:
    """Read the exported records, for tests and for the web layer.

    Args:
        records: The directory records were written to.

    Returns:
        Each record as a mapping, in filename order.
    """
    found = []
    for path in sorted(Path(records).glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            found.append(payload)
    return found


__all__ = ["read_records", "run_export"]
