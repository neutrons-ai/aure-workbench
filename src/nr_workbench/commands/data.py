"""``nrw data`` -- check the data before modelling it.

Every command here answers a question that is cheap to ask now and expensive
to discover after a fit has already absorbed the answer into a layer thickness.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click

from nr_workbench.project.layout import ProjectLayout, ProjectNotFoundError
from nr_workbench.project.scan import scan_sample


def _layout(root: str | None) -> ProjectLayout:
    """Resolve the project layout or fail with a usable message."""
    try:
        return (
            ProjectLayout(root=Path(root).resolve())
            if root
            else ProjectLayout.discover()
        )
    except ProjectNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc


def _emit(payload: dict[str, Any], result_out: str | None) -> None:
    """Write the machine-readable result, when one was asked for."""
    if result_out:
        Path(result_out).write_text(
            json.dumps(payload, indent=2, default=str), encoding="utf-8"
        )


def _segments_for(
    layout: ProjectLayout, sample: str, run: int | None
) -> dict[int, Any]:
    """Return ``{run: SteadyMeasurement}`` for a sample, optionally one run."""
    found = scan_sample(layout.root, sample)
    if run is None:
        return dict(found.steady)
    if run not in found.steady:
        known = ", ".join(str(r) for r in sorted(found.steady)) or "none"
        raise click.ClickException(f"No steady run {run} in {sample}. Found: {known}.")
    return {run: found.steady[run]}


def _beam_summary(beams: dict[int, int | None]) -> str:
    """Render the segment-to-direct-beam mapping on one line."""
    return "  ".join(
        f"{run}<-{beam if beam is not None else '?'}"
        for run, beam in sorted(beams.items())
    )


def run_reconcile(
    *,
    sample: str,
    root: str | None = None,
    result_out: str | None = None,
    as_json: bool = False,
) -> None:
    """Compare what the reduced files record against what sample.md claims.

    Args:
        sample: Sample identifier.
        root: Project root; discovered if omitted.
        result_out: Write the full result here as JSON.
        as_json: Emit machine-readable JSON.

    Raises:
        click.ClickException: If there is no project or no such sample.
        SystemExit: With code 1 when anything disagrees.
    """
    from nr_workbench.instrument.header import read_header
    from nr_workbench.project.scan import scan_sample
    from nr_workbench.reconcile import reconcile

    layout = _layout(root)
    directory = layout.sample(sample)
    if not directory.is_dir():
        raise click.ClickException(f"No sample {sample!r} in {layout.root}.")

    scan = scan_sample(layout.root, sample)
    headers = []
    for measurement in scan.steady.values():
        for path in [*measurement.partials.values(), measurement.combined]:
            if not path:
                continue
            try:
                headers.append(read_header(layout.root / path))
            except Exception as exc:  # noqa: BLE001 - one bad file is a finding
                click.secho(f"  ! could not read {path}: {exc}", fg="yellow", err=True)

    notes = directory / "sample.md"
    markdown = notes.read_text(encoding="utf-8") if notes.is_file() else ""
    if not markdown.strip():
        click.secho(
            "  ! sample.md is empty, so there is nothing to reconcile against.",
            fg="yellow",
            err=True,
        )

    config = _config(layout)
    result = reconcile(
        sample,
        headers,
        markdown,
        series_runs={s.run for s in scan.series if s.run},
        standard_thetas=list(getattr(config, "standard_thetas", []) or []),
    )

    payload = result.as_dict()
    if result_out:
        Path(result_out).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    if as_json:
        click.echo(json.dumps(payload, indent=2))
    else:
        _print_reconcile(result)
    if result.findings:
        raise SystemExit(1)


def _print_reconcile(result: Any) -> None:
    """Print the reconciliation for a human."""
    click.echo(
        f"  {len(result.runs)} run(s) on disk, "
        f"{len(result.documented)} documented in sample.md"
    )
    if not result.findings:
        click.secho("  The files and the notes agree.", fg="green")
        return
    click.echo()
    colour = {"blocker": "red", "warn": "yellow", "info": None}
    for finding in result.findings:
        click.secho(
            f"  {finding.severity:<7} {finding.message}", fg=colour[finding.severity]
        )
        if finding.from_file:
            click.echo(f"          file:  {finding.from_file}")
        if finding.from_notes:
            click.echo(f"          notes: {finding.from_notes}")
    click.echo()
    click.echo(
        "  These are disagreements, not verdicts: sometimes the notes are "
        "wrong and sometimes the filing is."
    )


def _config(layout: Any) -> Any:
    """The project config, or None."""
    from nr_workbench.project.config import ProjectConfigError, load_config

    try:
        return load_config(layout.root)
    except (ProjectConfigError, OSError):
        return None


def run_overlap(
    *,
    sample: str,
    run: int | None = None,
    root: str | None = None,
    result_out: str | None = None,
    as_json: bool = False,
) -> None:
    """Check that a measurement's angle segments agree where they overlap.

    Args:
        sample: Sample identifier.
        run: Restrict to one run.
        root: Project root; discovered if omitted.
        result_out: Write the full result here as JSON.
        as_json: Print JSON instead of a table.

    Raises:
        click.ClickException: If the sample is unknown or nothing overlaps.
        SystemExit: Non-zero when any pair is inconsistent, so this can gate CI.
    """
    from nr_workbench.data.overlap import SIGNIFICANCE, compare_all, load_segment
    from nr_workbench.instrument.template import direct_beams_for

    layout = _layout(root)
    try:
        measurements = _segments_for(layout, sample, run)
    except FileNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc

    report: dict[str, Any] = {"schema": "nrw-overlap/1", "sample": sample, "runs": []}
    inconsistent = 0

    for run_number in sorted(measurements):
        measurement = measurements[run_number]
        if len(measurement.partials) < 2:
            continue
        segments = [
            load_segment(layout.root / measurement.partials[index], f"#{index}")
            for index in sorted(measurement.partials)
        ]
        comparisons = compare_all(segments)
        # Which direct beam normalised each segment. Not part of the
        # measurement -- it is where to look when one comes out on a
        # different scale.
        first_file = measurement.partials[min(measurement.partials)]
        beams = direct_beams_for((layout.root / first_file).parent, run_number)
        report["runs"].append(
            {
                "run": run_number,
                "overlaps": [c.as_dict() for c in comparisons],
                "direct_beams": {str(k): v for k, v in beams.items()},
            }
        )

        if not as_json:
            click.echo(f"\n  run {run_number}")
        for comparison in comparisons:
            if not comparison.consistent:
                inconsistent += 1
            if as_json:
                continue
            if comparison.ratio is None:
                click.echo(
                    f"    {comparison.lower} / {comparison.upper}"
                    f"   no usable overlap ({comparison.n_points} point(s))"
                )
                continue
            mark = " " if comparison.consistent else "!"
            click.echo(
                f"  {mark} {comparison.lower} / {comparison.upper}"
                f"   {comparison.scale_percent:+6.2f}%"
                f"  ({comparison.sigma:4.1f} sigma)"
                f"   chi2 {comparison.chi2:5.2f}"
                f"   n={comparison.n_points:<4d}"
                f" Q {comparison.q_min:.4f}-{comparison.q_max:.4f}"
            )

        if beams and not as_json and any(not c.consistent for c in comparisons):
            click.echo("      direct beams: " + _beam_summary(beams))

    report["inconsistent"] = inconsistent
    _emit(report, result_out)

    if as_json:
        click.echo(json.dumps(report, indent=2, default=str))
        return

    if not report["runs"]:
        click.echo(
            f"  No multi-segment runs in {sample}; nothing to compare.\n"
            "  Segment overlap only applies to runs reduced per angle."
        )
        return

    if inconsistent:
        click.echo(
            f"\n  {inconsistent} pair(s) disagree by more than {SIGNIFICANCE:g} sigma.\n"
            "  Where two angle settings overlap they measure the same sample, so a\n"
            "  significant ratio is a normalisation problem, not a feature. Either\n"
            "  re-reduce, or give that segment its own `probe.intensity` in the spec\n"
            "  so the fit accounts for it explicitly rather than absorbing it into a\n"
            "  layer.\n\n"
            "  The direct beams listed above are where to look first: segments\n"
            "  normalised against direct-beam runs measured far apart are the ones\n"
            "  that come out on different scales. Different angles legitimately use\n"
            "  different direct beams, so this is a pointer, not a diagnosis."
        )
        raise SystemExit(1)
    click.echo("\n  All overlapping segments agree.")


def run_features(
    *,
    path: str,
    result_out: str | None = None,
    as_json: bool = False,
) -> None:
    """Report critical edges, fringes and thickness estimates for a curve.

    Args:
        path: A reduced data file.
        result_out: Write the full result here as JSON.
        as_json: Print JSON instead of a summary.

    Raises:
        click.ClickException: If the file cannot be read or AuRE is missing.
    """
    import numpy as np

    from nr_workbench.aure_adapter import AureUnavailableError, extract_features

    data_path = Path(path)
    if not data_path.is_file():
        raise click.ClickException(f"No such file: {data_path}")

    try:
        data = np.loadtxt(data_path, ndmin=2)
    except (OSError, ValueError) as exc:
        raise click.ClickException(f"Cannot read {data_path}: {exc}") from exc
    if data.shape[1] < 3:
        raise click.ClickException(
            f"{data_path} has {data.shape[1]} column(s); need Q, R and dR."
        )

    try:
        features = extract_features(data[:, 0], data[:, 1], data[:, 2])
    except AureUnavailableError as exc:
        raise click.ClickException(str(exc)) from exc

    payload = {"schema": "nrw-features/1", "file": str(data_path), **features.as_dict()}
    _emit(payload, result_out)

    if as_json:
        click.echo(json.dumps(payload, indent=2, default=str))
        return

    click.echo(f"\n  {data_path.name}")
    low, high = features.q_range
    if low is not None:
        click.echo(f"    Q {low:.5f} - {high:.5f}   {features.n_points} points")

    if features.critical_edges:
        click.echo("\n    critical edges")
        for edge in features.critical_edges:
            click.echo(
                f"      Qc = {edge.get('Qc', float('nan')):.5f}"
                f"   implied SLD {edge.get('estimated_SLD', float('nan')):.3f}"
                f"   ({edge.get('confidence', '?')})"
            )

    click.echo(f"\n    fringes counted: {features.n_fringes}")
    for name, estimate in (
        ("total thickness", features.thickness),
        ("roughness", features.roughness),
        ("layer count", features.n_layers),
    ):
        if estimate.value is None:
            continue
        line = f"      {name:16s} {estimate.value:.4g}"
        if estimate.uncertainty is not None:
            line += f" +/- {estimate.uncertainty:.4g}"
        if estimate.confidence:
            line += f"   ({estimate.confidence})"
        if not estimate.usable:
            line += "   <- uncertainty exceeds the value; not a constraint"
        click.echo(line)


def run_check(
    *,
    sample: str | None = None,
    root: str | None = None,
    result_out: str | None = None,
    as_json: bool = False,
) -> None:
    """Validate every reduced file for a sample.

    Args:
        sample: Sample identifier; all samples if omitted.
        root: Project root; discovered if omitted.
        result_out: Write the full result here as JSON.
        as_json: Print JSON instead of a summary.

    Raises:
        click.ClickException: If AuRE is unavailable.
        SystemExit: Non-zero when any file fails validation.
    """
    import numpy as np

    from nr_workbench.aure_adapter import AureUnavailableError, validate
    from nr_workbench.instrument.header import HeaderError, read_header

    layout = _layout(root)
    samples = [sample] if sample else layout.list_samples()
    report: dict[str, Any] = {"schema": "nrw-datacheck/1", "files": []}
    failures = 0

    for sample_id in samples:
        try:
            found = scan_sample(layout.root, sample_id)
        except FileNotFoundError as exc:
            raise click.ClickException(str(exc)) from exc

        relatives = []
        for measurement in found.steady.values():
            if measurement.combined:
                relatives.append(measurement.combined)
            relatives.extend(
                measurement.partials[k] for k in sorted(measurement.partials)
            )

        for relative in relatives:
            entry: dict[str, Any] = {"sample": sample_id, "file": relative}
            try:
                data = np.loadtxt(layout.root / relative, ndmin=2)
                result = validate(data[:, 0], data[:, 1], data[:, 2])
                entry.update(result.as_dict())
            except AureUnavailableError as exc:
                raise click.ClickException(str(exc)) from exc
            except (OSError, ValueError, IndexError) as exc:
                entry.update({"valid": False, "issues": [f"unreadable: {exc}"]})

            # Whether dQ is FWHM or sigma is a 2.355x factor on every
            # resolution, it is stated in the column titles, and it is expected
            # to change. Report it per file so a mixed set is visible here
            # rather than in a roughness three fits later.
            try:
                header = read_header(layout.root / relative)
                entry["dq_convention"] = header.dq_convention
            except HeaderError as exc:
                entry["dq_convention"] = None
                entry.setdefault("issues", []).append(str(exc))
                entry["valid"] = False
            except OSError:
                entry["dq_convention"] = None

            if not entry.get("valid"):
                failures += 1
            report["files"].append(entry)

    report["failures"] = failures
    _emit(report, result_out)

    if as_json:
        click.echo(json.dumps(report, indent=2, default=str))
        return

    for entry in report["files"]:
        mark = " " if entry.get("valid") else "!"
        click.echo(f"  {mark} {entry['file']}")
        for issue in entry.get("issues", []):
            click.echo(f"      {issue}")

    if not report["files"]:
        click.echo("  No reduced steady-state files found.")
        return

    conventions = {e.get("dq_convention") for e in report["files"]}
    stated = {c for c in conventions if c}
    if len(stated) > 1:
        click.secho(
            f"\n  ! Mixed dQ conventions in one sample: {', '.join(sorted(stated))}.\n"
            "    probe.dq_is_fwhm is one boolean per spec, so these cannot be\n"
            "    co-refined in a single model -- the resolution of one set would be\n"
            "    wrong by 2.355x. Fit each convention separately.",
            fg="red",
        )
        failures += 1
    elif stated:
        click.echo(
            f"\n  dQ convention: {next(iter(stated)).upper()} (from the headers)"
        )
    if None in conventions:
        click.secho(
            "  ! Some files do not state whether dQ is FWHM or sigma; it will be\n"
            "    assumed FWHM. Confirm before quoting a roughness.",
            fg="yellow",
        )

    if failures:
        click.echo(
            f"\n  {failures} of {len(report['files'])} file(s) have issues.\n"
            "  R > 1 on a low-angle segment is usually a normalisation left to the\n"
            "  fit -- see `nrw data overlap`. Other issues are worth fixing in the\n"
            "  reduction before modelling."
        )
        raise SystemExit(1)
    click.echo(f"\n  {len(report['files'])} file(s) validated.")
