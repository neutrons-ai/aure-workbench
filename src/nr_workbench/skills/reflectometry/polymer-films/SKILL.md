---
name: polymer-films
description: >
  Model polymer and ionomer films, including swelling, solvent uptake and
  isotope labelling.
  USE FOR: setting a polymer SLD, interpreting a film that is thicker or less
  dense in solvent than dry, choosing a labelling or contrast strategy,
  modelling a diffuse polymer-solvent interface.
  DO NOT USE FOR: metal or oxide layers (see metal-oxide-interfaces) or the
  solvent itself (see solvent-contrast-matching).
version: 2
metadata:
  facility: SNS
  instruments: [REF_L, BL-4B]
  techniques: [reflectometry, soft-matter]
  tags: [polymer, ionomer, swelling, solvation, deuteration, brush, film]
  source:
    repo: neutrons-ai/aure
    path: src/aure/skills/polymer-films/SKILL.md
    adaptation: >
      Restructured into the v2 anatomy. SLD tables and thickness ranges are
      unchanged. The swelling arithmetic is written out, and the graded-profile
      section points at the specific failure it prevents -- a slab model
      absorbing a concentration gradient into roughness.
---

# Polymer and ionomer films

## Overview

A polymer film in a solvent is not the dry polymer. It swells, it takes up
solvent, and its interface with the solvent is often graded rather than sharp.
All three change what the model should look like, and all three are commonly
absorbed into a roughness that then means nothing.

The rule that does most of the work: **a film's fitted SLD in solvent tells you
its solvent content**, and that is the number to report.

## When to Use

- Any polymer, ionomer or brush layer.
- A film whose fitted SLD sits between the dry polymer's and the solvent's.
- A film thicker in solvent than it was dry.
- A polymer-solvent interface with implausibly large roughness.
- Designing a labelling scheme, or reading one someone else designed.

## Process

### 1. Know the dry numbers

| Polymer | SLD (10⁻⁶ Å⁻²) | Deuterated |
|---|---|---|
| Polystyrene (hPS) | 1.41 | d8-PS 6.47 |
| PMMA | 1.06 | d8-PMMA 7.22 |
| Nafion / PFSA ionomer | 4.1–4.3 | — (fluorinated, already high) |
| PEO | 0.64 | d4-PEO 6.33 |
| Polyethylene | −0.33 | d4-PE 8.24 |
| PDMS | 0.06 | — |

Fluoropolymers are the useful exception: fluorine gives a high SLD without
deuteration, so a PFSA ionomer at ~4.2 already contrasts strongly against both
H₂O and most metals.

```python
from nr_workbench.aure_adapter import sld

sld("C8H8", density=1.05)  # polystyrene from formula and density
```

### 2. Turn a fitted SLD into a solvent fraction

This is the main quantitative step, and it is one line:

```
φ_solvent = (ρ_fit − ρ_dry) / (ρ_solvent − ρ_dry)
```

A PFSA ionomer (dry 4.2) in D₂O (6.36) fitting to 5.1:

```
φ = (5.1 − 4.2) / (6.36 − 4.2) = 0.42       ~42% solvent by volume
```

Report the fraction. An SLD of 5.1 is not interpretable by a reader; "42%
hydrated" is.

Two things to check on the result: it must lie between 0 and 1, and it should be
consistent with the film's thickness change. A film that swelled 40% in
thickness and reports 5% solvent is inconsistent — one of the two numbers is
wrong.

### 3. Expect swelling, and let thickness be free per state

Typical dry thicknesses are 50–1000 Å; hydrated ionomers commonly swell 10–50%.
So thickness is `per: state` whenever the ambient changes:

```yaml
parameters:
  - {path: Ionomer.thickness, range: [200, 900], per: state, in: [dry, wet]}
  - {path: Ionomer.rho,       range: [4.0, 6.4], per: state, in: [dry, wet]}
```

Both are per-state here, and that is correct: the film genuinely is a different
thing wet. What stays `per: model` is the substrate and anything under the film.

### 4. Do not let roughness stand in for a gradient

A polymer-solvent interface is often a **concentration gradient** tens of
angstroms deep, not a sharp interface with roughness. A single slab plus a large
roughness can fit it, but then:

- the roughness is not a roughness, and quoting it as one is wrong;
- roughness larger than about half the layer thickness is unphysical as an
  interface width, and the model is straining;
- the gradient's shape — which is the physics — is discarded.

The honest alternative is two or three sub-layers with decreasing polymer
fraction. That costs parameters, so decide with `thin-layer-degeneracy`'s rules:
add them only if the Q range resolves them, and check the extra layers do not
collapse or rail.

### 5. Choose labelling to make one thing visible

The point of deuteration is to make one component stand out:

- **Label the polymer** (d8-PS at 6.47) against H₂O (−0.56) for maximum contrast
  against the solvent.
- **Match the solvent to the polymer** to make the film invisible and isolate
  what is under it.
- **Match the solvent to the substrate** (38% D₂O for silicon) to remove the
  substrate's contribution.

`contrast_match_ratio` from the adapter gives the mixing ratio for any target.

## Rationalizations

**"The film SLD came out between dry and solvent — the model is wrong."** That
is the expected result for a swollen film. Convert it to a volume fraction.

**"Roughness is 80 Å on a 200 Å film, but χ² is good."** It fits because a wide
error function resembles a gradient. It is not an interface width, and reporting
it as one overstates what was measured.

**"The film is thinner wet than dry."** Possible — collapse, dissolution, or
delamination — but check the fit is not trading thickness against SLD along the
degeneracy ridge first.

**"I'll fix the polymer SLD at the dry value since I know the material."** Only
valid in air. In solvent that forces all the swelling into thickness, which is
usually not where it is.

**"Ionomers are just polymers."** They have mobile ions and an uptake that
depends on counter-ion and humidity, so the same film measured twice under
nominally the same conditions can genuinely differ. Record the conditions.

## Red Flags

- A polymer SLD outside the range between its dry value and the ambient.
- Roughness exceeding half the layer it bounds.
- A film swelling in thickness with no change in SLD, or the reverse — both
  should move together.
- A solvent fraction outside 0 to 1.
- A dry polymer SLD used as a fixed value in a wet measurement.
- Three or more sub-layers used to describe a gradient over a Q range that
  cannot resolve them.

## Verification

```bash
nrw data features <file>     # critical edge -> ambient/film SLD
nrw model preview <spec>     # scopes and bounds
```

Three checks:

1. **Convert every fitted polymer SLD to a solvent fraction** and check it lies
   in 0–1 and matches the thickness change.
2. **Compare roughness against thickness.** Over half means the model is
   describing a gradient with the wrong tool.
3. **Fit the swollen state from at least two starting thicknesses.** Thickness
   and SLD trade off along the ridge; if the two runs land in different places
   at similar χ², say so.
