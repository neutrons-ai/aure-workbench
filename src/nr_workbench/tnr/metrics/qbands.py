"""Where in Q the change lives.

Adapted from ``experiments-2025/tnr_chi2.py`` (see ``upstream.toml``); the
numerics are unchanged.

Bands moving in opposite directions indicate a fringe shift, i.e. a thickness
change; bands moving together indicate an overall contrast change. That
distinction is what decides which parameter to free in a fit.
"""

from __future__ import annotations

from typing import NamedTuple

import numpy as np

from nr_workbench.tnr.constants import DEFAULT_N_QBANDS
from nr_workbench.tnr.notify import notify
from nr_workbench.tnr.reference import build_reference, fractional_residuals


class QBandResult(NamedTuple):
    edges: np.ndarray  # (B+1,)
    values: np.ndarray  # (T, B)
    errors: np.ndarray  # (T, B)
    n_q: np.ndarray  # (B,)
    R_ref: np.ndarray
    dR_ref: np.ndarray
    ref_desc: str


def analyze_qbands(
    times,
    q,
    R,
    dR,
    valid,
    ref_idx,
    n_bands=DEFAULT_N_QBANDS,
    edges=None,
    loo=True,
    min_ref_snr=0.0,
    ref_desc="",
) -> QBandResult:
    """Weighted mean fractional change against the coadded reference, per Q band.

        value_ib = sum_{Q in b} w y / sum_{Q in b} w
        error_ib = 1 / sqrt(sum_{Q in b} w)

    Being dimensionless, these are directly comparable between interval types of
    different counting time — only the error bars change. The Q resolution is
    what distinguishes a uniform scale change from a fringe shift.
    """
    if edges is None:
        if n_bands < 1:
            raise ValueError("n_bands must be >= 1")
        if q.min() <= 0:
            raise ValueError("log-spaced Q bands require Q > 0")
        edges = np.geomspace(q.min(), q.max(), n_bands + 1)
    else:
        edges = np.sort(np.asarray(edges, dtype=float))
        if len(edges) < 2:
            raise ValueError("need at least two Q band edges")

    R_ref, dR_ref, ref_valid = build_reference(R, dR, valid, ref_idx)
    in_ref = np.zeros(len(times), dtype=bool)
    in_ref[np.asarray(ref_idx, dtype=int)] = True
    y, _, w, use = fractional_residuals(
        R,
        dR,
        valid,
        R_ref,
        dR_ref,
        ref_valid,
        in_ref,
        loo=loo,
        min_ref_snr=min_ref_snr,
    )

    masks, kept = [], []
    for b in range(len(edges) - 1):
        last = b == len(edges) - 2
        m = (q >= edges[b]) & (q <= edges[b + 1] if last else q < edges[b + 1])
        if not m.any():
            notify(f"  qbands: dropping empty band {edges[b]:.4g}-{edges[b + 1]:.4g}")
            continue
        masks.append(m)
        kept.append(b)
    if not masks:
        raise ValueError("no non-empty Q bands")

    B = len(masks)
    values = np.full((len(times), B), np.nan)
    errors = np.full((len(times), B), np.nan)
    for bi, m in enumerate(masks):
        wm = np.where(use[:, m], w[:, m], 0.0)
        wsum = wm.sum(axis=1)
        ok = wsum > 0
        values[ok, bi] = (wm * y[:, m])[ok].sum(axis=1) / wsum[ok]
        errors[ok, bi] = 1.0 / np.sqrt(wsum[ok])

    kept_edges = np.array([edges[b] for b in kept] + [edges[kept[-1] + 1]])
    return QBandResult(
        edges=kept_edges,
        values=values,
        errors=errors,
        n_q=np.array([int(m.sum()) for m in masks]),
        R_ref=R_ref,
        dR_ref=dR_ref,
        ref_desc=ref_desc,
    )
