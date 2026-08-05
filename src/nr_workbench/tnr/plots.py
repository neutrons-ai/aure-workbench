"""Plot rendering.

Adapted from ``experiments-2025/tnr_chi2.py`` (see ``upstream.toml``).

matplotlib is imported at module scope here and nowhere else in ``tnr/``, so
the metrics stay importable in a headless context without paying for it. The
backend is forced to Agg on import: these functions write files, and letting
matplotlib pick an interactive backend makes them hang on a machine with no
display.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from nr_workbench.tnr.constants import (  # noqa: E402
    EIS_COLOR,
    HOLD_COLOR,
    OTHER_COLOR,
    Z68,
    Z95,
)
from nr_workbench.tnr.intervals import ordered_types, type_style  # noqa: E402
from nr_workbench.tnr.metrics.chi2 import chi2_quantile  # noqa: E402
from nr_workbench.tnr.reference import coadd_in_time, pair_variance  # noqa: E402

if TYPE_CHECKING:  # annotations only; importing these at runtime is circular-ish
    from nr_workbench.tnr.metrics.amplitude import AmplitudeResult
    from nr_workbench.tnr.metrics.qbands import QBandResult
    from nr_workbench.tnr.metrics.variogram import VariogramResult


def plot_by_type(ax, times, values, types, ylabel, title):
    """Scatter values vs times with per-type coloring + grey connecting line."""
    ax.plot(times, values, "-", color="lightgrey", linewidth=1, zorder=1)
    types_arr = np.array(types)
    for itype, color in [("hold", HOLD_COLOR), ("eis", EIS_COLOR)]:
        m = types_arr == itype
        if m.any():
            ax.plot(
                times[m],
                values[m],
                "o",
                color=color,
                markersize=5,
                label=itype,
                zorder=2,
            )
    other = ~np.isin(types_arr, ["hold", "eis"])
    if other.any():
        ax.plot(
            times[other],
            values[other],
            "o",
            color=OTHER_COLOR,
            markersize=5,
            label="other",
            zorder=2,
        )
    ax.set_xlabel("Time (s)")
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.grid(True, alpha=0.3)
    ax.legend()


def _finish_figure(fig, out_path, dpi=150):
    """Lay out, save, and close the figure.

    The original tool kept figures open when it was going to call
    ``plt.show()``. ``nrw tnr`` only ever writes files, so figures are always
    closed -- which also stops memory growing when a whole project's runs are
    assessed in one process.
    """
    fig.tight_layout()
    fig.savefig(out_path, dpi=dpi)
    plt.close(fig)


def errorbar_by_type(
    ax,
    times,
    values,
    errors,
    types,
    ylabel=None,
    title=None,
    connect=True,
    hlines=(),
    legend=True,
    markersize=4,
):
    """Errorbar scatter colored/marked per interval_type, grey connecting line.

    Mirrors :func:`plot_by_type`'s color and legend convention but adds error
    bars and a distinct marker shape per type, so the type encoding survives in
    greyscale.
    """
    finite = np.isfinite(values)
    if connect and finite.any():
        ax.plot(
            times[finite], values[finite], "-", color="lightgrey", linewidth=1, zorder=1
        )
    types_arr = np.array(types)
    for itype in ordered_types(types):
        m = (types_arr == itype) & finite
        if not m.any():
            continue
        color, marker = type_style(itype)
        ax.errorbar(
            times[m],
            values[m],
            yerr=None if errors is None else errors[m],
            fmt=marker,
            color=color,
            markersize=markersize,
            elinewidth=0.8,
            capsize=0,
            label=itype,
            zorder=2,
        )
    for hl in hlines:
        ax.axhline(hl, color="k", linestyle="--", linewidth=0.8, alpha=0.5)
    if ylabel:
        ax.set_ylabel(ylabel)
    if title:
        ax.set_title(title)
    ax.grid(True, alpha=0.3)
    if legend:
        ax.legend(loc="best", fontsize=9)


def _cell_edges(centers: np.ndarray) -> np.ndarray:
    """Midpoint cell edges for pcolormesh, extrapolated half a step at the ends."""
    centers = np.asarray(centers, dtype=float)
    if len(centers) == 1:
        return np.array([centers[0] - 0.5, centers[0] + 0.5])
    return np.concatenate(
        [
            [centers[0] - (centers[1] - centers[0]) / 2],
            (centers[:-1] + centers[1:]) / 2,
            [centers[-1] + (centers[-1] - centers[-2]) / 2],
        ]
    )


def plot_chi2_with_band(
    times, types, chi2, n_valid, snr, delta, signif, label, out_path
):
    """chi^2 over its expected fluctuation band, then the two corrected readouts.

    The band exists to make it obvious that the wiggles in chi^2 are
    statistical: for N_Q Q points, chi^2 has an intrinsic spread of
    sqrt(2/N_Q) about 1 even when nothing at all has changed.

    The lower panels make the counting-time trap visible. ``snr`` still splits
    hold from eis, because it is measured in units of each interval's own noise.
    ``delta`` -- the fractional change -- does not, because it is a property of
    the sample. If the two panels disagree, believe ``delta``.
    """
    fig, axes = plt.subplots(
        3, 1, figsize=(10, 11), sharex=True, gridspec_kw={"height_ratios": [3, 2, 2]}
    )
    ax = axes[0]

    order = np.argsort(times)
    t_s = times[order]
    for z, alpha, lbl in ((Z95, 0.18, "95%"), (Z68, 0.3, "68%")):
        ax.fill_between(
            t_s,
            chi2_quantile(n_valid[order], -z),
            chi2_quantile(n_valid[order], z),
            color="grey",
            alpha=alpha,
            linewidth=0,
            label=f"expected $\\chi^2/N$ spread, {lbl} (Wilson–Hilferty)",
            zorder=0,
        )
    errorbar_by_type(
        ax,
        times,
        chi2,
        None,
        types,
        ylabel="$\\chi^2$",
        hlines=(1.0,),
        legend=False,
        markersize=5,
    )
    ax.set_title(
        f"Run {label} — $\\chi^2$ vs shared coadded reference"
        if label
        else "$\\chi^2$ vs shared coadded reference"
    )
    ax.legend(loc="best", fontsize=8)

    ax = axes[1]
    errorbar_by_type(
        ax,
        times,
        snr,
        None,
        types,
        ylabel="$\\sqrt{\\max(0,\\chi^2-1)}$\n(change ÷ own noise)",
        hlines=(0.0,),
        legend=False,
        markersize=4,
    )
    ax2 = ax.twinx()
    ax2.plot(times, signif, ".", color="k", markersize=2, alpha=0.35, zorder=0)
    ax2.set_ylabel("significance ($\\sigma$)", alpha=0.6)
    ax.set_title(
        "Change ÷ noise — still splits by counting time (right axis: significance)",
        fontsize=9,
    )

    ax = axes[2]
    errorbar_by_type(
        ax,
        times,
        delta,
        None,
        types,
        ylabel="$\\delta$ (fractional RMS change)",
        hlines=(0.0,),
        legend=False,
        markersize=4,
    )
    ax.set_xlabel("Time (s)")
    ax.set_title(
        "Fractional change — noise variance subtracted, so this IS "
        "count-time independent",
        fontsize=9,
    )

    _finish_figure(fig, out_path)


def plot_split_by_type(times, values, types, ylabel, title, out_path, hline=None):
    """Stacked subplot, one per interval_type present, sharing the time axis."""
    types_arr = np.array(types)
    present = [t for t in ["hold", "eis"] if (types_arr == t).any()]
    others = sorted(set(types_arr) - {"hold", "eis"})
    present += others
    if not present:
        present = ["all"]

    fig, axes = plt.subplots(
        len(present),
        1,
        figsize=(10, 3 + 2.2 * len(present)),
        sharex=True,
        squeeze=False,
    )
    axes = axes[:, 0]
    color_map = {"hold": HOLD_COLOR, "eis": EIS_COLOR}
    for ax, itype in zip(axes, present):
        m = types_arr == itype if itype != "all" else np.ones(len(times), dtype=bool)
        color = color_map.get(itype, OTHER_COLOR)
        ax.plot(times[m], values[m], "-", color="lightgrey", linewidth=1, zorder=1)
        ax.plot(
            times[m], values[m], "o", color=color, markersize=5, zorder=2, label=itype
        )
        if hline is not None:
            ax.axhline(hline, color="k", linestyle="--", linewidth=0.8, alpha=0.5)
        ax.set_ylabel(ylabel)
        ax.grid(True, alpha=0.3)
        ax.legend(loc="best", fontsize=9)
    axes[0].set_title(title)
    axes[-1].set_xlabel("Time (s)")
    _finish_figure(fig, out_path)


def plot_amplitude(times, types, res: AmplitudeResult, q, label, out_path):
    """Three panels: a(t) +- sigma, the residual chi2, and the template shape."""
    fig, axes = plt.subplots(
        3, 1, figsize=(10, 10), gridspec_kw={"height_ratios": [3, 1.2, 1.6]}
    )

    ax = axes[0]
    errorbar_by_type(
        ax,
        times,
        res.a,
        res.sigma_a,
        types,
        ylabel="amplitude $a$ (0 = initial, 1 = final)",
        hlines=(0.0, 1.0),
        markersize=4,
    )
    finite = np.isfinite(res.signif)
    peak = np.max(np.abs(res.signif[finite])) if finite.any() else float("nan")
    ax.set_title(
        (
            f"Run {label} — change amplitude along the template"
            if label
            else "change amplitude along the template"
        )
        + f"   (max |a/σ| = {peak:.1f})"
    )

    ax = axes[1]
    errorbar_by_type(
        ax,
        times,
        res.chi2_res,
        None,
        types,
        ylabel="$\\chi^2_{\\rm res}$",
        hlines=(1.0,),
        legend=False,
        markersize=3,
    )
    ax.set_xlabel("Time (s)")
    ax.set_title(
        "Residual after removing $a\\,T(Q)$ — near 1 means one "
        "template describes the whole run",
        fontsize=9,
    )
    axes[0].sharex(ax)

    ax = axes[2]
    tv = res.t_valid
    ax.plot(q[tv], res.template[tv], "-", color="k", linewidth=1.2)
    ax.axhline(0.0, color="k", linestyle="--", linewidth=0.8, alpha=0.5)
    ax.set_xscale("log")
    ax.set_xlabel("Q (Å$^{-1}$)")
    ax.set_ylabel("template $T(Q)$\n(fractional $\\Delta R/R$)")
    ax.set_title(f"Change template — {res.desc['template']}", fontsize=9)
    ax.grid(True, alpha=0.3)

    _finish_figure(fig, out_path)


def plot_variogram(res: VariogramResult, label, out_path, log_lag=True):
    """gamma vs lag, with the noise floor at 1, and the change/noise amplitude."""
    fig, axes = plt.subplots(
        2, 1, figsize=(10, 8), sharex=True, gridspec_kw={"height_ratios": [3, 2]}
    )

    for itype, d in res.per_type.items():
        color, marker = type_style(itype)
        axes[0].errorbar(
            d["lag_center"],
            d["gamma"],
            yerr=d["gamma_err"],
            fmt=marker + "-",
            color=color,
            markersize=5,
            linewidth=1,
            capsize=2,
            label=itype,
        )
        axes[1].plot(
            d["lag_center"],
            d["excess_amp"],
            marker + "-",
            color=color,
            markersize=5,
            linewidth=1,
            label=itype,
        )

    axes[0].axhline(1.0, color="k", linestyle="--", linewidth=0.8, alpha=0.6)
    axes[0].set_ylabel("$\\gamma$ (pairwise $\\chi^2$)")
    axes[0].set_title(
        (f"Run {label} — lag variogram" if label else "lag variogram")
        + "   ($\\gamma = 1$ is the pure-noise floor; no reference needed)"
    )
    axes[0].grid(True, alpha=0.3)
    axes[0].legend(loc="best", fontsize=9)

    axes[1].axhline(0.0, color="k", linestyle="--", linewidth=0.8, alpha=0.6)
    axes[1].set_ylabel("$\\sqrt{\\max(0,\\gamma-1)}$\n(change ÷ noise)")
    axes[1].set_xlabel("Time separation $\\Delta t$ (s)")
    if log_lag:
        axes[1].set_xscale("log")
    axes[1].grid(True, alpha=0.3)
    axes[1].legend(loc="best", fontsize=9)

    _finish_figure(fig, out_path)


def plot_qbands(times, types, res: QBandResult, label, out_path):
    """One errorbar series per Q band; colour runs low-Q to high-Q."""
    B = res.values.shape[1]
    fig, ax = plt.subplots(figsize=(10, 6))
    colors = plt.cm.viridis(np.linspace(0.05, 0.9, B))
    types_arr = np.array(types)
    eis = types_arr == "eis"

    for b in range(B):
        lo, hi = res.edges[b], res.edges[b + 1]
        lbl = f"{lo:.4f}–{hi:.4f} Å$^{{-1}}$ (N={res.n_q[b]})"
        labelled = False
        for m, face in ((~eis, colors[b]), (eis, "none")):
            if not m.any():
                continue
            ax.errorbar(
                times[m],
                res.values[m, b],
                yerr=res.errors[m, b],
                fmt="o",
                color=colors[b],
                markerfacecolor=face,
                markersize=4,
                elinewidth=0.7,
                capsize=0,
                label=None if labelled else lbl,
                zorder=2,
            )
            labelled = True

    ax.axhline(0.0, color="k", linestyle="--", linewidth=0.8, alpha=0.6)
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("fractional change $(R-R_{\\rm ref})/R_{\\rm ref}$")
    ax.set_title(
        (
            f"Run {label} — fractional change by Q band"
            if label
            else "fractional change by Q band"
        )
        + "   (open markers = eis)"
    )
    ax.grid(True, alpha=0.3)
    ax.legend(loc="best", fontsize=8)
    _finish_figure(fig, out_path)


def plot_residual_heatmap(
    times,
    q,
    R,
    dR,
    valid,
    label,
    out_path,
    R_ref=None,
    dR_ref=None,
    ref_valid=None,
    in_ref=None,
    types=None,
    labels=None,
    durations=None,
    coadd_seconds=None,
    loo=True,
):
    """Standardized residual map z(t,Q) against a reference.

        z = (R(t,Q) - R_ref(Q)) / sqrt(dR(t,Q)^2 + s_t dR_ref(Q)^2)

    When ``R_ref`` is not given this falls back to the first interval as the
    reference and its uncertainty alone, matching the original behaviour.
    Passing the shared coadd instead removes the reference's own counting noise
    (which otherwise inflates the map by ~sqrt(2) and stamps one 30 s slice's
    fluctuations across every column), so the map gets visibly sharper and the
    98th-percentile colour scale grows — that is expected, not a bug.

    ``coadd_seconds`` groups adjacent intervals first so every cell has
    comparable counting statistics.
    """
    if coadd_seconds:
        if durations is None or types is None or labels is None:
            raise ValueError("time coadding needs types, labels and durations")
        times, types, labels, R, dR, valid, durations = coadd_in_time(
            times, types, labels, R, dR, valid, durations, coadd_seconds
        )
        in_ref = None  # index-based membership no longer applies

    if R_ref is None:
        R_ref = R[0]
        dR_ref = np.zeros_like(R_ref)  # original behaviour: ignore ref noise
        ref_valid = valid[0]
        ref_note = "first interval"
    else:
        ref_note = "coadded reference"
    if in_ref is None:
        in_ref = np.zeros(len(times), dtype=bool)

    use = valid & ref_valid[None, :]
    var = pair_variance(dR, dR_ref, in_ref, loo=loo)
    z = np.full_like(R, np.nan)
    with np.errstate(divide="ignore", invalid="ignore"):
        np.divide(
            R - R_ref[None, :], np.sqrt(np.abs(var)), out=z, where=use & (var > 0)
        )
    z = np.where(use & (var > 0), z, np.nan)

    fig, ax = plt.subplots(figsize=(10, 6))
    qmesh = _cell_edges(q)
    tmesh = _cell_edges(times)

    vmax = float(np.nanpercentile(np.abs(z), 98))
    if not np.isfinite(vmax) or vmax == 0:
        vmax = 1.0
    pcm = ax.pcolormesh(
        tmesh, qmesh, z.T, cmap="RdBu_r", vmin=-vmax, vmax=vmax, shading="auto"
    )
    ax.set_yscale("log")
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Q (Å$^{-1}$)")
    base = (
        f"standardized residual (R − R$_{{\\rm ref}}$)/σ$_{{\\rm pair}}$  [{ref_note}]"
    )
    ax.set_title(f"Run {label} — {base}" if label else base)
    cbar = fig.colorbar(pcm, ax=ax)
    cbar.set_label("(R − R$_{\\rm ref}$) / σ$_{\\rm pair}$")
    _finish_figure(fig, out_path)


def plot_rq4_heatmap(times, q, R, valid, label, out_path):
    """log10(R * Q^4) vs (time, Q)."""
    rq4 = R * (q[None, :] ** 4)
    with np.errstate(invalid="ignore", divide="ignore"):
        log_rq4 = np.where((rq4 > 0) & valid, np.log10(rq4), np.nan)

    fig, ax = plt.subplots(figsize=(10, 6))
    qmesh = _cell_edges(q)
    tmesh = _cell_edges(times)

    pcm = ax.pcolormesh(tmesh, qmesh, log_rq4.T, cmap="viridis", shading="auto")
    ax.set_yscale("log")
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Q (Å$^{-1}$)")
    title = f"Run {label} — log$_{{10}}$(R·Q$^4$)" if label else "log$_{10}$(R·Q$^4$)"
    ax.set_title(title)
    cbar = fig.colorbar(pcm, ax=ax)
    cbar.set_label("log$_{10}$(R·Q$^4$)")
    _finish_figure(fig, out_path)


def plot_pca(times, types, scores, components, evr, q_c, label, out_path):
    k = scores.shape[1]
    fig, axes = plt.subplots(k + 1, 1, figsize=(10, 3 + 2.2 * k), sharex=False)
    types_arr = np.array(types)

    # One subplot per component for clear time scores
    for j in range(k):
        ax = axes[j]
        ax.plot(times, scores[:, j], "-", color="lightgrey", linewidth=1, zorder=1)
        for itype, color in [("hold", HOLD_COLOR), ("eis", EIS_COLOR)]:
            m = types_arr == itype
            if m.any():
                ax.plot(
                    times[m],
                    scores[m, j],
                    "o",
                    color=color,
                    markersize=4,
                    label=itype if j == 0 else None,
                    zorder=2,
                )
        ax.set_ylabel(f"PC{j + 1} score\n({evr[j] * 100:.1f}%)")
        ax.grid(True, alpha=0.3)
        if j == 0:
            ax.legend(fontsize=8, loc="best")
            ax.set_title(
                f"Run {label} — PCA time scores" if label else "PCA time scores"
            )
        if j == k - 1:
            ax.set_xlabel("Time (s)")

    # Q-mode shapes
    ax = axes[-1]
    for j in range(k):
        ax.plot(q_c, components[j], label=f"PC{j + 1} ({evr[j] * 100:.1f}%)")
    ax.set_xscale("log")
    ax.set_xlabel("Q (Å$^{-1}$)")
    ax.set_ylabel("Component\namplitude (log$_{10}$R)")
    ax.set_title("PCA Q-mode shapes")
    ax.grid(True, alpha=0.3)
    ax.legend(fontsize=8)

    _finish_figure(fig, out_path)
