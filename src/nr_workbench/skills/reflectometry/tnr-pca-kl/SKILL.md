---
name: tnr-pca-kl
description: >
  Diagnose a time-resolved change that one template cannot describe, using PCA
  of the R(Q,t) matrix and the symmetric KL divergence.
  USE FOR: following up when chi2_res is well above 1, deciding whether a
  second reaction coordinate is present, cross-checking a late-block template
  against PC1.
  DO NOT USE FOR: routine assessment -- these are follow-ups, not first steps
  (see tnr-variogram and tnr-amplitude).
version: 2
metadata:
  facility: SNS
  instruments: [REF_L, BL-4B]
  techniques: [reflectometry, time-resolved]
  tags: [tnr, pca, kl-divergence, multi-template, time-resolved]
  source:
    repo: mdoucet/experiments-2025
    paths: [docs/tnr-pca.md, docs/tnr-kl.md]
    adaptation: >
      Merged: both answer the same follow-up question, and neither is a first
      step. Restructured into the v2 anatomy.
---

# tNR PCA and KL Divergence

## Overview

Both of these exist for one situation: the amplitude's `χ²_res` is well above 1,
meaning the change direction is **rotating in Q** and no single template
describes it.

**PCA** decomposes `M[t, i] = log₁₀ R(t_i, Q_i)`, mean-centred along time, by
SVD. Working on `log₁₀ R` is essential — `R` spans many decades between low and
high Q, so a linear-R PCA would be dominated by the bright low-Q region and
miss structure everywhere else. Only Q points valid in *every* interval are
used, so the matrix has no missing entries.

It reports **components** `V_j` (how log R varies with Q along PC_j),
**scores** `U_j S_j` (the amplitude of PC_j at each time), and the **explained
variance ratio**.

**KL** is a symmetric Kullback-Leibler divergence per Q bin, treating each bin
as a Gaussian. It is a second opinion with different weighting: unlike χ² it is
not a pure sum of squares, so it responds differently to a few large deviations
versus many small ones.

## When to Use

- `amplitude.chi2_res_median` is well above 1, or drifts over the run.
- The change appears to reverse direction in Q partway through.
- You want to check a late-block template against PC1 before trusting it.

Not as a first look. PC1 is the direction of maximum *variance* over the run,
which need not be the direction from the initial to the final state — so on a
run with one clean process it tells you nothing the amplitude did not.

## Process

### 1. Compute it

```bash
nrw tnr pca data/tnr/<run>_<binning> --label r<run>
nrw tnr kl  data/tnr/<run>_<binning> --label r<run>
```

### 2. Read the explained variance first

`pca.explained_variance` in the assessment.

- **PC1 dominant, PC2 and PC3 small** → one process. The amplitude's single
  template was adequate and `χ²_res` should have said so. If it did not,
  suspect the template or the reference rather than the physics.
- **PC1 and PC2 comparable** → two processes are active. A single template
  cannot represent this; a two-template fit is the fix.

### 3. Look at the PC score trajectories

One panel per PC. A PC2 score that grows only in the second half of the run is
the signature of a second process starting partway through — which is precisely
what a single global template cannot represent.

### 4. Look at the Q-mode shapes

The bottom panel shows `V_j` against log Q. Compare PC1's shape with the
amplitude's template: they should broadly agree.

### 5. Use PC1 as a template if it helps

```bash
nrw tnr assess <dir> --template pca
```

This turns PC1 into a projection with proper error bars.

**The two template choices agree on shape but not on scale.** For run 218389
the two `a(t)` series correlate at 0.9993 with `a_pca = 1.78·a_late`: each is
normalised so the late coadd projects to 1 *under its own weights*, and the two
have different Q shapes. Compare trajectories, never absolute values.

**A drop in that correlation is the diagnostic worth watching.** It means PC1
and the start-to-end direction have parted company, which happens exactly when
more than one process is active.

### 6. Use KL as a cross-check

If KL and χ² tell different stories about which intervals moved, the difference
is in how they weight a few large deviations against many small ones. That is
informative rather than contradictory — look at the residual heatmap.

## Rationalizations

| Excuse | Rebuttal |
|---|---|
| "PC1 explains 34%, so the analysis is poor." | The EVR denominator includes all the noise variance. What matters is PC1 against PC2, not PC1 against 100%. |
| "PC1 is the change, so I'll interpret its shape physically." | PC1 is the direction of maximum variance, which need not be the direction from start to end. Interpret it only after checking it agrees with the late-block template. |
| "`--template pca` gave a bigger amplitude, so it's more sensitive." | The two normalisations differ; `a_pca = 1.78·a_late` for 218389 with correlation 0.9993. The scales are not comparable. |
| "χ²_res is 1.6, close enough to 1." | That is the threshold at which a second coordinate usually shows up. Run the PCA and find out rather than assuming. |
| "I'll run PCA first, it's the most general method." | It needs no reference but it also answers no question you have yet. The variogram is the first step. |

## Red Flags

- PCA run before the variogram and the amplitude.
- PC1's Q shape interpreted physically without comparing it with the template.
- Absolute `a` values compared between `--template late` and `--template pca`.
- A high `χ²_res` noted and then ignored while the single-template `a(t)` is
  used anyway.
- A PCA on linear `R` rather than `log₁₀ R` — it would be dominated by low Q.
- Concluding "two processes" from a PC2 that is at the noise level.

## Verification

- [ ] This was reached because `χ²_res` demanded it, not by default.
- [ ] `explained_variance` is reported and PC1 compared with PC2, not with 100%.
- [ ] PC1's Q shape was compared with the amplitude template.
- [ ] Any cross-template comparison is of trajectories, not absolute values.
- [ ] If two processes are claimed, PC2's score is above the noise and its time
      dependence is described.
