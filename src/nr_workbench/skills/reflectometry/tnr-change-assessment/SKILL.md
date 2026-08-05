---
name: tnr-change-assessment
description: >
  Decide whether, when, and how a time-resolved neutron reflectometry run
  changed, before fitting anything. Walks the diagnostic metrics in the order
  that avoids the classic misreadings.
  USE FOR: assessing a tNR run, deciding whether a change is real, choosing a
  functional form for a time-dependent fit, interpreting amplitude/variogram/
  chi-squared/PCA output.
  DO NOT USE FOR: building or running a fit (see nrw-model-spec), or
  steady-state data with no time axis (see neutron-reflectometry).
version: 2
metadata:
  facility: SNS
  instruments: [REF_L, BL-4B]
  techniques: [reflectometry, time-resolved]
  tags: [tnr, time-resolved, amplitude, variogram, chi-squared, pca, assessment]
---

# tNR Change Assessment

## Overview

A time-resolved run gives you an `R(Q, t)` matrix: typically 100+ intervals of
30–90 s each at a single angle. Before any model is written, two questions must
be answered from the data alone:

1. Did the sample change at all, and on what timescale?
2. Is the change a **thickness** change or a **contrast** change?

The metrics below answer both. Run them in the stated order — it is designed so
that the cheap, assumption-free check comes first and the easily-misread one
comes last.

**The headline result is the amplitude trajectory `a(t) ± σ`.** It is a
weighted linear projection of each interval onto a fixed template, so its
expectation carries no counting-time dependence and only its error bar shrinks
with longer counting. That is what makes intervals of different duration
comparable.

## When to Use

Whenever a sample has a tNR series — `data/tnr/<run>_<binning>/` containing
`r<run>_t<seconds>.txt` slices — and you have not yet assessed it. Always
before writing a model with a `series:` block, because the assessment is what
tells you which functional form the data can support.

Not for steady-state runs, and not as a substitute for fitting: these metrics
are diagnostics, not physics. They tell you *when* and *whether* to fit.

## Process

Run everything at once:

```bash
nrw tnr assess data/tnr/223921_240s --label r223921 --out assessments/r223921
```

Then read the outputs **in this order**. Stopping early is a valid outcome.

### 1. Variogram — is anything changing?

`*_variogram.png`. γ = 1 is the pure-noise floor and needs no reference, which
is why this comes first: it is the one metric with nothing to misconfigure.

**If it is flat, stop.** The sample did not change; there is no trajectory to
fit and no template to build. Record that and move on.

Otherwise note the knee — it is your first estimate of the timescale.

### 2. Amplitude — how does it change?

`*_amplitude.png`, three panels. **Check panel 2 before trusting panel 1.**

- **Panel 2, χ²_res ≈ 1** → a single template describes the change; `a(t)` is a
  near-lossless summary of the whole matrix. Proceed.
- **χ²_res ≫ 1, especially drifting** → the change direction is rotating in Q.
  One template is not enough; go to step 5.
- **Panel 1, `a(t)`** → the trajectory. `a = 0` is the initial state, `a = 1`
  the late-block state. Read its *shape*, which is what picks your constraint
  form: a smooth monotonic rise suggests `linear_in_time`; a flat–steep–flat
  sigmoid suggests `logistic`; a plateau means the process finished inside the
  run, which is a result worth stating explicitly.
- Values slightly outside `[0, 1]` are normal — the reference and late blocks
  are finite samples.

### 3. Q bands and the template — what kind of change?

`*_qbands.png` plus panel 3 of the amplitude plot. This is the step that
decides which parameter to free:

- **Oscillatory template**, node spacing ΔQ → a **thickness** change of order
  π/ΔQ. Free a thickness; a uniform SLD change cannot produce nodes.
- **One sign everywhere** → an overall reflectivity change, i.e. **SLD
  contrast**. Free an SLD.
- **Bands moving oppositely** → fringe shift (thickness). **Together** →
  contrast.

### 4. Running χ² — quote the numbers

`*_chi2.png`. Take the fractional change δ and its significance from here.

**Do not compare raw χ² or SNR between interval types.** χ² is a quadratic
statistic: it folds the noise variance into its expectation, so a longer-
counting interval reads as a larger change even when the physics is identical.
This is precisely why the amplitude metric exists. Quote δ, not χ².

### 5. PCA and KL — only when one template is not enough

`*_pca.png`, `*_kl.png`, `*_residual_heatmap.png`. Reach for these when
χ²_res ≫ 1 in step 2, or when the change appears to reverse direction in Q
partway through. The fix is a second template or a two-component model.

### 6. Hand off to the model

`assessments/<label>/assessment.json` carries the machine-readable verdict —
`amplitude.trajectory`, `template.classification`, `template.implied_change`.
Use those to choose `constraints.form` and which parameter to free, then write
the spec.

## Rationalizations

| Excuse | Rebuttal |
|---|---|
| "χ² is rising, so the sample is clearly changing." | χ² sits at its statistical floor for a typical run: for N_Q = 261 the intrinsic spread is √(2/261) = 0.088, and the observed scatter about a smooth trend is 0.095. Essentially all of the movement is the χ² distribution fluctuating. Look at the variogram. |
| "The EIS intervals show a much bigger change than the holds." | They count longer. Median dR/R was 0.141 for 30 s holds and 0.085 for 85 s EIS slices, so an identical physical change reads ~2.8× larger in EIS. That is an artifact, and the amplitude metric removes it. |
| "a(t) looks noisy, so the error bars must be underestimated." | If χ²_res ≈ 1 the per-point errors are right and the excess scatter is real structure — usually a trajectory shape you have not modelled yet. |
| "I'll skip the assessment and fit a linear ramp; it's the obvious model." | A linear ramp through a sigmoidal trajectory hides both the induction period and the completion, and both are physics. The assessment costs one command. |
| "The template is the change, so a = 0.5 means the layer is half-grown." | The template is empirical. `a` is the amplitude of an observed direction, not a thickness, roughness, or SLD. Fit to get parameters. |

## Red Flags

- Quoting a χ² comparison between `hold` and `eis` intervals.
- Reading `a(t)` without having looked at the χ²_res panel.
- A model with a `series:` block and no assessment in `assessments/`.
- Concluding "no change" from χ² alone when the variogram was never run.
- Choosing a constraint form that contradicts `template.implied_change` — for
  example freeing an SLD when the template oscillates.
- Treating `a = 1` as a physical endpoint rather than "the late block's state".

## Verification

Before writing a model from an assessment:

- [ ] The variogram is not flat — there is a change to model.
- [ ] χ²_res is near 1, or you have explicitly moved to a two-template picture.
- [ ] The parameter you intend to free matches `template.implied_change`.
- [ ] Your chosen `constraints.form` matches the shape of `a(t)`.
- [ ] Hold and EIS intervals lie on one curve — if not, the measurement is
      perturbing the sample and that needs saying before any fit is trusted.
- [ ] `assessments/<label>/assessment.json` exists and is referenced by the
      report or the spec's `description`.
