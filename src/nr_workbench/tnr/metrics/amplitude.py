"""The change amplitude a(t) +- sigma -- the primary temporal metric.

Adapted from ``experiments-2025/tnr_chi2.py`` (see ``upstream.toml``); the
numerics are unchanged.

A generalized-least-squares projection of each interval's fractional residual
onto a fixed template. Because it is *linear* in the data, its expectation
carries no counting-time dependence -- only the error bar shrinks with longer
counting. That is what makes a 30 s hold directly comparable with an 85 s eis
slice, which a chi-squared cannot be.
"""

from __future__ import annotations

from typing import NamedTuple

import numpy as np

from nr_workbench.tnr.constants import DEFAULT_TEMPLATE_SMOOTH
from nr_workbench.tnr.reference import build_reference, fractional_residuals


class AmplitudeResult(NamedTuple):
    a: np.ndarray  # (T,) amplitude along the change template
    sigma_a: np.ndarray  # (T,) its 1-sigma uncertainty
    signif: np.ndarray  # (T,) a / sigma_a
    chi2_res: np.ndarray  # (T,) residual chi2 of the one-template fit
    n_used: np.ndarray  # (T,) Q points contributing
    template: np.ndarray  # (Nq,) the change template, in fractional units
    t_valid: np.ndarray  # (Nq,) bool
    R_ref: np.ndarray
    dR_ref: np.ndarray
    ref_idx: np.ndarray
    late_idx: np.ndarray
    desc: dict[str, str]


def boxcar_smooth(x: np.ndarray, width: int) -> np.ndarray:
    """Odd-width boxcar smoothing with edge padding; length is preserved."""
    x = np.asarray(x, dtype=float)
    if width is None or width <= 1:
        return x.copy()
    width = int(width)
    if width % 2 == 0:
        width += 1
    if width >= len(x):
        return np.full_like(x, x.mean())
    half = width // 2
    padded = np.pad(x, (half, half), mode="edge")
    return np.convolve(padded, np.ones(width) / width, mode="valid")


def build_template(
    q,
    y_late,
    w_late,
    use_late,
    smooth=DEFAULT_TEMPLATE_SMOOTH,
    source="late",
    pca_component=None,
    pca_mask=None,
):
    """Build the change template T(Q), in fractional (dR/R) units.

    ``source="late"``
        T_raw = boxcar_smooth((R_late - R_ref)/R_ref). Smoothing matters: an
        unsmoothed template soaks up its own noise realisation, which biases
        every amplitude toward it.
    ``source="pca"``
        T_raw = ln(10) * PC1(Q) from :func:`analyze_pca`, the ln(10) converting
        its d(log10 R) units to fractional dR/R. Sign-flipped to agree with the
        late block.

    T is normalised so that projecting the late coadd with its own weights gives
    a = 1, and the reference block gives a ~ 0 by construction. Individual late
    intervals land near but not exactly at 1, since each carries its own weight
    vector.

    Returns ``(T, t_valid, norm_c, source_desc)``.
    """
    if source == "late":
        t_valid = use_late.copy()
        raw = boxcar_smooth(np.where(t_valid, y_late, 0.0), smooth)
        source_desc = f"late-minus-reference, boxcar-smoothed over {smooth} Q points"
    elif source == "pca":
        if pca_component is None or pca_mask is None:
            raise ValueError("template source 'pca' requires a PCA component")
        raw = np.zeros(len(q))
        raw[pca_mask] = np.log(10.0) * pca_component
        t_valid = use_late & pca_mask
        if np.sum(w_late * y_late * raw, where=t_valid) < 0:
            raw = -raw
        source_desc = "PC1 of log10 R, converted to fractional units"
    else:
        raise ValueError("template source must be 'late' or 'pca'")

    t_valid = t_valid & np.isfinite(raw)
    denom = float(np.sum(w_late * raw**2, where=t_valid))
    if denom <= 0:
        raise ValueError("template is degenerate (no weight on any Q point)")
    norm_c = float(np.sum(w_late * y_late * raw, where=t_valid)) / denom
    if abs(norm_c) < 1e-12:
        raise ValueError(
            "template is degenerate (late block indistinguishable from reference)"
        )
    return np.where(t_valid, raw / norm_c, 0.0), t_valid, norm_c, source_desc


def analyze_amplitude(
    times,
    q,
    R,
    dR,
    valid,
    ref_idx,
    late_idx,
    template="late",
    smooth=DEFAULT_TEMPLATE_SMOOTH,
    loo=True,
    min_ref_snr=0.0,
    pca_component=None,
    pca_mask=None,
    ref_desc="",
    late_desc="",
) -> AmplitudeResult:
    """Project each interval onto an empirical change template.

    With y = (R - R_ref)/R_ref, sigma its uncertainty and w = 1/sigma^2, the
    generalized-least-squares amplitude of the template T is

        a_i        = sum_Q w y T / sum_Q w T^2
        sigma_a,i  = 1 / sqrt(sum_Q w T^2)
        chi2_res,i = (1/N_i) sum_Q (y - a_i T)^2 / sigma^2

    a = 0 means indistinguishable from the reference state, a = 1 means the
    late-block state has been reached.

    The key property is that ``a`` is **linear** in the data: its expectation
    does not depend on how long the interval was counted, only sigma_a does.
    That is what makes 30 s and 85 s intervals directly comparable, unlike
    chi^2, which is quadratic and so mixes the size of the change with the
    noise level.

    ``chi2_res`` is the goodness-of-one-coordinate diagnostic. Near 1 means a
    single template describes the whole (T x Nq) matrix and ``a`` is a
    near-lossless summary; well above 1 means a second component is needed and
    the PCA output is worth a look.
    """
    R_ref, dR_ref, ref_valid = build_reference(R, dR, valid, ref_idx)
    R_late, dR_late, late_valid = build_reference(R, dR, valid, late_idx)

    in_ref = np.zeros(len(times), dtype=bool)
    in_ref[np.asarray(ref_idx, dtype=int)] = True
    y, sigma, w, use = fractional_residuals(
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

    # Late-block residual and weight, used only to define and normalise T.
    use_late = late_valid & ref_valid & (R_ref > 0)
    y_late = np.zeros(len(q))
    var_late = dR_late**2 + dR_ref**2
    w_late = np.zeros(len(q))
    with np.errstate(divide="ignore", invalid="ignore"):
        np.divide(R_late - R_ref, R_ref, out=y_late, where=use_late)
        np.divide(R_ref**2, var_late, out=w_late, where=use_late & (var_late > 0))

    T, t_valid, _, template_desc = build_template(
        q,
        y_late,
        w_late,
        use_late,
        smooth=smooth,
        source=template,
        pca_component=pca_component,
        pca_mask=pca_mask,
    )

    m = use & t_valid[None, :]
    n_used = m.sum(axis=1)
    Tsq = np.where(m, w * T[None, :] ** 2, 0.0).sum(axis=1)
    num = np.where(m, w * y * T[None, :], 0.0).sum(axis=1)

    a = np.full(len(times), np.nan)
    sigma_a = np.full(len(times), np.nan)
    chi2_res = np.full(len(times), np.nan)
    ok = (n_used > 0) & (Tsq > 0)
    a[ok] = num[ok] / Tsq[ok]
    sigma_a[ok] = 1.0 / np.sqrt(Tsq[ok])

    resid = y - a[:, None] * T[None, :]
    resid2 = np.zeros_like(y)
    np.divide(resid**2, sigma**2, out=resid2, where=m & (sigma > 0))
    chi2_res[ok] = resid2[ok].sum(axis=1) / n_used[ok]

    with np.errstate(divide="ignore", invalid="ignore"):
        signif = a / sigma_a

    return AmplitudeResult(
        a=a,
        sigma_a=sigma_a,
        signif=signif,
        chi2_res=chi2_res,
        n_used=n_used,
        template=T,
        t_valid=t_valid,
        R_ref=R_ref,
        dR_ref=dR_ref,
        ref_idx=np.asarray(ref_idx, dtype=int),
        late_idx=np.asarray(late_idx, dtype=int),
        desc={"ref": ref_desc, "late": late_desc, "template": template_desc},
    )
