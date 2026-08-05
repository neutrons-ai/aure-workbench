"""Run every metric and reduce the result to a machine-readable verdict.

``assessment.json`` is the part that did not exist before, and it is what makes
the refactor pay for itself. The metrics have always produced plots a human
reads; this turns the reading-order in ``docs/notes.md`` into fields an agent
can act on -- above all ``template.implied_change``, which says whether a
thickness or an SLD should be freed in the fit that follows.

The plots and ASCII tables keep their original filenames, so existing notes and
habits still work.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from nr_workbench.tnr import ascii as ascii_out
from nr_workbench.tnr.constants import (
    DEFAULT_N_LAG_BINS,
    DEFAULT_N_QBANDS,
    DEFAULT_TEMPLATE_SMOOTH,
)
from nr_workbench.tnr.metrics.amplitude import analyze_amplitude
from nr_workbench.tnr.metrics.chi2 import analyze_chi2_ref, analyze_delta2
from nr_workbench.tnr.metrics.decomposition import analyze_kl, analyze_pca
from nr_workbench.tnr.metrics.qbands import analyze_qbands
from nr_workbench.tnr.metrics.variogram import analyze_variogram
from nr_workbench.tnr.run import TnrRun

ASSESSMENT_SCHEMA = "nrw-tnr-assessment/1"

#: chi2_res above this means one template does not describe the change, so the
#: amplitude trajectory is not a faithful summary and PCA is the next step.
CHI2_RES_MULTI_TEMPLATE = 1.5

#: |a/sigma| below this over the whole run is consistent with no change.
SIGNIFICANCE_FLOOR = 3.0

#: How many sigma above the pure-noise floor a variogram bin must sit before
#: the run counts as changing. The floor is exactly gamma = 1 and the metric
#: reports its own spread, so this is a significance test rather than a
#: threshold: real runs sit at gamma ~ 1.16 with an error of 0.03.
VARIOGRAM_SIGMA = 3.0

#: Fraction of the run's amplitude span within which the trajectory counts as
#: having plateaued.
PLATEAU_TOLERANCE = 0.1


@dataclass
class Assessment:
    """The outcome of assessing one run.

    Attributes:
        label: Filename prefix and plot title label.
        run: The loaded run.
        payload: The ``assessment.json`` contents.
        written: Files produced, newest last.
        skipped: Metrics that could not run, with the reason.
    """

    label: str
    run: TnrRun
    payload: dict[str, Any] = field(default_factory=dict)
    written: list[Path] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)

    @property
    def verdict(self) -> str:
        """The one-line conclusion."""
        return str(self.payload.get("verdict", ""))


def assess(
    run: TnrRun,
    out_dir: Path,
    *,
    label: str = "",
    template_source: str = "late",
    template_smooth: int = DEFAULT_TEMPLATE_SMOOTH,
    n_qbands: int = DEFAULT_N_QBANDS,
    qband_edges: list[float] | None = None,
    variogram_bins: int = DEFAULT_N_LAG_BINS,
    variogram_min_pairs: int = 3,
    variogram_log_bins: bool = True,
    delta2_clip: str = "mean",
    heatmap_coadd_seconds: float | None = None,
    plots: bool = True,
) -> Assessment:
    """Run every metric, write the outputs, and build the verdict.

    Args:
        run: The loaded run.
        out_dir: Directory to write outputs into.
        label: Filename prefix and plot-title label.
        template_source: ``late`` or ``pca``.
        template_smooth: Boxcar width for the template, in Q points.
        n_qbands: Number of Q bands.
        qband_edges: Explicit band edges, overriding ``n_qbands``.
        variogram_bins: Number of lag bins.
        variogram_min_pairs: Minimum interval pairs per lag bin.
        variogram_log_bins: Space lag bins logarithmically.
        delta2_clip: ``mean`` or ``element``.
        heatmap_coadd_seconds: Coadd the residual heatmap to this block size.
        plots: Render PNGs. Off makes the assessment much faster when only the
            numbers are wanted.

    Returns:
        The assessment.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = f"{label}_" if label else ""

    result = Assessment(label=label, run=run)
    payload: dict[str, Any] = {
        "schema": ASSESSMENT_SCHEMA,
        "label": label,
        **run.summary(),
    }

    def out(name: str) -> Path:
        return out_dir / f"{prefix}{name}"

    def record(path: Path) -> Path:
        result.written.append(path)
        return path

    # Ordered as docs/notes.md prescribes: the cheap assumption-free check
    # first, the easily-misread one last.
    payload["variogram"] = _do_variogram(
        run,
        result,
        out,
        record,
        variogram_bins,
        variogram_min_pairs,
        variogram_log_bins,
        plots,
    )
    payload["amplitude"], payload["template"] = _do_amplitude(
        run,
        result,
        out,
        record,
        template_source,
        template_smooth,
        plots,
    )
    payload["qbands"] = _do_qbands(
        run, result, out, record, n_qbands, qband_edges, plots
    )
    payload["chi2"] = _do_chi2(run, result, out, record, delta2_clip, plots)
    payload["pca"] = _do_pca(run, result, out, record, plots)
    payload["kl"] = _do_kl(run, result, out, record, plots)

    if plots:
        _do_heatmaps(run, result, out, record, heatmap_coadd_seconds)

    payload["skipped"] = dict(result.skipped)
    payload["verdict"] = build_verdict(payload)
    result.payload = payload

    record(out("assessment.json")).write_text(
        json.dumps(payload, indent=2, default=_json_default) + "\n", encoding="utf-8"
    )
    return result


# --------------------------------------------------------------------------
# Per-metric drivers
# --------------------------------------------------------------------------


def _do_variogram(run, result, out, record, n_bins, min_pairs, log_bins, plots):
    """Lag variogram: is anything changing, and on what timescale?"""
    try:
        res = analyze_variogram(
            run.times,
            run.types,
            run.R,
            run.dR,
            run.valid,
            n_bins=n_bins,
            min_pairs=min_pairs,
            log_bins=log_bins,
            durations=run.durations,
        )
    except ValueError as exc:
        result.skipped["variogram"] = str(exc)
        return None

    ascii_out.write_variogram_ascii(
        str(record(out("variogram.txt"))), res, str(run.data_dir), str(run.json_path)
    )
    if plots:
        from nr_workbench.tnr.plots import plot_variogram

        plot_variogram(
            res, result.label, str(record(out("variogram.png"))), log_lag=log_bins
        )

    per_type = {}
    flat = True
    knee = None
    for itype, d in res.per_type.items():
        gamma = np.asarray(d["gamma"], dtype=float)
        lags = np.asarray(d["lag_center"], dtype=float)
        excess = np.asarray(d["excess_amp"], dtype=float)

        # gamma = 1 is the pure-noise floor, and the metric already reports the
        # spread that floor has. So "is it rising?" is a significance question,
        # not a threshold one: a real run can sit at gamma = 1.16 against an
        # error of 0.03, which is a 5-sigma rise that any fixed cut near 1.3
        # would call flat.
        errors = np.asarray(d["gamma_err_theory"], dtype=float)
        with np.errstate(divide="ignore", invalid="ignore"):
            sigmas = np.where(errors > 0, (gamma - 1.0) / errors, 0.0)
        significant = np.isfinite(sigmas) & (sigmas > VARIOGRAM_SIGMA)

        if significant.any():
            flat = False
            first = float(lags[np.argmax(significant)])
            knee = first if knee is None else min(knee, first)

        per_type[itype] = {
            "gamma_first": _round(float(gamma[0]), 4) if gamma.size else None,
            "gamma_last": _round(float(gamma[-1]), 4) if gamma.size else None,
            "lag_first_s": _round(float(lags[0]), 1) if lags.size else None,
            "lag_last_s": _round(float(lags[-1]), 1) if lags.size else None,
            "change_over_noise": _round(float(excess[-1]), 4) if excess.size else None,
            "max_sigma_above_noise": _round(float(np.nanmax(sigmas)), 2)
            if sigmas.size
            else None,
            "rises": bool(significant.any()),
        }

    return {
        "flat": flat,
        "knee_s": _round(knee, 1) if knee is not None else None,
        "per_type": per_type,
    }


def _do_amplitude(run, result, out, record, template_source, smooth, plots):
    """The primary metric, plus what its template says about the change."""
    pca_component = pca_mask = None
    if template_source == "pca":
        try:
            _, comps, _, _, _ = analyze_pca(
                run.times, run.q, run.R, run.valid, n_components=1
            )
        except ValueError as exc:
            result.skipped["amplitude"] = f"pca template unavailable: {exc}"
            return None, None
        pca_component = comps[0]
        pca_mask = np.all(run.valid, axis=0) & np.all(run.R > 0, axis=0)

    try:
        res = analyze_amplitude(
            run.times,
            run.q,
            run.R,
            run.dR,
            run.valid,
            run.ref_idx,
            run.late_idx,
            template=template_source,
            smooth=smooth,
            loo=run.options.loo,
            min_ref_snr=run.options.min_ref_snr,
            pca_component=pca_component,
            pca_mask=pca_mask,
            ref_desc=run.ref_desc,
            late_desc=run.late_desc,
        )
    except ValueError as exc:
        result.skipped["amplitude"] = str(exc)
        return None, None

    ascii_out.write_amplitude_ascii(
        str(record(out("amplitude.txt"))),
        run.times,
        run.types,
        run.labels,
        res,
        str(run.data_dir),
        str(run.json_path),
    )
    if plots:
        from nr_workbench.tnr.plots import plot_amplitude

        plot_amplitude(
            run.times,
            run.types,
            res,
            run.q,
            result.label,
            str(record(out("amplitude.png"))),
        )

    finite = np.isfinite(res.a)
    per_type_sigma = {}
    types_arr = np.array(run.types)
    for itype in sorted(set(run.types)):
        m = (types_arr == itype) & finite
        if m.any():
            per_type_sigma[itype] = _round(float(np.median(res.sigma_a[m])), 5)

    amplitude = {
        "max_significance": _round(float(np.nanmax(np.abs(res.signif))), 3),
        "chi2_res_median": _round(float(np.nanmedian(res.chi2_res)), 4),
        "chi2_res_mean": _round(float(np.nanmean(res.chi2_res)), 4),
        "a_range": [
            _round(float(np.nanmin(res.a)), 4),
            _round(float(np.nanmax(res.a)), 4),
        ],
        "trajectory": classify_trajectory(run.times, res.a, finite, res.sigma_a),
        "plateau": _has_plateaued(res.a, finite),
        "single_template_sufficient": bool(
            np.isfinite(np.nanmedian(res.chi2_res))
            and np.nanmedian(res.chi2_res) < CHI2_RES_MULTI_TEMPLATE
        ),
        "median_sigma_a": per_type_sigma,
    }
    return amplitude, classify_template(run.q, res.template, res.t_valid)


def _do_qbands(run, result, out, record, n_bands, edges, plots):
    """Where in Q the change lives."""
    try:
        res = analyze_qbands(
            run.times,
            run.q,
            run.R,
            run.dR,
            run.valid,
            run.ref_idx,
            n_bands=n_bands,
            edges=edges,
            loo=run.options.loo,
            min_ref_snr=run.options.min_ref_snr,
            ref_desc=run.ref_desc,
        )
    except ValueError as exc:
        result.skipped["qbands"] = str(exc)
        return None

    ascii_out.write_qbands_ascii(
        str(record(out("qbands.txt"))),
        run.times,
        run.types,
        run.labels,
        res,
        str(run.data_dir),
        str(run.json_path),
    )
    if plots:
        from nr_workbench.tnr.plots import plot_qbands

        plot_qbands(
            run.times, run.types, res, result.label, str(record(out("qbands.png")))
        )

    last = int(np.argmax(run.times))
    final = [
        {
            "q_min": _round(float(res.edges[b]), 5),
            "q_max": _round(float(res.edges[b + 1]), 5),
            "value": _round(float(res.values[last, b]), 5),
            "error": _round(float(res.errors[last, b]), 5),
        }
        for b in range(res.values.shape[1])
    ]
    signs = {int(np.sign(band["value"])) for band in final if band["value"]}
    return {
        "at_t_s": _round(float(run.times[last]), 1),
        "bands": final,
        "bands_move_together": len(signs) <= 1,
    }


def _do_chi2(run, result, out, record, delta2_clip, plots):
    """Running chi-squared, delta, and significance."""
    chi2, n_valid, snr, delta, signif = analyze_chi2_ref(
        run.R,
        run.dR,
        run.valid,
        run.R_ref,
        run.dR_ref,
        run.ref_valid,
        run.in_ref,
        loo=run.options.loo,
    )
    delta2 = analyze_delta2(
        run.times, run.types, run.R, run.dR, run.valid, clip=delta2_clip
    )

    ascii_out.write_chi2_ascii(
        str(record(out("chi2.txt"))),
        run.times,
        chi2,
        n_valid,
        snr,
        delta,
        signif,
        delta2,
        run.types,
        run.labels,
        str(run.data_dir),
        str(run.json_path),
        run.ref_desc,
    )
    if plots:
        from nr_workbench.tnr.plots import plot_chi2_with_band, plot_split_by_type

        plot_chi2_with_band(
            run.times,
            run.types,
            chi2,
            n_valid,
            snr,
            delta,
            signif,
            result.label,
            str(record(out("chi2.png"))),
        )
        title = (
            f"Run {result.label} — $\\chi^2$ vs time (per type)"
            if result.label
            else "$\\chi^2$ vs time (per type)"
        )
        plot_split_by_type(
            run.times,
            chi2,
            run.types,
            "$\\chi^2$",
            title,
            str(record(out("chi2_split.png"))),
            hline=1.0,
        )
        d_title = (
            f"Run {result.label} — noise-corrected fractional change"
            if result.label
            else "noise-corrected fractional change"
        )
        plot_split_by_type(
            run.times,
            delta2,
            run.types,
            "$\\delta^2$ (fractional)",
            d_title,
            str(record(out("delta2.png"))),
            hline=0.0,
        )

    types_arr = np.array(run.types)
    final_delta = {}
    for itype in sorted(set(run.types)):
        m = (types_arr == itype) & np.isfinite(delta)
        if m.any():
            final_delta[itype] = _round(float(delta[m][-1]), 5)

    return {
        "chi2_range": [
            _round(float(np.nanmin(chi2)), 4),
            _round(float(np.nanmax(chi2)), 4),
        ],
        "chi2_over_reference": _round(float(np.nanmean(chi2[run.in_ref])), 4),
        "final_delta": final_delta,
    }


def _do_pca(run, result, out, record, plots):
    """PCA of the R(Q,t) matrix."""
    try:
        scores, components, evr, q_c, _ = analyze_pca(
            run.times, run.q, run.R, run.valid, n_components=3
        )
    except ValueError as exc:
        result.skipped["pca"] = str(exc)
        return None

    ascii_out.write_pca_ascii(
        str(record(out("pca_scores.txt"))),
        run.times,
        run.types,
        run.labels,
        scores,
        evr,
        str(run.data_dir),
        str(run.json_path),
    )
    if plots:
        from nr_workbench.tnr.plots import plot_pca

        plot_pca(
            run.times,
            run.types,
            scores,
            components,
            evr,
            q_c,
            result.label,
            str(record(out("pca.png"))),
        )

    return {"explained_variance": [_round(float(e), 5) for e in evr]}


def _do_kl(run, result, out, record, plots):
    """Symmetric KL divergence per interval."""
    kl = analyze_kl(run.times, run.types, run.R, run.dR, run.valid)

    ascii_out.write_kl_ascii(
        str(record(out("kl.txt"))),
        run.times,
        kl,
        run.types,
        run.labels,
        str(run.data_dir),
        str(run.json_path),
    )
    if plots:
        import matplotlib.pyplot as plt

        from nr_workbench.tnr.plots import _finish_figure, plot_by_type

        fig, ax = plt.subplots(figsize=(10, 6))
        title = (
            f"Run {result.label} — symmetric KL vs time"
            if result.label
            else "symmetric KL vs time"
        )
        plot_by_type(ax, run.times, kl, run.types, "Symmetric KL per Q", title)
        _finish_figure(fig, str(record(out("kl.png"))))

    return {"range": [_round(float(np.nanmin(kl)), 4), _round(float(np.nanmax(kl)), 4)]}


def _do_heatmaps(run, result, out, record, coadd_seconds):
    """Residual and RQ^4 heatmaps."""
    from nr_workbench.tnr.plots import plot_residual_heatmap, plot_rq4_heatmap

    try:
        plot_residual_heatmap(
            run.times,
            run.q,
            run.R,
            run.dR,
            run.valid,
            result.label,
            str(record(out("residual_heatmap.png"))),
            R_ref=run.R_ref,
            dR_ref=run.dR_ref,
            ref_valid=run.ref_valid,
            in_ref=run.in_ref,
            types=run.types,
            labels=run.labels,
            durations=run.durations,
            coadd_seconds=coadd_seconds,
            loo=run.options.loo,
        )
    except ValueError as exc:
        result.skipped["residual_heatmap"] = str(exc)
        result.written.pop()

    plot_rq4_heatmap(
        run.times,
        run.q,
        run.R,
        run.valid,
        result.label,
        str(record(out("rq4_heatmap.png"))),
    )


# --------------------------------------------------------------------------
# Interpretation
# --------------------------------------------------------------------------


def classify_template(
    q: np.ndarray, template: np.ndarray, tmask: np.ndarray
) -> dict[str, Any]:
    """Decide whether the template implies a thickness or a contrast change.

    This is the field the model layer consumes. The rule is from
    ``docs/tnr-amplitude.md``: a template that oscillates in Q with node
    spacing dQ is a Kiessig-fringe shift, i.e. a **thickness** change of order
    pi/dQ; a template of one sign everywhere is an overall reflectivity change,
    i.e. an **SLD contrast** change.

    Getting this backwards means freeing the wrong parameter, so the
    classification only commits when the sign changes are unambiguous.

    Args:
        q: The Q grid.
        template: The change template.
        tmask: Where the template is defined.

    Returns:
        The ``template`` block of the assessment.
    """
    values = np.asarray(template, dtype=float)[tmask]
    q_used = np.asarray(q, dtype=float)[tmask]
    if values.size < 4:
        return {
            "classification": "unknown",
            "implied_change": None,
            "note": "too few Q points to classify",
        }

    scale = float(np.max(np.abs(values)))
    if scale <= 0:
        return {
            "classification": "flat",
            "implied_change": None,
            "note": "template is identically zero",
        }

    # Count sign changes only among points that are meaningfully non-zero, so
    # noise crossing zero near a node does not register as a lobe.
    significant = np.abs(values) > 0.2 * scale
    signs = np.sign(values[significant])
    crossings = int(np.count_nonzero(np.diff(signs) != 0)) if signs.size > 1 else 0

    positive = float(np.sum(values[values > 0]))
    negative = float(-np.sum(values[values < 0]))
    balance = min(positive, negative) / max(positive, negative, 1e-30)

    if crossings >= 1 and balance > 0.15:
        nodes = _node_positions(q_used, values)
        spacing = float(np.median(np.diff(nodes))) if len(nodes) > 1 else None
        implied_thickness = (np.pi / spacing) if spacing and spacing > 0 else None
        return {
            "classification": "oscillatory",
            "implied_change": "thickness",
            "n_lobes": crossings + 1,
            "node_spacing_invA": _round(spacing, 6) if spacing else None,
            "implied_thickness_A": _round(implied_thickness, 1)
            if implied_thickness
            else None,
            "note": "template changes sign in Q: a fringe shift, so free a thickness",
        }

    return {
        "classification": "one-sign",
        "implied_change": "sld_contrast",
        "n_lobes": 1,
        "note": "template keeps one sign across Q: an overall reflectivity change, so free an SLD",
    }


#: A sigmoid's middle slope must exceed the mean of its outer slopes by this
#: factor. Chosen so a straight line (ratio 1) is never called sigmoidal and a
#: logistic with a realistic induction period is.
SIGMOID_SLOPE_RATIO = 2.5

#: A segment counts as running backwards only if it undoes this fraction of the
#: overall span, so noise on a plateau is not mistaken for a reversal.
REVERSAL_FRACTION = 0.15


#: Drift between the first and last thirds must exceed this many combined
#: standard errors to count as a trajectory at all.
DRIFT_SIGMA = 3.0


def classify_trajectory(
    times: np.ndarray,
    a: np.ndarray,
    finite: np.ndarray,
    sigma_a: np.ndarray | None = None,
) -> str:
    """Describe the shape of a(t): the thing that picks a constraint form.

    Two decisions, in order.

    **Is there a trajectory at all?** Answered against the error bars, not by
    eye: the difference between the first and last thirds' weighted means must
    exceed :data:`DRIFT_SIGMA` combined standard errors. Without that test, a
    run of pure noise has a perfectly good peak-to-peak range and gets
    classified by shape -- reliably as ``non-monotonic``, because noise wanders.

    **What shape is it?** Judged by where the slope lives, not by departure
    from a chord. A chord-residual test looks reasonable but breaks whenever
    the trajectory overshoots and settles back: the chord then ends below the
    plateau, the late residual swamps the middle one, and a textbook sigmoid
    reads as non-monotonic. Slope concentration is the defining property of a
    sigmoid and does not care where the endpoints land.

    Args:
        times: Interval times.
        a: The amplitude per interval.
        finite: Where ``a`` is finite.
        sigma_a: Per-interval uncertainty on ``a``. Without it the significance
            test is skipped and a noisy flat run may be called non-monotonic.

    Returns:
        ``flat``, ``monotonic``, ``sigmoidal``, ``non-monotonic``, or
        ``unknown`` when there are too few points to tell.
    """
    t = np.asarray(times, dtype=float)[finite]
    values = np.asarray(a, dtype=float)[finite]
    if values.size < 6:
        return "unknown"

    span = float(np.nanmax(values) - np.nanmin(values))
    if span <= 0:
        return "flat"

    if sigma_a is not None:
        errors = np.asarray(sigma_a, dtype=float)[finite]
        if (
            errors.size == values.size
            and np.all(np.isfinite(errors))
            and np.all(errors > 0)
        ):
            third = max(2, values.size // 3)
            early, late = values[:third], values[-third:]
            se_early = float(np.sqrt(np.sum(errors[:third] ** 2))) / third
            se_late = float(np.sqrt(np.sum(errors[-third:] ** 2))) / third
            combined = float(np.hypot(se_early, se_late))
            drift = abs(float(np.mean(late)) - float(np.mean(early)))
            if combined > 0 and drift < DRIFT_SIGMA * combined:
                return "flat"

    # Split by time, not by index: hold and eis intervals differ in duration,
    # so equal counts do not mean equal elapsed time.
    edges = np.quantile(t, [0.0, 1 / 3, 2 / 3, 1.0])
    segments = [
        (t >= edges[i]) & (t <= edges[i + 1])
        if i == 2
        else (t >= edges[i]) & (t < edges[i + 1])
        for i in range(3)
    ]
    if any(mask.sum() < 2 for mask in segments):
        return "unknown"

    slopes = [float(np.polyfit(t[mask], values[mask], 1)[0]) for mask in segments]
    overall = float(np.polyfit(t, values, 1)[0])

    # Scale-free comparison: how much of the span each segment accounts for.
    rises = [slope * (edges[i + 1] - edges[i]) / span for i, slope in enumerate(slopes)]

    if abs(overall) * (t[-1] - t[0]) < 0.25 * span:
        # No net movement, but the values do range -- so it wanders.
        return "flat" if max(abs(r) for r in rises) < 0.3 else "non-monotonic"

    direction = np.sign(overall)
    if any(np.sign(r) == -direction and abs(r) > REVERSAL_FRACTION for r in rises):
        return "non-monotonic"

    outer = (abs(rises[0]) + abs(rises[2])) / 2.0
    if abs(rises[1]) > SIGMOID_SLOPE_RATIO * max(outer, 1e-9):
        return "sigmoidal"
    return "monotonic"


def build_verdict(payload: dict[str, Any]) -> str:
    """Reduce the metrics to one sentence a human or an agent can act on.

    Args:
        payload: The assembled assessment.

    Returns:
        A single-sentence verdict.
    """
    variogram = payload.get("variogram") or {}
    amplitude = payload.get("amplitude") or {}
    template = payload.get("template") or {}

    significance = amplitude.get("max_significance")
    variogram_flat = bool(variogram.get("flat")) if variogram else None

    # The reading order says: if the variogram is flat, stop. Honour that
    # rather than letting a later metric talk over it -- the variogram is the
    # one measurement with no reference to misconfigure, so when it disagrees
    # with the amplitude the disagreement is the finding.
    if variogram_flat:
        if significance is not None and significance >= SIGNIFICANCE_FLOOR:
            return (
                "ambiguous: the variogram is flat but the amplitude is "
                f"significant (max |a/sigma| = {significance:.1f}). The two "
                "disagree, so treat neither as settled -- check whether the "
                "reference block spans a period when the sample was already "
                "changing"
            )
        return "no detectable change; the variogram is flat"

    if significance is not None and significance < SIGNIFICANCE_FLOOR:
        return (
            f"no significant change (max |a/sigma| = {significance:.1f}); "
            "nothing here needs fitting"
        )

    parts = ["changing"]
    if amplitude.get("single_template_sufficient"):
        parts.append("a single reaction coordinate describes it")
    else:
        parts.append(
            "chi2_res is above 1, so the change direction rotates in Q -- "
            "look at the PCA before fitting"
        )

    trajectory = amplitude.get("trajectory")
    if trajectory == "sigmoidal":
        parts.append("a(t) is sigmoidal, so fit with a logistic constraint")
    elif trajectory == "monotonic":
        parts.append("a(t) is monotonic, so a linear-in-time constraint fits")
    elif trajectory == "non-monotonic":
        parts.append("a(t) is not monotonic; a single functional form may not suffice")

    implied = template.get("implied_change")
    if implied == "thickness":
        parts.append("the template oscillates in Q, so free a thickness")
    elif implied == "sld_contrast":
        parts.append("the template keeps one sign, so free an SLD")

    if amplitude.get("plateau"):
        parts.append("the process completed within the run")

    return "; ".join(parts)


def _node_positions(q: np.ndarray, values: np.ndarray) -> list[float]:
    """Q positions where the template crosses zero, by linear interpolation."""
    nodes: list[float] = []
    for i in range(len(values) - 1):
        left, right = values[i], values[i + 1]
        if left == 0.0:
            nodes.append(float(q[i]))
        elif left * right < 0:
            frac = abs(left) / (abs(left) + abs(right))
            nodes.append(float(q[i] + frac * (q[i + 1] - q[i])))
    return nodes


def _has_plateaued(a: np.ndarray, finite: np.ndarray) -> bool:
    """Whether the trajectory flattens over its final third.

    A plateau means the process finished inside the run, which is a statement
    worth making explicitly and one a running chi-squared cannot support.
    """
    values = np.asarray(a, dtype=float)[finite]
    if values.size < 6:
        return False
    span = float(np.nanmax(values) - np.nanmin(values))
    if span <= 0:
        return False
    third = max(2, values.size // 3)
    tail = values[-third:]
    return bool((float(np.nanmax(tail) - np.nanmin(tail)) / span) < PLATEAU_TOLERANCE)


def _round(value: Any, digits: int) -> float | None:
    """Round for JSON, mapping non-finite values to None."""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if not np.isfinite(number) else round(number, digits)


def _json_default(value: Any) -> Any:
    """Serialise numpy scalars and arrays."""
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return None if not np.isfinite(value) else float(value)
    if isinstance(value, np.ndarray):
        return value.tolist()
    return str(value)
