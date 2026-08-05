---
name: tnr-amplitude
description: >
  Read and act on the change amplitude a(t) +- sigma, the primary temporal
  metric for time-resolved reflectometry.
  USE FOR: judging whether and when a tNR run changed, choosing a functional
  form for a time-dependent fit, deciding which parameter the change implies,
  interpreting chi2_res and the template shape.
  DO NOT USE FOR: deciding whether anything changed at all (see tnr-variogram,
  which comes first) or quoting the size of a change (see tnr-chi2).
version: 2
metadata:
  facility: SNS
  instruments: [REF_L, BL-4B]
  techniques: [reflectometry, time-resolved]
  tags: [tnr, amplitude, template, trajectory, gls, time-resolved]
  source:
    repo: mdoucet/experiments-2025
    path: docs/tnr-amplitude.md
    adaptation: >
      Restructured into the v2 anatomy and pointed at `nrw tnr`. The physics,
      the formulas and the interpretation rules are unchanged.
---

# tNR Change Amplitude

## Overview

`a(t) ± σ_a` is the primary metric for how a time-resolved run changed. It is a
generalized-least-squares projection of each interval's fractional residual
onto a single template `T(Q)`:

```
y_i(Q) = (R_i - R_ref) / R_ref            σ_i = sqrt(dR_i² + s_i·dR_ref²) / R_ref
a_i    = Σ w y T / Σ w T²                 σ_a  = 1 / sqrt(Σ w T²)          w = 1/σ²
χ²_res = (1/N) Σ (y - a·T)² / σ²
```

`a = 0` is the reference state, `a = 1` the late-block state.

**Why this and not χ².** `a` is *linear* in the data, so `E[a]` carries no
counting-time dependence — only `σ_a` shrinks as you count longer. That is
exactly the behaviour you want, and it is what makes a 30 s hold directly
comparable with an 85 s eis slice. χ² is quadratic: it folds the noise variance
into its expectation, so the same physical change reads ~2.8× larger in the
longer slices for no physical reason.

The sign `s_i` is `−1` for intervals **inside** the reference block. That is
exact, not a correction: such an interval is correlated with the coadd it
helped build, and `Var(R_i − R_ref) = dR_i² − dR_ref²`. Using `+` biases those
intervals' χ² to ~0.90 — a spurious 10 % offset in precisely the intervals that
define the baseline.

## When to Use

After the variogram, whenever a run has a tNR series. Always before writing a
model with a `series:` block: the trajectory shape picks the constraint form
and the template shape picks the parameter to free.

Not for deciding whether anything changed — that is the variogram's job, and it
needs no reference so it cannot be misconfigured.

## Process

### 1. Compute it

```bash
nrw tnr assess data/tnr/<run>_<binning> --label r<run> --out assessments/r<run>
```

Reads `<label>_amplitude.png`, `<label>_amplitude.txt` and the `amplitude` and
`template` blocks of `<label>_assessment.json`.

### 2. Check χ²_res before reading a(t) at all

Panel 2 of the plot, `amplitude.chi2_res_median` in the JSON.

- **≈ 1** → one template suffices; `a(t)` is a near-lossless summary of the
  whole `(T × N_Q)` matrix. It also confirms the `dR` values are right, since
  χ²_res is what would inflate if they were understated.
- **≫ 1, especially drifting** → the change direction is rotating in Q. One
  template cannot describe it. Go to `tnr-pca-kl` before trusting anything in
  panel 1.

### 3. Read the trajectory shape, and let it pick the constraint form

`amplitude.trajectory` in the JSON:

| trajectory | what it means | constraint form to use |
|---|---|---|
| `flat` | the drift is inside the error bars | none; do not fit a time dependence |
| `monotonic` | steady change | `linear_in_time` |
| `sigmoidal` | induction period, rise, plateau | `logistic` — and `t_half`/`w` become fitted physics |
| `non-monotonic` | reverses direction | no single form; consider two templates |
| `unknown` | too few intervals | say so rather than guessing |

A **plateau** means the process finished *within the run*. State that
explicitly — it is a real result and one the χ² plot cannot support.

### 4. Read the template, and let it pick the parameter to free

`template.implied_change` in the JSON, panel 3 of the plot:

- **Oscillatory**, node spacing ΔQ → a Kiessig-fringe shift, i.e. a
  **thickness** change of order π/ΔQ. Free a thickness.
- **One sign everywhere** → an overall reflectivity change, i.e. an **SLD
  contrast** change. Free an SLD.

### 5. Confirm the counting-time artifact is gone

`hold` and `eis` points should lie on one curve, and their `σ_a` should be in
the ratio `sqrt(t_eis / t_hold)`. If the two types separate, something is
wrong with the reference or the errors — do not proceed.

### 6. Hand off to the model

The `verdict` field states the constraint form and the parameter. Write the
spec from that, and cite the assessment in the model's `description`.

## Rationalizations

| Excuse | Rebuttal |
|---|---|
| "a(t) is noisy, so the error bars must be wrong." | If χ²_res ≈ 1 the per-point errors are right and the excess scatter is real structure — usually a trajectory shape you have not modelled. For run 218389 the scatter about a *cubic* is 1.5σ purely because the truth is sigmoidal. |
| "a went above 1, so something is broken." | Normal. The reference and late blocks are finite samples and the change may continue past the late block; 218389 spans −0.13 to 1.13. |
| "a = 0.5, so the layer is half grown." | The template is empirical. `a` is the amplitude of an observed direction — not a thickness, roughness or SLD. Fit to get parameters. |
| "The eis points sit higher, so the measurement perturbs the sample." | Check `σ_a` first. If the two types lie on one curve within their errors, they agree; only a genuine offset means perturbation. |
| "I'll use `--template pca`, it's more principled." | The two agree on *shape* but not scale — for 218389 they correlate at 0.9993 with `a_pca = 1.78·a_late`. Compare trajectories, never absolute values, and treat a drop in correlation as the interesting signal. |
| "The template is unsmoothed, which is more faithful." | An unsmoothed template absorbs its own noise realisation and biases every amplitude toward it. That is what `--template-smooth` is for. |

## Red Flags

- Reading `a(t)` without having looked at χ²_res.
- Choosing a constraint form that contradicts `amplitude.trajectory`.
- Freeing an SLD when `template.implied_change` says `thickness`, or vice versa.
- Quoting `a` as a physical quantity.
- A late block of only a few intervals: it carries its own noise into the
  template and the normalisation. Widen it with `--late-seconds`.
- Intervals inside the reference block treated as independent of the baseline —
  they are not, even with the exact leave-one-out variance.
- A single template used on a run where the change reverses direction in Q.

## Verification

- [ ] `amplitude.chi2_res_median` is near 1, or you have moved to a
      two-template picture and said so.
- [ ] `hold` and `eis` lie on one curve, with `σ_a` in the expected ratio.
- [ ] Your chosen `constraints.form` matches `amplitude.trajectory`.
- [ ] The parameter you freed matches `template.implied_change`.
- [ ] `scripts/summarize_amplitude.py` reports no failed checks.
- [ ] The assessment is referenced from the model spec or the report.
