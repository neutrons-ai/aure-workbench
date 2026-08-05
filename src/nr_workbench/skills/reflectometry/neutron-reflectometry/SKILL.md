---
name: neutron-reflectometry
description: >
  Baseline domain knowledge for modelling and fitting neutron reflectometry with
  refl1d at SNS REF_L: data file structure, probe construction, SLD values and
  ranges, chi-squared and BIC interpretation, roughness rules, and the refl1d
  API traps.
  USE FOR: any reflectometry modelling or fitting task, choosing SLD bounds,
  judging whether a fit is good, deciding whether to add a layer.
  DO NOT USE FOR: time-resolved change assessment (see tnr-change-assessment)
  or project layout and provenance (see nr-workbench-project).
version: 2
metadata:
  facility: SNS
  instruments: [REF_L, BL-4B]
  techniques: [reflectometry]
  tags: [refl1d, bumps, sld, chi-squared, bic, roughness, probe, modelling]
  source:
    repos: [neutrons-ai/aure, mdoucet/experiments-2025]
    paths:
      - src/aure/skills/neutron-reflectometry/SKILL.md
      - docs/neutron-reflectometry.md
    adaptation: >
      Merged and narrowed to REF_L/BL-4B, restructured into the v2 anatomy.
      Long refinement guidance moved to references/refinement-strategy.md.
---

# Neutron Reflectometry

## Overview

This is the always-on baseline for reflectometry work at REF_L. It covers what
the data files contain, how to turn them into a refl1d probe, what values and
bounds are physically defensible, and how to tell a good fit from a lucky one.

Two facts about REF_L data cause most avoidable errors, so they lead:

- **The 4th column is dQ as FWHM**, not sigma. Using it directly as sigma
  scales every resolution by 2.355.
- **A "combined" file has already merged several angle segments**, so the
  per-segment incident angle is no longer recoverable from it. If you need
  per-angle normalisation, angle offset, or sample broadening, fit the
  `_partial` files instead.

## When to Use

Every modelling or fitting task on reflectivity data. Read it before writing a
model, choosing bounds, or judging a χ².

Not for time-resolved trajectory analysis, which has its own metrics and
failure modes.

## Process

### 1. Know which file you have

| Pattern | What it is |
|---|---|
| `REFL_{run}_combined_data_auto.txt` | All angle segments merged. No header row; 4 columns `Q, R, dR, dQ`. |
| `REFL_{run}_{seg}_{subrun}_partial.txt` | One angle segment. One header line, then the same 4 columns. |

Standard incident angles are `[0.45, 1.2, 3.5]` degrees (theta, not two-theta);
tNR runs use a single angle, usually 0.6°. The header table of a combined file
lists `TwoTheta(deg)` per segment — **halve it** to get theta.

### 2. Build the probe

**Combined file** — `load4` with the FWHM flag set:

```python
from refl1d.probe.data_loaders.load4 import load4

probe = load4(data_file, FWHM=True)
```

**Per-segment file** — build an angle-based probe, which is what makes
`sample_broadening` and `theta_offset` fittable:

```python
import numpy as np
from refl1d.probe import make_probe


def create_probe(data_file, theta):
    q, data, errors, dq = np.loadtxt(data_file).T
    wl = 4 * np.pi * np.sin(np.pi / 180 * theta) / q
    dT = dq / q * np.tan(np.pi / 180 * theta) * 180 / np.pi
    dL = 0 * q  # angular-only resolution; see the BL-4B skill
    return make_probe(
        T=theta,
        dT=dT,
        L=wl,
        dL=dL,
        data=(data, errors),
        radiation="neutron",
        resolution="uniform",
    )
```

`dT` and `dL` are FWHM here, matching the file convention.

### 3. Choose SLDs and bounds

| Material | SLD (×10⁻⁶ Å⁻²) | | Material | SLD |
|---|---|---|---|---|
| Silicon | 2.07 | | Copper | 6.55 |
| SiO₂ | 3.47 | | Titanium | −1.95 |
| Air | 0.0 | | D₂O | 6.19 |
| Gold | 4.5 | | H₂O | −0.56 |

Bounds rules:

- At least **±2.0** around nominal — materials are rarely stoichiometric and
  intermixing is real. Never narrower than ±1.0.
- **±3.0 or wider** for adhesion layers such as Ti, which intermix freely
  (e.g. −5.0 to 1.0).
- **Never let the substrate SLD vary** unless the user asks for it.

### 4. Respect the physical floors

- Roughness **≥ 5 Å**; below that is not physical.
- Roughness **< half the thickness** of either adjacent layer, or you get
  profile artifacts the χ² will not show you.
- Typical roughness 5–30 Å.
- Minimum layer thickness **5 Å** — thinner cannot be resolved.

### 5. Read the χ² honestly

| χ² | Reading |
|---|---|
| < 0.5 | Overfitting, or overestimated error bars |
| ≈ 1 | Ideal |
| 1–2 | Excellent |
| 2–5 | Good; minor discrepancies |
| 5–10 | Marginal; the model is probably missing a feature |
| > 10 | Poor; structural problem |

### 6. Justify any added complexity with BIC

`BIC = n·ln(χ²) + k·ln(n)`, lower is better. Each layer costs three parameters
(thickness, SLD, roughness), so adding one must buy a substantial χ²
improvement.

Do not split a layer into sublayers (CuO + Cu₂O) unless χ² > 10 **and** the
residuals show a clear unmodelled contrast step. If adding a layer was already
tried and reverted on BIC, do not re-add it — try something else.

By default, do **not** add native SiO₂ on silicon: it is 10–20 Å and costs three
parameters that would otherwise resolve layers you actually care about. If the
user asks for it, add it.

### 7. Refine in priority order

Constrain unphysical values → widen bounds that are pinned → update starting
values → check the ambient SLD → enable `sample_broadening` if segments are
unevenly fit → and only then consider structural changes, one at a time.

Full detail, including when to enable `sample_broadening` and `theta_offset`:
[references/refinement-strategy.md](references/refinement-strategy.md).

## Rationalizations

| Excuse | Rebuttal |
|---|---|
| "χ² dropped when I added a layer, so the layer is real." | χ² almost always drops when you add parameters. Check BIC, and check that the layer is thicker than the resolution limit. |
| "The fit is good, so the model is right." | A thin layer sits on an SLD × thickness ridge — many (Δρ, t) pairs give the same χ². A good fit is necessary, not sufficient. |
| "I'll widen the bounds until it converges." | Widening in an unphysical direction buys χ² with nonsense. Widen only toward values the material could actually take. |
| "The ambient is water, so SLD = −0.56." | Unless someone confirmed it is H₂O. Unspecified deuteration is the single most common cause of an unexplained critical edge; suspect it first. |
| "I'll set roughness to 2 Å, the fit likes it." | Below 5 Å is not physical, and roughness above half the adjacent thickness produces profile artifacts χ² cannot see. |
| "`copper.material.rho.range(...)` should work." | It crashes. See Red Flags. |

## Red Flags

- **`SLD(...)` objects have no `.material`, `.thickness`, or `.interface`.**
  Those live on the `Slab` objects inside the stack. Write
  `sample[1].material.rho.range(2.0, 4.0)`, never
  `copper.material.rho.range(...)` — the latter raises
  `'SLD' object has no attribute 'material'`.
- dQ used as sigma without dividing by 2.355 (or `FWHM=True` not passed).
- Substrate SLD floating.
- A layer thinner than the resolution limit reported with a tight uncertainty.
- Roughness exceeding half of an adjacent layer's thickness.
- Suggesting a change to the fitting method, the error bars, the Q range, or the
  back-reflection geometry. Those are set by the experiment, not the model.
- Several structural changes made in one step, so none can be attributed.

## Verification

Before reporting a fit:

- [ ] χ² is in a defensible band, and you have said which.
- [ ] BIC supports every layer present.
- [ ] Every roughness ≥ 5 Å and below half of each adjacent thickness.
- [ ] No parameter is pinned at a bound.
- [ ] The SLD profile stays within its bounding media — no erf-tail excursion
      outside the ambient or substrate values.
- [ ] The ambient SLD matches the stated solvent, or the discrepancy is
      explained.
- [ ] For multi-segment fits, per-segment χ² values are comparable; if the
      low-Q segment is much worse, consider `sample_broadening`.
