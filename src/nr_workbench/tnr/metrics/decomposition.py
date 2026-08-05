"""PCA and symmetric KL divergence -- the follow-ups when one template is not enough.

Adapted from ``experiments-2025/tnr_chi2.py`` (see ``upstream.toml``); the
numerics are unchanged.

Reach for these when the amplitude's chi2_res is well above 1, which means the
change direction is rotating in Q and a single template cannot describe it.
"""

from __future__ import annotations

import numpy as np


def analyze_pca(times, q, R, valid, n_components=3):
    """SVD-based PCA on log10(R), restricted to Q points common to all intervals.

    Operating on log10(R) prevents the low-Q (large R) region from dominating.

    Returns (scores [T, k], components [k, Nq_common], explained_variance_ratio,
    q_common, mean_curve [Nq_common]).
    """
    common = np.all(valid, axis=0) & np.all(R > 0, axis=0)
    if common.sum() < 3:
        raise ValueError("Too few Q points common to all intervals for PCA.")
    q_c = q[common]
    M = np.log10(R[:, common])
    mean = M.mean(axis=0)
    Mc = M - mean
    U, S, Vt = np.linalg.svd(Mc, full_matrices=False)
    k = min(n_components, len(S))
    scores = U[:, :k] * S[:k]
    components = Vt[:k]
    var = (S**2) / max(len(times) - 1, 1)
    evr = var[:k] / var.sum()
    return scores, components, evr, q_c, mean


def analyze_kl(times, types, R, dR, valid):
    """Symmetric KL between Gaussian product distributions, per-type reference.

    For two Gaussians N(μ1, σ1), N(μ2, σ2):
        KL(1||2) = log(σ2/σ1) + (σ1^2 + (μ1-μ2)^2)/(2 σ2^2) - 1/2
    Symmetric KL summed over Q bins, divided by N to give per-bin value.
    """
    types_arr = np.array(types)
    kl = np.full(len(times), np.nan)
    for itype in np.unique(types_arr):
        idx = np.where(types_arr == itype)[0]
        ref = idx[0]
        mu_r = R[ref]
        sig_r = dR[ref]
        for i in idx:
            v = valid[i] & valid[ref] & (sig_r > 0) & (dR[i] > 0)
            if not v.any():
                continue
            mu_a, sig_a = R[i, v], dR[i, v]
            mu_b, sig_b = mu_r[v], sig_r[v]
            kl_ab = (
                np.log(sig_b / sig_a)
                + (sig_a**2 + (mu_a - mu_b) ** 2) / (2 * sig_b**2)
                - 0.5
            )
            kl_ba = (
                np.log(sig_a / sig_b)
                + (sig_b**2 + (mu_b - mu_a) ** 2) / (2 * sig_a**2)
                - 0.5
            )
            kl[i] = float(np.mean(0.5 * (kl_ab + kl_ba)))
    return kl
