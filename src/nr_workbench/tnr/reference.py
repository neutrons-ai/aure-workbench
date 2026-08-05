"""Building the shared reference and the fractional residuals against it.

Adapted from ``experiments-2025/tnr_chi2.py`` (see ``upstream.toml``); the
numerics are unchanged.

Every metric compares against one inverse-variance coadd of a block of
intervals, not against the first interval of each type. Coadding drops the
reference's own noise to the point where it contributes essentially nothing,
and a single shared reference is what puts interval types of different
counting time on a common baseline.
"""

from __future__ import annotations

import fnmatch
import math
from collections.abc import Sequence

import numpy as np

from nr_workbench.tnr.notify import notify


def build_reference(
    R: np.ndarray, dR: np.ndarray, valid: np.ndarray, idx: Sequence[int]
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Inverse-variance coadd of the intervals in ``idx``.

    Per Q point, over the selected intervals j:

        w_j    = valid_j / dR_j^2
        W      = sum_j w_j
        R_ref  = sum_j w_j R_j / W
        dR_ref = 1 / sqrt(W)

    Returns ``(R_ref, dR_ref, ref_valid)`` on the full Q grid, with zeros
    (never NaN) where nothing contributed and ``ref_valid = W > 0``.
    """
    idx = np.asarray(idx, dtype=int)
    if idx.size == 0:
        raise ValueError("cannot build a reference from an empty interval set")

    sel_R = R[idx]
    sel_dR = dR[idx]
    ok = valid[idx] & (sel_dR > 0)

    w = np.zeros_like(sel_dR)
    np.divide(1.0, sel_dR**2, out=w, where=ok)
    W = w.sum(axis=0)
    ref_valid = W > 0

    R_ref = np.zeros(R.shape[1])
    np.divide((w * sel_R).sum(axis=0), W, out=R_ref, where=ref_valid)
    dR_ref = np.zeros(R.shape[1])
    np.divide(
        1.0,
        np.sqrt(W, where=ref_valid, out=np.zeros_like(W)),
        out=dR_ref,
        where=ref_valid,
    )
    return R_ref, dR_ref, ref_valid


def resolve_interval_set(
    times: np.ndarray,
    types: Sequence[str],
    labels: Sequence[str],
    seconds: float | None = None,
    label_spec: str | None = None,
    mode: str = "leading",
    match_type: str | None = None,
    durations: np.ndarray | None = None,
) -> tuple[np.ndarray, str]:
    """Resolve a reference or late-block interval set.

    Priority: ``label_spec`` > ``seconds`` > contiguous-block default.

    label_spec
        Comma-separated interval labels or ``fnmatch`` globs, e.g.
        ``hold_initial_*``.
    seconds
        ``mode="leading"``  → intervals starting within ``seconds`` of the
        first; ``mode="trailing"`` → within ``seconds`` of the last. Restricted
        to ``match_type`` when given.
    default
        ``mode="leading"``  → the leading contiguous run of ``types[0]``;
        ``mode="trailing"`` → the trailing contiguous run of ``match_type``
        (defaulting to the leading block's type).

    Returns ``(idx, description)``; the description is echoed to the console and
    embedded in every ASCII header.
    """
    types_arr = np.array(types)
    T = len(times)

    if label_spec:
        patterns = [p.strip() for p in label_spec.split(",") if p.strip()]
        keep = [
            i
            for i, lab in enumerate(labels)
            if any(fnmatch.fnmatch(lab, p) for p in patterns)
        ]
        if not keep:
            raise ValueError(f"no interval labels matched {label_spec!r}")
        idx = np.array(keep, dtype=int)
        how = f"labels matching {label_spec!r}"
    elif seconds is not None:
        if seconds <= 0:
            raise ValueError("--ref-seconds/--late-seconds must be positive")
        if mode == "leading":
            m = times <= times[0] + seconds
        else:
            m = times >= times[-1] - seconds
        if match_type is not None:
            m &= types_arr == match_type
        idx = np.where(m)[0]
        if idx.size == 0:
            raise ValueError(f"no intervals within {seconds} s ({mode})")
        how = f"{mode} {seconds:g} s"
    else:
        fallback_n = min(max(3, math.ceil(0.1 * T)), T)
        if mode == "leading":
            itype = str(types_arr[0])
            n = 1
            while n < T and types_arr[n] == itype:
                n += 1
            idx = np.arange(n)
            how = f"leading contiguous {itype!r} block"
            if n == T:
                # Single-type run: the whole run as reference would drive every
                # amplitude to ~0. Use a leading fraction instead.
                idx = np.arange(fallback_n)
                how = f"leading {idx.size} intervals (single-type run)"
                notify(
                    "Warning: only one interval_type present; using the first "
                    f"{idx.size} intervals as the reference block."
                )
            elif n < 2:
                # The run opens with a lone interval of its type (e.g. an OCV
                # PEIS scan before the first hold). Ignore the type boundary.
                idx = np.arange(fallback_n)
                how = f"leading {idx.size} intervals (mixed type)"
                notify(
                    f"Warning: the run starts with a single {itype!r} interval; "
                    f"using the first {idx.size} intervals (mixed type) as the "
                    "reference block."
                )
        else:
            itype = str(match_type if match_type is not None else types_arr[0])
            hits = np.where(types_arr == itype)[0]
            if hits.size == 0:
                raise ValueError(
                    f"no intervals of type {itype!r} for the trailing block"
                )
            # Walk back from the last interval of this type while contiguous.
            end = hits[-1]
            start = end
            while start - 1 >= 0 and types_arr[start - 1] == itype:
                start -= 1
            idx = np.arange(start, end + 1)
            how = f"trailing contiguous {itype!r} block"
            if idx.size < 2:
                idx = np.arange(T - fallback_n, T)
                how = f"trailing {idx.size} intervals (mixed type)"
                notify(
                    f"Warning: the trailing {itype!r} block has a single member; "
                    f"using the last {idx.size} intervals (mixed type) as the "
                    "late block."
                )

    if idx.size < 2:
        raise ValueError(
            f"resolved {mode} interval set has {idx.size} member(s) "
            f"({how}); at least 2 are needed for a meaningful coadd "
            "(the leave-one-out variance is undefined for a single member). "
            "Widen the selection with --ref-seconds/--ref-labels."
        )

    span_end = times[idx[-1]]
    if durations is not None:
        span_end += durations[idx[-1]]
    counting = float(durations[idx].sum()) if durations is not None else float("nan")
    desc = (
        f"{how}: {idx.size} intervals, {labels[idx[0]]}..{labels[idx[-1]]}, "
        f"t={times[idx[0]]:.1f}-{span_end:.1f} s"
    )
    if durations is not None:
        desc += f", {counting:.0f} s counting"
    return idx, desc


def pair_variance(
    dR: np.ndarray,
    dR_ref: np.ndarray,
    in_ref: np.ndarray,
    loo: bool = True,
    floor_frac: float = 1e-3,
) -> np.ndarray:
    """Var(R_i - R_ref) on the full (T, Nq) grid.

        sigma^2 = dR_i^2 + s_i * dR_ref^2,   s_i = -1 if i is in the reference
                                                   set else +1

    The minus sign for reference members is *exact*, not an approximation. With
    R_ref the inverse-variance coadd and w_i = 1/dR_i^2, W = sum_j w_j:

        Var(R_i - R_ref) = dR_i^2 (1 - w_i/W)^2 + sum_{j!=i} w_j^2 dR_j^2 / W^2
                         = dR_i^2 - 1/W
                         = dR_i^2 - dR_ref^2

    Using ``+`` for reference members instead biases their chi^2 low by ~10%,
    i.e. it puts a spurious offset in exactly the intervals that define the
    baseline. ``loo=False`` restores the naive (conservative) ``+`` everywhere.

    The result is floored at ``(floor_frac * dR_i)^2`` so a degenerate
    single-member reference cannot produce a zero or negative variance.
    """
    sign = np.where(in_ref[:, None], -1.0, 1.0) if loo else 1.0
    var = dR**2 + sign * dR_ref[None, :] ** 2
    floor = (floor_frac * dR) ** 2
    clipped = var < floor
    if clipped.any():
        n_int = int(clipped.any(axis=1).sum())
        notify(
            f"Warning: paired variance floored at {clipped.sum()} Q point(s) "
            f"across {n_int} interval(s); the reference block may be too small."
        )
        var = np.maximum(var, floor)
    return var


def fractional_residuals(
    R: np.ndarray,
    dR: np.ndarray,
    valid: np.ndarray,
    R_ref: np.ndarray,
    dR_ref: np.ndarray,
    ref_valid: np.ndarray,
    in_ref: np.ndarray,
    loo: bool = True,
    min_ref_snr: float = 0.0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Fractional residuals against the coadded reference.

        y     = (R - R_ref) / R_ref
        sigma = sqrt(dR^2 + s dR_ref^2) / R_ref
        w     = 1 / sigma^2

    Returns ``(y, sigma, w, use)``, all (T, Nq), zero-filled outside ``use``.

    This is the single place the divide-by-zero and negative-reflectivity
    guards live. Note that ``valid`` from :func:`load_run` only tests
    ``dR > 0`` — reduced data can and does contain negative R at the highest Q
    points, so ``R_ref > 0`` is enforced here. ``min_ref_snr > 0`` additionally
    drops reference bins that are not that many sigma above zero.
    """
    use = valid & ref_valid[None, :] & (R_ref[None, :] > 0)
    if min_ref_snr > 0:
        use = use & (R_ref[None, :] > min_ref_snr * dR_ref[None, :])

    var = pair_variance(dR, dR_ref, in_ref, loo=loo)
    y = np.zeros_like(R)
    sigma = np.zeros_like(R)
    with np.errstate(divide="ignore", invalid="ignore"):
        np.divide(R - R_ref[None, :], R_ref[None, :], out=y, where=use)
        np.divide(np.sqrt(np.abs(var)), R_ref[None, :], out=sigma, where=use)
    use = use & (sigma > 0)
    w = np.zeros_like(R)
    np.divide(1.0, sigma**2, out=w, where=use)
    return np.where(use, y, 0.0), np.where(use, sigma, 0.0), w, use


def coadd_in_time(
    times: np.ndarray,
    types: Sequence[str],
    labels: Sequence[str],
    R: np.ndarray,
    dR: np.ndarray,
    valid: np.ndarray,
    durations: np.ndarray,
    seconds: float,
):
    """Group consecutive intervals into blocks of >= ``seconds`` counting time.

    Each block is coadded with :func:`build_reference`, so every output row has
    comparable counting statistics. Block time is the duration-weighted centre;
    a block spanning more than one interval_type is labelled ``mixed``.

    Returns ``(times_c, types_c, labels_c, R_c, dR_c, valid_c, durations_c)``.
    """
    if seconds <= 0:
        raise ValueError("coadd seconds must be positive")

    groups: list[list[int]] = []
    current: list[int] = []
    acc = 0.0
    for i in range(len(times)):
        current.append(i)
        acc += durations[i]
        if acc >= seconds:
            groups.append(current)
            current = []
            acc = 0.0
    if current:
        # Fold a short trailing remainder into the previous group.
        if groups:
            groups[-1].extend(current)
        else:
            groups.append(current)

    times_c, types_c, labels_c, dur_c = [], [], [], []
    R_rows, dR_rows, valid_rows = [], [], []
    for g in groups:
        gi = np.array(g, dtype=int)
        R_g, dR_g, valid_g = build_reference(R, dR, valid, gi)
        centres = times[gi] + durations[gi] / 2.0
        weights = durations[gi]
        times_c.append(float(np.average(centres, weights=weights)))
        dur_c.append(float(durations[gi].sum()))
        g_types = {types[i] for i in g}
        types_c.append(g_types.pop() if len(g_types) == 1 else "mixed")
        labels_c.append(labels[g[0]] if len(g) == 1 else f"{labels[g[0]]}+{len(g) - 1}")
        R_rows.append(R_g)
        dR_rows.append(dR_g)
        valid_rows.append(valid_g)

    return (
        np.array(times_c),
        types_c,
        labels_c,
        np.vstack(R_rows),
        np.vstack(dR_rows),
        np.vstack(valid_rows),
        np.array(dur_c),
    )
