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
    repo: neutrons-ai/aure
    path: src/aure/skills/neutron-reflectometry/SKILL.md
    adaptation: >
      Narrowed to REF_L/BL-4B, restructured into the v2 anatomy. Long
      refinement guidance moved to references/refinement-strategy.md.
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

**SLD is a density measurement, not a composition label.** It is
`ρ = (mass density / molar mass) · N_A · b_coh`, so at fixed composition it scales
linearly with density. A sputtered or electrochemically cycled film is routinely
well below bulk — porosity, grain boundaries, hydrogen or deuterium uptake,
partial oxidation, solvent ingress. **Copper is a repeat offender.** So the useful
quantity is the *fraction of bulk density*, and a nominal SLD is meaningless
without the density it assumes:

| Material | Bulk SLD (10⁻⁶ Å⁻²) | at density (g/cm³) |
|---|---|---|
| Si | 2.07 | 2.329 |
| SiO₂ | 3.47 | 2.196 |
| Cu | 6.55 | 8.96 |
| Ti | −1.91 | 4.506 |
| Au | 4.66 | 19.3 |
| D₂O | **6.37** | 1.107 |
| H₂O | −0.56 | 0.997 |
| Air | 0.0 | — |

**D₂O is 6.37, not 6.19.** 6.19 is roughly 96% deuteration and it is a value that
circulates. Pinning a solvent 0.18 below pure is a 0.18 of contrast handed to
whatever layer is next to it. If a fit pulls the solvent *below* pure D₂O the
usual causes are real H in the cell — atmospheric exchange, an exchangeable
proton on the electrolyte, incomplete purging — or a diffuse interfacial region
bleeding into the backing. Both are findings. Neither is a reason to widen the
bound and move on.

Compute anything not in that table rather than copying a quick-reference one —
several published tables disagree, badly, for the oxides:

```python
from nr_workbench.aure_adapter import sld

sld("Cu")  # 6.55, bulk density from the built-in table
sld("Cu2O", density=6.00)  # 5.36 -- an oxide needs an explicit density
```

Bounds rules:

- **State the bound as a density range, then convert.** A metal film that may be
  up to 15% porous is `0.85–1.02 × bulk`: for Cu that is `range: [5.57, 6.69]`.
  Put the fraction in a spec comment — the fraction is the physical claim and the
  SLD number is only its consequence.
- **±3.0 or wider** for an adhesion layer such as Ti, which intermixes freely
  (e.g. −5.0 to 1.0). Intermixing changes composition, not just density, so a
  fraction-of-bulk bound is the wrong shape there.
- **Fixing a metal SLD to its bulk value is a choice, not a default.** It asserts
  the film is fully dense. If you fix it, say so in the note, and test it once by
  freeing it and comparing BIC. A fit that improves materially when a metal SLD
  is freed was being told something false.
- **Never let the substrate SLD vary** unless asked. A silicon wafer really is
  bulk-dense.
- **Float the ambient medium's SLD, unless it is air.** A book value for H₂O,
  D₂O or a deuterated solvent assumes 100% isotopic purity at a stated
  temperature, and the cell rarely delivers either — pinning it pushes that
  few-tenths error into a layer thickness instead. Air is the one ambient
  worth fixing; its SLD really is 0. See `solvent-contrast-matching`.

**A fitted SLD below bulk is information, not an error.** Convert it back to a
density fraction and ask whether that fraction is plausible for how the film was
made. It means porosity, solvent ingress, or roughness being absorbed — not a
different material.

**Naming a value after a compound makes a claim about density.** Calling 4.1
"Cu₂O" says Cu₂O at 76% of bulk, because bulk Cu₂O is 5.36. That may be the right
model for a porous native oxide, but it is an assumption to be recorded, not a
material constant to be looked up.

For the copper and titanium oxides — including the fact that dense CuO (6.46) is
nearly contrast-matched to Cu (6.55) and therefore close to invisible — read
`metal-oxide-interfaces`.

### 4. Respect the physical floors

- Roughness **≥ 5 Å**; below that is not physical. Watch for it railing *down* on
  that floor — that usually means something else in the model is over-smeared.
- Typical roughness 5–30 Å.
- Minimum layer thickness **5 Å** — thinner cannot be resolved.

**Half the thickness is where interpretation changes, not where the model breaks.**
Roughness in refl1d is the σ of an error function, and a slab between two erfs of
σ comparable to its own thickness is no longer a slab: its nominal SLD is attained
nowhere in the profile, and its fitted thickness and SLD stop being separable
quantities. What you have instead is a **three-parameter parametrisation of a
graded SLD profile**, and that is a legitimate and often necessary tool — a
diffuse hydroxide or hydrated-oxide region, an SEI, a gas-populated electrode
interface, a swollen polymer surface. Electrochemistry produces these routinely
and no slab model describes them.

So crossing the line is allowed. What is not allowed is crossing it silently:

1. **Say in the note that you are parametrising a gradient**, not measuring a
   layer. One sentence.
2. **Report the SLD profile**, not the slab numbers. `d = 11 Å, ρ = 4.1` describes
   a shape that is not in your model; the profile is what the model actually says.
3. **Quote an invariant.** For a smeared slab the product `Γ = d · Δρ` survives
   the degeneracy that `d` and `Δρ` individually do not — see
   `thin-layer-degeneracy`.
4. **Check the profile for erf artifacts.** Two independent error functions closer
   together than their widths can make ρ(z) *overshoot* the bounding medium before
   it dips, which is arithmetic, not physics.

`nrw check` reports when a layer's roughnesses can sum past its thickness. Treat
that as a prompt to write down which of the two things you are doing, not as an
instruction to tighten the bound.

### 5. Read the χ² honestly

| χ² | Reading |
|---|---|
| < 0.5 | Overfitting, or overestimated error bars |
| ≈ 1 | Ideal |
| 1–2 | Excellent |
| 2–5 | Good; minor discrepancies |
| 5–10 | Marginal; the model is probably missing a feature |
| > 10 | Poor; structural problem |

**The bands describe how good the fit is, not whether you are finished.** A
reflectivity co-refinement has 2000+ points. At n = 2000, χ²_red = 1 has a
standard error of about `sqrt(2/n)` = 0.03, so **χ² = 1.5 is roughly 16σ from
acceptable** — there is something in the data the model does not contain, and
"Good; minor discrepancies" is not a licence to stop looking for it. Above ~1.5
with that many points, decompose before accepting:

1. **Per segment.** One segment three times worse than the others is a
   normalisation or resolution problem, not structure.
2. **Per Q band** within each segment. Excess concentrated at a segment *edge* is
   a stitching or band-edge artifact; excess spread across the fringes is not.
3. **In-phase vs quadrature against the model's own fringes.** Regress the
   residual on the model's fringe modulation and on its Q-derivative. In-phase
   means the fringe *depth* is wrong — resolution, interfacial width, or a
   thickness distribution. Quadrature means the fringe *positions* are wrong —
   a thickness or an angle. The two have different fixes and χ² alone hides
   which you have.

Residual structure is coherent and adds in phase, so a 10σ pattern can sit inside
a χ² that reads as "good". A flat residual at χ² = 2.5 and an oscillating one at
χ² = 2.5 are different findings.

**Inflate the uncertainties by √χ²_red before quoting any interval.** DREAM's
posterior assumes the reported `dR` are correct. χ²_red = 3 says they are
understated by about `√3` = 1.7, or the model is wrong, or both — and in every
case the raw 68% interval is too narrow by that factor. Quoting `409.6 ± 0.8 Å`
off a χ² of 2.9 claims a precision the fit does not have, and it will make two
states look 3σ apart when they are not. Either fix the model until χ² ≈ 1, or
scale the intervals and say you did.

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

### 8. Explore with amoeba; decide with DREAM

Amoeba is fast and good enough while the model is still moving — every step of
the priority order above is cheaper to iterate with it. But it returns a point
estimate, not a posterior, so it cannot tell you an uncertainty, a parameter
correlation, or whether two fits are significantly different, because it never
sampled one.

The moment any of that is what you are about to do — quote an interval, claim
two states or two fits differ, or write the note that settles on a model —
re-fit with `--method dream` first. A conclusion drawn from amoeba's point
estimate where DREAM was never run is a guess dressed as a number.

## Rationalizations

| Excuse | Rebuttal |
|---|---|
| "χ² dropped when I added a layer, so the layer is real." | χ² almost always drops when you add parameters. Check BIC, and check that the layer is thicker than the resolution limit. |
| "The fit is good, so the model is right." | A thin layer sits on an SLD × thickness ridge — many (Δρ, t) pairs give the same χ². A good fit is necessary, not sufficient. |
| "I'll widen the bounds until it converges." | Widening in an unphysical direction buys χ² with nonsense. Widen only toward values the material could actually take. |
| "The ambient is water, so SLD = −0.56." | Unless someone confirmed it is H₂O. Unspecified deuteration is the single most common cause of an unexplained critical edge; suspect it first. |
| "I'll set roughness to 2 Å, the fit likes it." | Below 5 Å is not physical. Above half the adjacent thickness is allowed but stops being a layer — declare it as a gradient parametrisation and report the profile. |
| "χ² is 2.9, which the table calls good, so I'm done." | The table grades the fit, not your understanding of it. At 2000 points, χ²_red = 1 has a standard error of 0.03, so 2.9 is not a rounding error — something coherent is unmodelled. Decompose it. |
| "DREAM converged, so ± 0.8 Å is the uncertainty." | Only if χ²_red ≈ 1. DREAM trusts the reported `dR`; at χ²_red = 2.9 the intervals are too narrow by √2.9, and the 3σ difference you are about to report is 1.9σ. |
| "The amoeba fit converged nicely, so ± the last step size is close enough." | Amoeba has no posterior; there is no ± to read off it, close or otherwise. Re-fit with `--method dream` before quoting anything. |
| "The two states differ by 3 Å with ± 0.6 Å errors, so it changed." | Inflate first, then check whether the intervals still separate. Then check whether a nuisance parameter is correlated with the thing you think changed. |
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
- Roughness past half an adjacent thickness with no note saying it is a gradient
  parametrisation.
- A DREAM interval quoted without inflation on a fit whose χ²_red is well above 1.
- An uncertainty, a significance claim, or a promoted fit backed only by an
  amoeba run — none of those are answerable without a posterior.
- Two states declared different on intervals that overlap once inflated.
- χ² accepted as "good" with no per-segment or per-Q-band breakdown behind it.
- Suggesting a change to the fitting method, the error bars, the Q range, or the
  back-reflection geometry. Those are set by the experiment, not the model.
- Several structural changes made in one step, so none can be attributed.

## Verification

Before reporting a fit:

- [ ] χ² is in a defensible band, and you have said which.
- [ ] Above χ²_red ≈ 1.5 on a large point count, the residual has been decomposed
      per segment, per Q band, and in-phase vs quadrature — and what it showed is
      written down.
- [ ] Any quoted uncertainty or claimed significance comes from a `--method
      dream` run, not amoeba's point estimate.
- [ ] Every quoted interval is inflated by √χ²_red, and the note says so.
- [ ] No difference between states is called significant on raw DREAM intervals
      when χ²_red > 1.2.
- [ ] BIC supports every layer present.
- [ ] Every roughness ≥ 5 Å. Any roughness past half an adjacent thickness is
      declared as a gradient parametrisation, with the profile and `Γ = d · Δρ`
      reported instead of the slab numbers.
- [ ] No parameter is pinned at a bound.
- [ ] The SLD profile stays within its bounding media, or an erf-tail excursion
      outside them is identified as the arithmetic artifact it is.
- [ ] Every nominal SLD has the density it assumes recorded next to it, and any
      fitted SLD below bulk is converted back to a density fraction and judged.
- [ ] The ambient SLD matches the stated solvent, or the discrepancy is
      explained.
- [ ] The ambient's `rho` is a fitted parameter, not pinned to a book value —
      unless the ambient is air.
- [ ] For multi-segment fits, per-segment χ² values are comparable; if one is
      much worse, the normalisation was checked before `sample_broadening` was
      reached for.
