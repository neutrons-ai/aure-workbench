---
name: tnr-chi2
description: >
  Quote the size and significance of a time-resolved change using delta, and
  avoid the counting-time trap that makes raw chi-squared misleading.
  USE FOR: quoting how big a change is, reporting significance in sigma,
  reading the Q-band breakdown of where the change lives.
  DO NOT USE FOR: deciding whether anything changed (see tnr-variogram) or how
  it evolved over time (see tnr-amplitude).
version: 2
metadata:
  facility: SNS
  instruments: [REF_L, BL-4B]
  techniques: [reflectometry, time-resolved]
  tags: [tnr, chi-squared, delta, significance, qbands, time-resolved]
  source:
    repo: mdoucet/experiments-2025
    paths: [docs/tnr-chi2.md]
    adaptation: >
      Restructured into the v2 anatomy, pointed at `nrw tnr`, and merged in the
      Q-band material since both come from the same command.
---

# tNR Chi-Squared, delta, and Q Bands

## Overview

Against the shared coadded reference, with `var_i = dR_i² + s_i·dR_ref²`:

```
χ²_i     = (1/N) Σ (R_i − R_ref)² / var_i
snr_i    = sqrt(max(0, χ²_i − 1))
δ_i      = sqrt(max(0, (1/N) Σ [(R_i − R_ref)² − var_i] / R_ref²))
signif_i = (χ²_i − 1)·sqrt(N/2)
```

**Quote δ. Do not quote χ².** Three reasons, all fatal:

- **The noise floor is additive.** χ² = 1 is not "no change", it is "no change
  *detectable at this noise level*".
- **The value depends on counting time.** χ² ≈ 1 + Δ²/var, so it mixes the size
  of the change with the noise. For run 218389 median dR/R is 0.141 for the
  30 s holds and 0.085 for the ~85 s eis slices; an identical physical change
  reads about 2.8× larger in eis. The measured late-run eis/hold χ² ratio is
  1.99 — a factor-of-two split with no physical content, and the source of the
  sawtooth in the top panel.
- **Its scatter is its own.** χ²/N has intrinsic spread `sqrt(2/N_Q)` about 1.
  At N_Q = 261 that is 0.088, against an observed scatter about a smooth trend
  of 0.095 — so essentially *all* the visible point-to-point movement is the χ²
  distribution fluctuating, not the sample changing.

`δ` subtracts the noise variance, so it is a property of the sample and is
count-time independent. That is what belongs in a paper.

## When to Use

Fourth in the reading order, once the variogram has established that something
changed and the amplitude has described how. Use it to put a number and an
uncertainty on the change, and to see which Q bands moved.

Not as the primary evidence of change, and never to compare interval types.

## Process

### 1. Compute it

```bash
nrw tnr chi2 data/tnr/<run>_<binning> --label r<run>
nrw tnr qbands data/tnr/<run>_<binning> --label r<run>
```

### 2. Sanity-check the reference

`chi2.chi2_over_reference` should be ≈ 1. If it is not, the reference block or
the leave-one-out variance is wrong, and nothing downstream is trustworthy.

A value near 0.90 specifically suggests the leave-one-out sign was disabled:
intervals inside the reference block are correlated with the coadd they helped
build, so their variance is `dR_i² − dR_ref²`, not `+`.

### 3. Quote δ, with its significance

`chi2.final_delta` per interval type, and `signif` from the table. Say
"the reflectivity changed by δ = 0.043 ± … , significance 8σ", not "χ² rose to 3".

### 4. Read the Q bands

The `qbands` block says where in Q the change lives, in dimensionless units:

- **Bands moving in opposite directions** → a fringe shift, i.e. a **thickness**
  change.
- **Bands moving together** → an overall **contrast** change.

`qbands.bands_move_together` reports this directly. It must agree with
`template.implied_change` from the amplitude; if it does not, neither is
settled.

### 5. Use the expected band on the plot

`*_chi2.png` draws the expected χ²/N fluctuation band. Points inside it are
consistent with no change whatsoever. That band is the fastest way to see that
the wiggles are statistical.

## Rationalizations

| Excuse | Rebuttal |
|---|---|
| "χ² doubled during the EIS steps, so the measurement perturbs the sample." | The eis slices count ~2.8× longer. The measured 1.99 ratio is almost exactly what counting time alone predicts. Compare δ, or compare amplitudes. |
| "χ² = 1 means nothing changed." | It means nothing changed *detectably at this noise level*. The floor is additive. |
| "χ² is wandering, so the sample is unstable." | At N_Q = 261 the intrinsic spread is 0.088. Almost all of that wander is the χ² distribution. |
| "I'll use `--delta2-clip element`, it's more careful per-bin." | Element-wise clipping is biased upward by 0.4839·(dR² + dR_ref²) per Q bin. Prefer the `delta` column. |
| "δ is small so the change doesn't matter." | δ is a fractional RMS over all Q. A localized fringe shift can be scientifically large with a modest δ. Read the Q bands. |

## Red Flags

- Any comparison of raw χ² or snr between `hold` and `eis`.
- Reporting χ² as the headline number instead of δ.
- `chi2_over_reference` far from 1, ignored.
- Q bands contradicting the amplitude template, with one silently preferred.
- `--delta2-clip element` used without noting the upward bias.
- Concluding "unstable sample" from scatter that lies inside the expected band.

## Verification

- [ ] `chi2_over_reference` ≈ 1.
- [ ] The reported number is δ with a significance, not a raw χ².
- [ ] No cross-type χ² comparison appears anywhere in the write-up.
- [ ] The Q-band conclusion agrees with `template.implied_change`.
- [ ] Points quoted as "changed" lie outside the expected fluctuation band.
