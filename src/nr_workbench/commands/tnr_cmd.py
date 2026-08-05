"""``nrw tnr`` -- assess temporal change in a time-resolved run.

The original tool exposed one command with roughly sixty flat options. Here the
default is ``nrw tnr assess``, which runs everything in the order
``docs/notes.md`` prescribes; per-metric subcommands exist for tuning one
analysis without recomputing the rest. Options shared by every metric -- the
reference and late-block selection -- live in one decorator rather than being
repeated.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click


def _load(data_dir: str, kwargs: dict[str, Any]):
    """Load a run from the shared options, failing with a clear message."""
    from nr_workbench.tnr.run import BlockOptions, load

    options = BlockOptions(
        ref_seconds=kwargs.get("ref_seconds"),
        ref_labels=kwargs.get("ref_labels"),
        late_seconds=kwargs.get("late_seconds"),
        late_labels=kwargs.get("late_labels"),
        min_ref_snr=kwargs.get("min_ref_snr", 0.0),
        loo=kwargs.get("ref_loo", True),
    )
    try:
        return load(
            Path(data_dir),
            options,
            json_path=Path(kwargs["json_path"]) if kwargs.get("json_path") else None,
            min_duration=kwargs.get("min_duration", 5.0),
        )
    except (FileNotFoundError, ValueError, KeyError) as exc:
        raise click.ClickException(str(exc)) from exc


def _echo_run(run) -> None:
    """Print the run header shared by every subcommand."""
    counts = run.type_counts
    click.echo(f"Loaded {run.n_intervals} intervals from {run.data_dir}")
    click.echo("  " + "  ".join(f"{k}: {v}" for k, v in sorted(counts.items())))
    click.echo(f"  time range: {run.times[0]:.1f} - {run.times[-1]:.1f} s")
    click.echo(
        f"  Q range:    {run.q.min():.4g} - {run.q.max():.4g}  (N_Q = {len(run.q)})"
    )
    click.echo(f"  reference:  {run.ref_desc}")
    click.echo(f"  late block: {run.late_desc}")
    click.echo(f"  median dR_ref/R_ref: {run.median_ref_rel_err:.4f}")


def run_assess(
    *,
    data_dir: str,
    out: str | None,
    label: str,
    template: str,
    template_smooth: int,
    qbands: int,
    qband_edges: str | None,
    variogram_bins: int,
    variogram_min_pairs: int,
    variogram_lag_scale: str,
    delta2_clip: str,
    heatmap_coadd_seconds: float | None,
    no_plots: bool,
    result_out: str | None,
    as_json: bool,
    **shared: Any,
) -> None:
    """Run every metric and write the outputs plus ``assessment.json``.

    Raises:
        click.ClickException: If the run cannot be loaded or assessed.
        SystemExit: With code 1 if every metric was skipped.
    """
    from nr_workbench.tnr.report import assess

    run = _load(data_dir, shared)
    if not as_json:
        _echo_run(run)

    out_dir = (
        Path(out)
        if out
        else Path(data_dir).parent / "assessments" / (label or "assessment")
    )
    edges = (
        [float(x) for x in qband_edges.split(",") if x.strip()] if qband_edges else None
    )

    result = assess(
        run,
        out_dir,
        label=label,
        template_source=template,
        template_smooth=template_smooth,
        n_qbands=qbands,
        qband_edges=edges,
        variogram_bins=variogram_bins,
        variogram_min_pairs=variogram_min_pairs,
        variogram_log_bins=variogram_lag_scale == "log",
        delta2_clip=delta2_clip,
        heatmap_coadd_seconds=heatmap_coadd_seconds,
        plots=not no_plots,
    )

    if as_json:
        click.echo(json.dumps(result.payload, indent=2))
    else:
        _echo_assessment(result, out_dir)

    if result_out:
        _write_result_manifest(result_out, result, out_dir)

    if len(result.skipped) >= 5:
        raise SystemExit(1)


def _echo_assessment(result, out_dir: Path) -> None:
    """Print the assessment in the order the reading-order prescribes."""
    payload = result.payload

    variogram = payload.get("variogram")
    if variogram:
        state = "flat -- nothing is changing" if variogram["flat"] else "rising"
        knee = (
            f", knee near {variogram['knee_s']:.0f} s"
            if variogram.get("knee_s")
            else ""
        )
        click.echo(f"\n  1. variogram: {state}{knee}")

    amplitude = payload.get("amplitude")
    if amplitude:
        click.echo(
            f"  2. amplitude: a in [{amplitude['a_range'][0]}, {amplitude['a_range'][1]}], "
            f"max |a/sigma| = {amplitude['max_significance']}"
        )
        click.echo(
            f"     chi2_res median {amplitude['chi2_res_median']} "
            + (
                "(one template suffices)"
                if amplitude["single_template_sufficient"]
                else "(ABOVE 1 -- read the PCA before trusting a(t))"
            )
        )
        click.echo(
            f"     trajectory: {amplitude['trajectory']}"
            + (", plateaued" if amplitude["plateau"] else "")
        )

    template = payload.get("template")
    if template and template.get("implied_change"):
        thickness = template.get("implied_thickness_A")
        extra = f", order {thickness:.0f} A" if thickness else ""
        click.echo(
            f"  3. template: {template['classification']} -> "
            f"{template['implied_change']} change{extra}"
        )

    chi2 = payload.get("chi2")
    if chi2:
        click.echo(
            f"  4. chi2 over the reference block: {chi2['chi2_over_reference']}"
            "   (should be ~1)"
        )

    if result.skipped:
        click.echo("\n  skipped:")
        for name, reason in result.skipped.items():
            click.echo(f"    {name}: {reason}")

    click.echo(f"\n  verdict: {result.verdict}")
    click.echo(f"\n  {len(result.written)} file(s) -> {out_dir}")


def _write_result_manifest(path: str, result, out_dir: Path) -> None:
    """Write an ``ndip-tool-result/1`` manifest for an orchestrator."""
    from nr_workbench._vendor.result_manifest import write_manifest

    payload = result.payload
    amplitude = payload.get("amplitude") or {}
    template = payload.get("template") or {}
    write_manifest(
        path,
        "nrw-tnr-assess",
        "ok" if not result.skipped else "needs-reprocessing",
        params={"label": result.label, "out_dir": str(out_dir)},
        artifacts={p.name: str(p) for p in result.written},
        info={
            "verdict": payload.get("verdict"),
            "max_significance": amplitude.get("max_significance"),
            "chi2_res_median": amplitude.get("chi2_res_median"),
            "trajectory": amplitude.get("trajectory"),
            "implied_change": template.get("implied_change"),
            "skipped": ",".join(result.skipped) or None,
        },
    )


def run_metric(
    metric: str,
    *,
    data_dir: str,
    out: str | None,
    label: str,
    as_json: bool,
    extra: dict[str, Any],
    **shared: Any,
) -> None:
    """Run a single metric.

    Args:
        metric: One of ``amplitude``, ``variogram``, ``qbands``, ``chi2``,
            ``pca``, ``kl``.
        data_dir: The run directory.
        out: Output directory.
        label: Filename prefix.
        as_json: Emit the metric's JSON block instead of prose.
        extra: Metric-specific options.
        shared: The shared block-selection options.

    Raises:
        click.ClickException: If the metric could not run.
    """
    from nr_workbench.tnr.report import assess

    run = _load(data_dir, shared)
    out_dir = (
        Path(out)
        if out
        else Path(data_dir).parent / "assessments" / (label or "assessment")
    )

    # Running one metric goes through the same driver so the reference
    # resolution and file naming cannot drift between the two paths; the
    # others are simply not requested.
    # One metric goes through the same driver as `assess`, so reference
    # resolution and file naming cannot drift between the two paths.
    result = assess(run, out_dir, label=label, plots=not extra.get("no_plots", False))

    block = result.payload.get(metric)
    if block is None:
        reason = result.skipped.get(metric, "no result")
        raise click.ClickException(f"{metric} could not be computed: {reason}")

    if as_json:
        click.echo(json.dumps(block, indent=2))
    else:
        click.echo(json.dumps(block, indent=2))
        click.echo(f"\n  files -> {out_dir}")


def run_manifest(*, data_dir: str, as_json: bool, **shared: Any) -> None:
    """Report a run's interval structure without computing any metric.

    Answers "what is in this directory?" -- how many intervals, of which types,
    over what time, against what reference -- which is the first thing anyone
    wants before spending time on the analysis.
    """
    run = _load(data_dir, shared)
    if as_json:
        click.echo(json.dumps(run.summary(), indent=2))
        return

    _echo_run(run)
    click.echo()
    click.echo(f"  {'#':>4}  {'TIME_S':>9}  {'DUR_S':>7}  {'TYPE':<8} LABEL")
    for i, (t, d, itype, lab) in enumerate(
        zip(run.times, run.durations, run.types, run.labels, strict=True)
    ):
        mark = "*" if run.in_ref[i] else " "
        click.echo(f" {mark}{i:>4}  {t:>9.1f}  {d:>7.1f}  {itype:<8} {lab}")
    click.echo("\n  * reference block")
