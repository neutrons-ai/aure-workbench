"""The lag variogram: is anything changing, and on what timescale?

Adapted from ``experiments-2025/tnr_chi2.py`` (see ``upstream.toml``); the
numerics are unchanged.

Read this first. It needs no reference at all -- gamma = 1 is the pure-noise
floor -- so it is the one metric with nothing to misconfigure. If it is flat,
the sample did not change and there is nothing further to compute.
"""

from __future__ import annotations

from typing import NamedTuple

import numpy as np

from nr_workbench.tnr.constants import DEFAULT_N_LAG_BINS
from nr_workbench.tnr.intervals import ordered_types
from nr_workbench.tnr.notify import notify


class VariogramResult(NamedTuple):
    per_type: dict[str, dict]
    lag_edges: np.ndarray


def analyze_variogram(
    times,
    types,
    R,
    dR,
    valid,
    n_bins=DEFAULT_N_LAG_BINS,
    lag_edges=None,
    min_pairs=3,
    log_bins=True,
    durations=None,
) -> VariogramResult:
    """Pairwise chi^2 between same-type intervals versus their time separation.

    For every pair (i, j) of intervals of the same type,

        gamma_ij = mean_Q( (R_i - R_j)^2 / (dR_i^2 + dR_j^2) )

    binned by the lag |t_i - t_j|. Each term is a squared standard normal when
    nothing has changed between i and j, so **E[gamma] = 1 exactly**, with no
    reference, no template and no model. The intercept therefore pins the noise
    floor and any rise with lag is real change on that timescale.

    ``sqrt(max(0, gamma - 1))`` reads as "change / noise" on that timescale.

    Error columns: ``gamma_err`` is the standard error of the mean over pairs
    in the bin, and ``gamma_err_theory`` is the pure-noise expectation
    sqrt(2/N_Q)/sqrt(n_pairs). Both **understate** the truth, because pairs
    share intervals and so are correlated; ``n_intervals`` is reported so the
    reader can judge.
    """
    types_arr = np.array(types)
    centres = times if durations is None else times + durations / 2.0

    if len(times) > 400:
        notify(
            f"Note: variogram is O(T^2) in the number of intervals (T={len(times)}); "
            "this may take a moment."
        )

    all_lags, all_gamma, all_types, all_ij = [], [], [], []
    for itype in ordered_types(types):
        idx = np.where(types_arr == itype)[0]
        if idx.size < 2:
            continue
        for pos, i in enumerate(idx[:-1]):
            j = idx[pos + 1 :]
            v = valid[i][None, :] & valid[j]
            denom = dR[i][None, :] ** 2 + dR[j] ** 2
            term = np.zeros_like(denom)
            np.divide(
                (R[i][None, :] - R[j]) ** 2, denom, out=term, where=v & (denom > 0)
            )
            n = v.sum(axis=1)
            ok = n > 0
            if not ok.any():
                continue
            g = term.sum(axis=1)[ok] / n[ok]
            all_gamma.append(g)
            all_lags.append(np.abs(centres[j][ok] - centres[i]))
            all_types.append(np.repeat(itype, ok.sum()))
            all_ij.append(np.stack([np.repeat(i, ok.sum()), j[ok]], axis=1))

    if not all_gamma:
        raise ValueError("no same-type interval pairs available for the variogram")

    lags = np.concatenate(all_lags)
    gammas = np.concatenate(all_gamma)
    pair_types = np.concatenate(all_types)
    pair_ij = np.concatenate(all_ij, axis=0)
    n_q_mean = float(valid.sum(axis=1).mean())

    if lag_edges is None:
        lag_max = lags.max()
        if log_bins:
            positive = lags[lags > 0]
            lo = max(positive.min(), 1.0) if positive.size else 1.0
            edges = np.geomspace(lo, lag_max * 1.001, n_bins + 1)
            edges[0] = 0.0
        else:
            edges = np.linspace(0.0, lag_max * 1.001, n_bins + 1)
    else:
        edges = np.asarray(lag_edges, dtype=float)

    per_type = {}
    for itype in ordered_types(types):
        m_type = pair_types == itype
        if not m_type.any():
            continue
        rows = []
        dropped = 0
        for b in range(len(edges) - 1):
            lo, hi = edges[b], edges[b + 1]
            m = (
                m_type
                & (lags >= lo)
                & (lags < hi if b < len(edges) - 2 else lags <= hi)
            )
            n_pairs = int(m.sum())
            if n_pairs < min_pairs:
                dropped += n_pairs
                continue
            g = gammas[m]
            rows.append(
                {
                    "lag_lo": lo,
                    "lag_hi": hi,
                    "lag_center": float(lags[m].mean()),
                    "gamma": float(g.mean()),
                    "gamma_err": float(g.std(ddof=1) / np.sqrt(n_pairs))
                    if n_pairs > 1
                    else float("nan"),
                    "gamma_err_theory": float(
                        np.sqrt(2.0 / n_q_mean) / np.sqrt(n_pairs)
                    ),
                    "excess_amp": float(np.sqrt(max(0.0, g.mean() - 1.0))),
                    "n_pairs": n_pairs,
                    "n_intervals": int(len(np.unique(pair_ij[m]))),
                }
            )
        if dropped:
            notify(
                f"  variogram: dropped {dropped} '{itype}' pair(s) in bins with "
                f"fewer than {min_pairs} pairs"
            )
        if rows:
            per_type[itype] = {k: np.array([r[k] for r in rows]) for k in rows[0]}
    if not per_type:
        raise ValueError(
            "no variogram bin had enough pairs; lower --variogram-min-pairs"
        )
    return VariogramResult(per_type=per_type, lag_edges=edges)
