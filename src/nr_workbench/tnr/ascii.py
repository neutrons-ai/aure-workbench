"""ASCII table writers.

Adapted from ``experiments-2025/tnr_chi2.py`` (see ``upstream.toml``).

The formats are preserved exactly -- filenames, headers, column order and
number formatting -- so existing notes, scripts and habits keep working.
``tests/test_tnr_characterization.py`` compares the output byte-for-byte
against tables produced by the original tool.
"""

from __future__ import annotations

import numpy as np

from nr_workbench.tnr.constants import Z68, Z95
from nr_workbench.tnr.metrics.amplitude import AmplitudeResult
from nr_workbench.tnr.metrics.chi2 import chi2_quantile
from nr_workbench.tnr.metrics.qbands import QBandResult
from nr_workbench.tnr.metrics.variogram import VariogramResult


def write_chi2_ascii(
    path,
    times,
    chi2,
    n_valid,
    snr,
    delta,
    signif,
    delta2,
    types,
    labels,
    data_dir,
    json_path,
    ref_desc,
):
    """ASCII table for the shared-reference chi^2 analysis.

    The column layout differs from the pre-coadd version of this file, so the
    title line is changed too: an old parser reading by column index will fail
    loudly on the header rather than silently mis-read the data.
    """
    with open(path, "w") as fh:
        fh.write(
            "# tNR chi-squared vs shared coadded reference, with expected spread\n"
        )
        fh.write(
            "# var    = dR^2 + s dR_ref^2, s = -1 inside the reference block else +1\n"
        )
        fh.write(
            "# chi2   = mean((R-R_ref)^2 / var)                      (baseline 1)\n"
        )
        fh.write("# snr    = sqrt(max(0, chi2-1)) = RMS change / RMS noise\n")
        fh.write("#          NOT count-time independent: short intervals read low.\n")
        fh.write("# delta  = sqrt(max(0, mean(((R-R_ref)^2 - var)/R_ref^2)))\n")
        fh.write(
            "#          fractional RMS change; IS count-time independent -- use this\n"
        )
        fh.write("#          one to compare interval types of different duration.\n")
        fh.write("# significance_sigma = (chi2-1) * sqrt(n_q/2)\n")
        fh.write(
            "# q68/q95 = Wilson-Hilferty quantiles of chi2_n/n (expected spread)\n"
        )
        fh.write("# delta2 = legacy per-type-reference noise-corrected metric\n")
        fh.write(f"# reference: {ref_desc}\n")
        fh.write(
            f"# source_dir: {data_dir}\n# json: {json_path}\n# n_points: {len(times)}\n"
        )
        fh.write(
            "# time_s\tchi2\tn_q\tsnr\tdelta\tsignificance_sigma"
            "\tq68_lo\tq68_hi\tq95_lo\tq95_hi\tdelta2\tinterval_type\tlabel\n"
        )
        q68_lo = chi2_quantile(n_valid, -Z68)
        q68_hi = chi2_quantile(n_valid, Z68)
        q95_lo = chi2_quantile(n_valid, -Z95)
        q95_hi = chi2_quantile(n_valid, Z95)
        for i, (t, ity, lab) in enumerate(zip(times, types, labels)):
            fh.write(
                f"{t:.3f}\t{chi2[i]:.8e}\t{int(n_valid[i])}\t{snr[i]:.8e}\t"
                f"{delta[i]:.8e}\t{signif[i]:.6e}\t"
                f"{q68_lo[i]:.6f}\t{q68_hi[i]:.6f}\t"
                f"{q95_lo[i]:.6f}\t{q95_hi[i]:.6f}\t{delta2[i]:.8e}\t{ity}\t{lab}\n"
            )


def write_amplitude_ascii(
    path, times, types, labels, res: AmplitudeResult, data_dir, json_path
):
    ok = res.dR_ref > 0
    rel = np.median(res.dR_ref[ok] / res.R_ref[ok]) if ok.any() else float("nan")
    with open(path, "w") as fh:
        fh.write("# tNR change amplitude along an empirical template\n")
        fh.write(
            "# y     = (R - R_ref)/R_ref,  sigma = sqrt(dR^2 + s dR_ref^2)/R_ref\n"
        )
        fh.write(
            "# a     = sum(w y T)/sum(w T^2),  sigma_a = 1/sqrt(sum(w T^2)),  w = 1/sigma^2\n"
        )
        fh.write(
            "# chi2_res = mean((y - a T)^2 / sigma^2)   (1 => one template suffices)\n"
        )
        fh.write("# a = 0 at the reference block, a = 1 at the late block\n")
        fh.write(f"# reference: {res.desc['ref']}\n")
        fh.write(f"# late_block: {res.desc['late']}\n")
        fh.write(f"# template: {res.desc['template']}\n")
        fh.write(f"# median dR_ref/R_ref: {rel:.4f}\n")
        fh.write(
            f"# source_dir: {data_dir}\n# json: {json_path}\n# n_points: {len(times)}\n"
        )
        fh.write(
            "# time_s\ta\tsigma_a\tsignificance\tchi2_res\tn_q\tinterval_type\tlabel\n"
        )
        for i, (t, ity, lab) in enumerate(zip(times, types, labels)):
            fh.write(
                f"{t:.3f}\t{res.a[i]:.8e}\t{res.sigma_a[i]:.8e}\t"
                f"{res.signif[i]:.4f}\t{res.chi2_res[i]:.6f}\t"
                f"{int(res.n_used[i])}\t{ity}\t{lab}\n"
            )


def write_variogram_ascii(path, res: VariogramResult, data_dir, json_path):
    with open(path, "w") as fh:
        fh.write("# tNR lag variogram (same-type interval pairs, no reference)\n")
        fh.write(
            "# gamma = mean_Q((R_i-R_j)^2 / (dR_i^2 + dR_j^2)) binned by |t_i-t_j|\n"
        )
        fh.write(
            "# E[gamma] = 1 exactly under 'nothing changed'; excess is real change\n"
        )
        fh.write(
            "# excess_amp = sqrt(max(0, gamma-1)) = change / noise on that timescale\n"
        )
        fh.write(
            "# gamma_err is the SEM over pairs; it UNDERSTATES the error because\n"
        )
        fh.write("# pairs share intervals (see n_intervals). gamma_err_theory is the\n")
        fh.write("# pure-noise expectation sqrt(2/N_Q)/sqrt(n_pairs).\n")
        fh.write(f"# source_dir: {data_dir}\n# json: {json_path}\n")
        for itype, d in res.per_type.items():
            fh.write(f"#\n# interval_type: {itype}\n")
            fh.write(
                "# lag_lo_s\tlag_hi_s\tlag_center_s\tgamma\tgamma_err"
                "\tgamma_err_theory\texcess_amp\tn_pairs\tn_intervals\n"
            )
            for i in range(len(d["gamma"])):
                fh.write(
                    f"{d['lag_lo'][i]:.3f}\t{d['lag_hi'][i]:.3f}\t"
                    f"{d['lag_center'][i]:.3f}\t{d['gamma'][i]:.6f}\t"
                    f"{d['gamma_err'][i]:.6f}\t{d['gamma_err_theory'][i]:.6f}\t"
                    f"{d['excess_amp'][i]:.6f}\t{int(d['n_pairs'][i])}\t"
                    f"{int(d['n_intervals'][i])}\n"
                )


def write_qbands_ascii(
    path, times, types, labels, res: QBandResult, data_dir, json_path
):
    B = res.values.shape[1]
    with open(path, "w") as fh:
        fh.write("# tNR fractional change by Q band, vs shared coadded reference\n")
        fh.write("# value = sum(w y)/sum(w),  error = 1/sqrt(sum(w))  over Q in band\n")
        fh.write(
            "# y = (R - R_ref)/R_ref, w = 1/sigma^2 -- dimensionless, so directly\n"
        )
        fh.write("# comparable between interval types of different counting time\n")
        fh.write(f"# reference: {res.ref_desc}\n")
        fh.write("# band_edges: " + " ".join(f"{e:.6g}" for e in res.edges) + "\n")
        fh.write("# band_n_q:   " + " ".join(str(int(n)) for n in res.n_q) + "\n")
        fh.write(
            f"# source_dir: {data_dir}\n# json: {json_path}\n# n_points: {len(times)}\n"
        )
        cols = "\t".join(f"b{b + 1}\tdb{b + 1}" for b in range(B))
        fh.write(f"# time_s\t{cols}\tinterval_type\tlabel\n")
        for i, (t, ity, lab) in enumerate(zip(times, types, labels)):
            row = "\t".join(
                f"{res.values[i, b]:.6e}\t{res.errors[i, b]:.6e}" for b in range(B)
            )
            fh.write(f"{t:.3f}\t{row}\t{ity}\t{lab}\n")


def write_pca_ascii(path, times, types, labels, scores, evr, data_dir, json_path):
    with open(path, "w") as fh:
        fh.write("# tNR PCA time scores\n")
        fh.write(f"# source_dir: {data_dir}\n# json: {json_path}\n")
        fh.write(f"# n_points: {len(times)}  n_components: {scores.shape[1]}\n")
        fh.write(
            "# explained_variance_ratio: " + " ".join(f"{x:.6f}" for x in evr) + "\n"
        )
        cols = "\t".join(f"PC{j + 1}" for j in range(scores.shape[1]))
        fh.write(f"# time_s\t{cols}\tinterval_type\tlabel\n")
        for i, (t, ity, lab) in enumerate(zip(times, types, labels)):
            row = "\t".join(f"{scores[i, j]:.6e}" for j in range(scores.shape[1]))
            fh.write(f"{t:.3f}\t{row}\t{ity}\t{lab}\n")


def write_kl_ascii(path, times, kl, types, labels, data_dir, json_path):
    with open(path, "w") as fh:
        fh.write("# tNR symmetric KL divergence per Q bin (per-type reference)\n")
        fh.write(
            f"# source_dir: {data_dir}\n# json: {json_path}\n# n_points: {len(times)}\n"
        )
        fh.write("# time_s\tsym_kl\tinterval_type\tlabel\n")
        for t, k, ity, lab in zip(times, kl, types, labels):
            fh.write(f"{t:.3f}\t{k:.8e}\t{ity}\t{lab}\n")
