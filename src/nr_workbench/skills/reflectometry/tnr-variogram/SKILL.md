---
name: tnr-variogram
description: >
  Establish whether a time-resolved run changed at all, and on what timescale,
  without choosing a reference state.
  USE FOR: the first question about any tNR run, deciding whether an analysis
  is worth continuing, estimating the timescale of a change, cross-checking a
  suspicious amplitude result.
  DO NOT USE FOR: how the sample changed (see tnr-amplitude) or where in Q the
  change lives (see tnr-chi2 for the Q bands).
version: 2
metadata:
  facility: SNS
  instruments: [REF_L, BL-4B]
  techniques: [reflectometry, time-resolved]
  tags: [tnr, variogram, lag, noise-floor, time-resolved]
  source:
    repo: mdoucet/experiments-2025
    path: docs/tnr-variogram.md
    adaptation: Restructured into the v2 anatomy and pointed at `nrw tnr`.
---

# tNR Lag Variogram

## Overview

Every other change metric requires picking a reference state, and that choice
is never innocent: a single noisy interval stamps its own fluctuations across
the series, and even a coadded block fixes "unchanged" to whatever the start of
the run happened to look like.

The variogram needs **no reference at all**. For every pair `(i, j)` of
intervals of the same type,

```
γ_ij = (1/N_ij) Σ_Q (R_i − R_j)² / (dR_i² + dR_j²)
```

If nothing changed between them, each term is a squared standard normal, so
`E[γ] = 1` **exactly** — no model, no template, no fitted parameter. That makes
it the cleanest available evidence that a sample is genuinely evolving rather
than being observed through noise, and it is why it comes first.

It also answers a question no other metric can: *on what timescale* the change
becomes detectable.

Pairs are binned by lag using interval **centre** times — start times would
bias every lag involving an 85 s eis slice by up to ~40 s.

## When to Use

First, on any tNR run, before any other metric. Also whenever an amplitude
result looks surprising: the variogram is the independent check, because it
shares none of the amplitude's assumptions.

Not for characterising the change once its existence is established.

## Process

### 1. Compute it

```bash
nrw tnr variogram data/tnr/<run>_<binning> --label r<run>
```

or read the `variogram` block of an existing `assessment.json`.

### 2. Ask whether γ rises above the noise floor — significantly

`γ = 1` is the floor. The question is not "is γ big?" but "is γ − 1 large
compared with its own error?" The table reports two error columns:

- `gamma_err` — the standard error over pairs. It **understates** the true
  error because pairs share intervals.
- `gamma_err_theory` — the pure-noise expectation `sqrt(2/N_Q)/sqrt(n_pairs)`.
  Use this one.

`nrw` reports `max_sigma_above_noise` per interval type and calls the run
changing when any lag bin sits more than 3σ above 1. A real run can sit at
γ = 1.16 with an error of 0.03 — a 5σ rise that any fixed threshold near 1.3
would wrongly dismiss.

### 3. If it is flat, stop

A flat variogram means the sample did not change detectably. Record that and
move on: there is no trajectory to fit and no template to build. This is a
result, not a failure.

### 4. Read the timescale off the rise

The lag at which γ first departs from 1 is when the change becomes detectable
(`knee_s`). `sqrt(max(0, γ − 1))` is the RMS change divided by the RMS noise on
that timescale, with the noise floor subtracted rather than left in.

### 5. Cross-check against the amplitude

If the variogram is flat but the amplitude is significant, the two disagree and
**neither is settled**. The usual cause is a reference block chosen over a
period when the sample was already moving, which suppresses the amplitude's
baseline while leaving pairwise differences small. `nrw` reports this as an
explicit `ambiguous` verdict rather than picking a side.

## Rationalizations

| Excuse | Rebuttal |
|---|---|
| "γ = 1.05, that is basically 1." | Compare it with `gamma_err_theory`. With a few hundred pairs the error is ~0.007, so 1.05 is a 7σ rise. Significance, not magnitude. |
| "The amplitude already says it changed, so I can skip this." | The amplitude depends on a reference you chose. The variogram does not. When they disagree, that is the finding — and you only see it by running both. |
| "I'll compare γ between hold and eis intervals." | Pairs are formed within a type precisely because the types have different noise. The γ values are each on their own 1-floor and are comparable in *significance*, not in value. |
| "Use `gamma_err`, it's the measured one." | It understates the error because pairs share intervals. `gamma_err_theory` is the honest floor. |
| "A flat variogram means the measurement failed." | It means the sample did not change detectably at this noise level. That is a scientific result worth stating. |

## Red Flags

- Concluding "no change" from χ² instead of from the variogram.
- Judging γ against a fixed threshold rather than against its error.
- A bin with very few pairs driving the conclusion — check `n_pairs`.
- Lags computed from interval start times rather than centres.
- The variogram flat while the amplitude is significant, reported as if the
  amplitude simply won.
- Reading the variogram *after* the amplitude and letting it confirm what you
  already believed.

## Verification

- [ ] `max_sigma_above_noise` is reported for every interval type present.
- [ ] The conclusion (flat or rising) is stated against the error, not the value.
- [ ] Bins driving the conclusion have enough pairs to mean something.
- [ ] If the run is flat, the analysis stopped there.
- [ ] If the variogram and the amplitude disagree, the reference block was
      re-examined before either result was used.
