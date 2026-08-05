"""Running chi-squared, the fractional change delta, and its significance.

Adapted from ``experiments-2025/tnr_chi2.py`` (see ``upstream.toml``); the
numerics are unchanged.

Read these *after* the amplitude. chi-squared is a quadratic statistic, so it
folds the noise variance into its expectation and a longer-counting interval
reads as a larger change even when the physics is identical. Quote delta, not
chi-squared, and never compare chi-squared between interval types.
"""

from __future__ import annotations

import numpy as np

from nr_workbench.tnr.reference import pair_variance


def analyze_chi2(times, types, R, dR, valid):
    """Per-interval chi-squared with paired-difference denominator.

    Returns chi2[t] = mean( (R - R_ref)^2 / (dR^2 + dR_ref^2) ) over valid Q.
    For two independent measurements of the same truth this has expectation 1.
    """
    types_arr = np.array(types)
    chi2 = np.full(len(times), np.nan)
    for itype in np.unique(types_arr):
        idx = np.where(types_arr == itype)[0]
        ref = idx[0]
        for i in idx:
            v = valid[i] & valid[ref]
            if not v.any():
                continue
            denom = dR[i, v] ** 2 + dR[ref, v] ** 2
            chi2[i] = float(np.mean((R[ref, v] - R[i, v]) ** 2 / denom))
    return chi2


def analyze_delta2(times, types, R, dR, valid, clip="mean"):
    """Noise-corrected fractional change metric, comparable across count times.

    delta2[t] = mean( ((R - R_ref)^2 - dR^2 - dR_ref^2) / R_ref^2 )

    The noise subtraction in the numerator removes the statistical floor; the
    R_ref^2 denominator makes the result dimensionless. delta is roughly the
    fractional RMS change of R relative to its reference value.

    ``clip`` controls where the non-negativity constraint is applied:

    ``"mean"`` (default)
        Clip only the final average. Unbiased when nothing has changed.
    ``"element"``
        Clip each bin before averaging, reproducing the original behaviour.
        This
        is **biased**: it discards noise-negative bins while keeping
        noise-positive ones, so for a bin with no real change

            E[max(0, (dR_tot g)^2 - dR_tot^2)] = dR_tot^2 E[max(0, g^2 - 1)]
                                               = 0.48394 dR_tot^2

        (``CLIP_BIAS`` = 2*phi(1) = sqrt(2/(pi*e)), with
        dR_tot^2 = dR^2 + dR_ref^2). The floor therefore scales with the noise
        level, which *manufactures* a split between interval types of different
        counting time -- the very artifact delta^2 is meant to avoid. Prefer the
        ``delta`` output of :func:`analyze_chi2_ref`, which uses the shared
        coadded reference and clips only the mean.
    """
    if clip not in ("mean", "element"):
        raise ValueError("clip must be 'mean' or 'element'")
    types_arr = np.array(types)
    delta2 = np.full(len(times), np.nan)
    for itype in np.unique(types_arr):
        idx = np.where(types_arr == itype)[0]
        ref = idx[0]
        for i in idx:
            v = valid[i] & valid[ref] & (R[ref] != 0)
            if not v.any():
                continue
            num = (R[ref, v] - R[i, v]) ** 2 - dR[i, v] ** 2 - dR[ref, v] ** 2
            if clip == "element":
                num = np.maximum(num, 0.0)
            value = float(np.mean(num / R[ref, v] ** 2))
            delta2[i] = value if clip == "element" else max(0.0, value)
    return delta2


def chi2_quantile(n, z):
    """Wilson-Hilferty quantile of chi2_n / n at ``z`` standard deviations.

        q(n, z) = (1 - 2/(9n) + z sqrt(2/(9n)))^3

    Accurate to <1e-3 for n >= 50, so the expected fluctuation band can be
    drawn without pulling in scipy. At n = 261 this gives a 68% band of
    [0.91259, 1.08741] against the exact [0.91271, 1.08766], and a 95% band of
    [0.83579, 1.17872] against [0.83549, 1.17927].

    ``n`` may be an array (the per-interval valid-point count).
    """
    n = np.asarray(n, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        t = 1.0 - 2.0 / (9.0 * n) + z * np.sqrt(2.0 / (9.0 * n))
    return np.where(n > 0, np.maximum(t, 0.0) ** 3, np.nan)


def analyze_chi2_ref(R, dR, valid, R_ref, dR_ref, ref_valid, in_ref, loo=True):
    """chi^2 against the shared coadded reference, plus derived quantities.

        var_i    = dR_i^2 + s_i dR_ref^2
        chi2_i   = (1/N_i) sum_Q (R_i - R_ref)^2 / var_i
        snr_i    = sqrt(max(0, chi2_i - 1))
        delta_i  = sqrt(max(0, mean_Q( ((R_i-R_ref)^2 - var_i) / R_ref^2 )))
        signif_i = (chi2_i - 1) sqrt(N_i / 2)

    chi2 has expectation 1 when nothing has changed, but its *value* is
    chi2 ~ 1 + Delta^2/var, so it mixes the size of the change with the noise
    level and cannot be compared between interval types of different duration.

    Two different corrections are reported, and the distinction matters:

    ``snr`` = RMS change / RMS noise. This removes the additive noise floor but
        **not** the counting-time dependence, because the denominator is still
        this interval's own noise. Short intervals still read low. It answers
        "how well resolved is the change in this particular measurement?".
    ``delta`` = fractional RMS change of R, with the noise variance *subtracted*
        rather than divided out. This one **is** count-time independent: it
        estimates a property of the sample rather than of the measurement, and
        is the quantity to compare across interval types. It is the
        shared-reference, mean-clipped analogue of :func:`analyze_delta2`.
    ``signif`` says how many sigma the chi2 excess is. This legitimately grows
        with counting time -- longer counting really does buy sensitivity.

    Returns ``(chi2, n_valid, snr, delta, signif)``.
    """
    use = valid & ref_valid[None, :] & (R_ref[None, :] > 0)
    var = pair_variance(dR, dR_ref, in_ref, loo=loo)
    use = use & (var > 0)
    n_valid = use.sum(axis=1)

    resid2 = np.zeros_like(R)
    frac = np.zeros_like(R)
    with np.errstate(divide="ignore", invalid="ignore"):
        np.divide((R - R_ref[None, :]) ** 2, var, out=resid2, where=use)
        np.divide(
            (R - R_ref[None, :]) ** 2 - var, R_ref[None, :] ** 2, out=frac, where=use
        )
    chi2 = np.full(len(R), np.nan)
    delta = np.full(len(R), np.nan)
    ok = n_valid > 0
    chi2[ok] = resid2[ok].sum(axis=1) / n_valid[ok]
    delta[ok] = np.sqrt(np.maximum(frac[ok].sum(axis=1) / n_valid[ok], 0.0))

    snr = np.sqrt(np.maximum(chi2 - 1.0, 0.0))
    signif = (chi2 - 1.0) * np.sqrt(n_valid / 2.0)
    return chi2, n_valid, snr, delta, signif
