"""Loading a time-resolved run from reduced ASCII slices.

Adapted from ``experiments-2025/tnr_chi2.py`` (see ``upstream.toml``); the
numerics are unchanged.

A run is a directory of ``r<run>_<label>.txt`` files plus an
``r<run>_eis_reduction.json`` sidecar that carries the interval ordering,
types and timestamps. The sidecar is authoritative for order -- filenames
sort lexically, which puts ``t001000`` before ``t000240``.
"""

from __future__ import annotations

import json
import os
from datetime import datetime

import numpy as np

from nr_workbench.tnr.notify import notify


def load_tnr_data(filepath: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Load (Q, R, dR) from a tNR ASCII file."""
    data = np.loadtxt(filepath, usecols=(0, 1, 2))
    return data[:, 0], data[:, 1], data[:, 2]


def align_to_ref(
    q_ref: np.ndarray, q: np.ndarray, r: np.ndarray, dr: np.ndarray, rtol: float = 1e-4
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Align (q, r, dr) onto q_ref by nearest match within rtol relative tol.

    Returns (r_aligned, dr_aligned, mask) on the q_ref grid.
    """
    if len(q) == len(q_ref) and np.allclose(q, q_ref):
        return r, dr, np.ones(len(q_ref), dtype=bool)

    idx = np.searchsorted(q, q_ref)
    idx_left = np.clip(idx - 1, 0, len(q) - 1)
    idx_right = np.clip(idx, 0, len(q) - 1)
    diff_left = np.abs(q[idx_left] - q_ref)
    diff_right = np.abs(q[idx_right] - q_ref)
    use_left = diff_left < diff_right
    nearest = np.where(use_left, idx_left, idx_right)
    mindiff = np.minimum(diff_left, diff_right)
    mask = mindiff <= rtol * np.maximum(np.abs(q_ref), 1e-12)
    return r[nearest], dr[nearest], mask


def find_reduction_json(data_dir: str) -> str:
    candidates = [f for f in os.listdir(data_dir) if f.endswith("_eis_reduction.json")]
    if not candidates:
        raise FileNotFoundError(f"No *_eis_reduction.json file found in {data_dir}")
    if len(candidates) > 1:
        raise ValueError(
            f"Multiple reduction JSON files found in {data_dir}: {candidates}. "
            "Specify one explicitly with --json."
        )
    return os.path.join(data_dir, candidates[0])


def load_run(data_dir: str, json_path: str, min_duration: float = 0.0):
    """Load all qualifying intervals onto a common Q grid.

    Returns
    -------
    times : (T,) float array — seconds since first interval's start
    types : list[str] of length T
    labels : list[str] of length T
    q : (Nq,) float array — reference Q grid (from first qualifying interval)
    R, dR : (T, Nq) arrays — aligned reflectivity and uncertainty
    valid : (T, Nq) bool array — True where a real measurement exists
    durations : (T,) float array — interval duration in seconds

    ``times`` are interval *start* times. Use ``times + durations / 2`` where
    the interval centre matters (variogram lags, time coadding), since the eis
    slices are ~3x longer than the holds.
    """
    with open(json_path) as fh:
        meta = json.load(fh)
    run_number = meta["run_number"]
    intervals = meta["intervals"]
    if not intervals:
        raise ValueError(f"No intervals listed in {json_path}")

    t0 = datetime.fromisoformat(intervals[0]["start"])

    times: list[float] = []
    types: list[str] = []
    labels: list[str] = []
    durations: list[float] = []
    R_rows: list[np.ndarray] = []
    dR_rows: list[np.ndarray] = []
    valid_rows: list[np.ndarray] = []

    q_ref = None
    missing: list[str] = []
    n_short = 0

    for interval in intervals:
        label = interval["label"]
        itype = interval.get("interval_type", "unknown")
        start = datetime.fromisoformat(interval["start"])
        end = datetime.fromisoformat(interval["end"])
        duration = (end - start).total_seconds()
        if duration < min_duration:
            n_short += 1
            continue

        filename = f"r{run_number}_{label}.txt"
        filepath = os.path.join(data_dir, filename)
        if not os.path.isfile(filepath):
            missing.append(filename)
            continue

        q, r, dr = load_tnr_data(filepath)
        if q_ref is None:
            q_ref = q
            r_a, dr_a, mask = r.copy(), dr.copy(), np.ones(len(q_ref), dtype=bool)
        else:
            r_a, dr_a, mask = align_to_ref(q_ref, q, r, dr)
            if not mask.any():
                raise ValueError(
                    f"No overlapping Q points with reference for: {filepath}"
                )
            # Zero out invalid entries; valid mask records them.
            r_a = np.where(mask, r_a, 0.0)
            dr_a = np.where(mask, dr_a, 0.0)

        times.append((start - t0).total_seconds())
        types.append(itype)
        labels.append(label)
        durations.append(duration)
        R_rows.append(r_a)
        dR_rows.append(dr_a)
        valid_rows.append(mask & (dr_a > 0) if mask.any() else mask)

    if n_short:
        notify(f"Skipped {n_short} interval(s) with duration < {min_duration} s")
    if missing:
        notify(
            f"Warning: {len(missing)} interval file(s) listed in JSON not found "
            f"(first: {missing[0]})"
        )
    if not times:
        raise FileNotFoundError(
            f"None of the interval files referenced by {json_path} were found in {data_dir}"
        )

    return (
        np.array(times),
        types,
        labels,
        q_ref,
        np.vstack(R_rows),
        np.vstack(dR_rows),
        np.vstack(valid_rows),
        np.array(durations),
    )
