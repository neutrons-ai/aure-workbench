---
name: thin-layer-degeneracy
description: >
  Judge how much a thin layer in a reflectometry model can actually be trusted,
  and why a chi-squared or BIC comparison can reject a layer that is really there.
  USE FOR: any model containing a layer thinner than about 30 A, deciding
  whether an expected layer is "not needed", reading correlated SLD/thickness
  uncertainties, choosing starting points for a model that keeps collapsing.
  DO NOT USE FOR: whether a time-resolved run changed (see tnr-change-assessment)
  or instrument conventions (see refl-bl4b-instrument).
version: 2
metadata:
  facility: SNS
  instruments: [REF_L, BL-4B]
  techniques: [reflectometry, model-selection]
  tags: [degeneracy, thin-layer, bic, local-minima, contrast-thickness, uncertainty]
  source:
    repo: neutrons-ai/aure
    path: src/aure/skills/thin-layer-degeneracy/SKILL.md
    adaptation: >
      Restructured into the v2 anatomy and pointed at nrw commands. The physics
      -- the contrast-thickness ridge, the BIC caveat, mode enumeration -- is
      unchanged. The tNR prior section is new: a time series gives a
      continuity constraint AuRE's single-run framing does not consider.
---

# Thin layers, and how far to trust them

## Overview

Reflectivity constrains a thin layer mainly through the **product** of its
contrast and its thickness, `Δρ · t`, not through `ρ` and `t` separately. Below
the real-space resolution limit — roughly `2π / Q_max`, about **30 Å** for a
typical REF_L `Q_max ≈ 0.2 Å⁻¹` — many `(ρ, t)` pairs along a curve of constant
`Δρ · t` fit almost equally well.

Three consequences follow, and all of them look like success:

- SLD and thickness are individually poorly determined even when their product
  is tightly determined. Expect large, strongly correlated uncertainties.
- The likelihood surface has **distinct local minima** — a thin dense layer
  versus a thicker dilute one — separated by barriers a local optimizer will
  not cross, and a global one will not cross at modest effort.
- Two fits with different `(ρ, t)` and the same product have essentially the
  same χ². Which one the optimizer lands in is an accident of starting point.

## When to Use

- Any model with a layer under ~30 Å. On this beamline that is most native
  oxides and every adhesion layer.
- When a layer you have physical reason to expect fits to near-zero thickness.
- When a χ² or BIC comparison is about to be used to accept or reject a
  structural change.
- When a fitted SLD and thickness have huge uncertainties but a sensible-looking
  central value.
- When two fits of the same data disagree about a thin layer but have similar χ².

## Process

### 1. Establish whether the layer is even resolvable

```
t_min ≈ 2π / Q_max
```

`nrw data features <file>` reports the Q range actually measured. At
`Q_max = 0.2 Å⁻¹`, anything under ~30 Å is in the degenerate regime. Say so in
`sample.md` before fitting, not after.

### 2. Read the uncertainty as a pair, not two numbers

A thin layer with `ρ = 5.0 ± 2.0` and `t = 25 ± 12` is not two loose parameters.
It is one well-determined product and one unconstrained direction along the
ridge. Quote `Δρ · t` if that is what the data supports, and say the split is
not determined.

DREAM gives you this directly: the 2D marginal for `(ρ, t)` will show the ridge.
An optimizer run gives a point on the ridge and no indication there is a ridge.

### 3. Do not let BIC reject a layer the optimizer failed to fit

Model selection compares the *best achievable* fit of each candidate. If the
optimizer settled in a local minimum for the more complex model, its χ² is too
high, its BIC looks too large, and a real layer is rejected as "not justified".

Signatures of a layer-absorbing local minimum, rather than a true rejection:

- an **adjacent** layer's parameter pinned at a bound — an adhesion layer's SLD
  railed to its limit is the classic one;
- a roughness pinned at a bound;
- a tiny χ² change for the added parameters;
- the added layer collapsing to its minimum thickness.

Any of those means re-optimise the complex model from better starting points and
compare again. It does not mean the layer is absent.

### 4. Enumerate SLD modes rather than hoping

The reliable escape is to stop treating SLD as continuous. Fit the same model
several times with the thin layer's SLD **fixed** at each physically plausible
value — the pure material, a plausible hydrated or oxidised value, the ambient —
letting thickness and everything else float. Then compare.

```bash
for rho in 3.0 4.5 5.5 6.3; do
  # one spec per mode, each committed, each with its own fit record
  nrw fit run samples/S1/models/oxide-rho-$rho.py --method dream --samples 50000
done
nrw ls
```

Each mode is a separate committed spec with its own fit record, which is exactly
what the provenance layer is for: four results you can compare, rather than one
result whose starting point nobody remembers.

If several modes fit comparably, that is the answer — the data does not
distinguish them — and it belongs in the paper rather than being resolved by
whichever one you ran last.

### 5. Use the rest of the experiment as a prior

This is where a REF_L sequence has an advantage over a single curve.

- **A sibling measurement.** The same sample in a different contrast, or before
  a treatment, may resolve the layer cleanly. Carry that value in as a fixed or
  tightly-bounded starting point.
- **A time series.** If a tNR run brackets the state, the layer cannot jump
  discontinuously between the two steady states. A `linear_in_time` constraint
  over the series ties the endpoints together and rules out mode pairs that
  would require an implausible excursion in between.
- **The critical edge.** `nrw data features` reports `Qc` and the SLD it
  implies. That constrains the topmost layer independently of any fringe
  analysis.

## Rationalizations

**"BIC says the layer isn't needed."** BIC compares best achievable fits. If
the complex model was not optimised well, BIC is comparing a good fit against a
bad one and the conclusion is about the optimizer.

**"The uncertainty is small, so the value is good."** An optimizer reports the
curvature of the local minimum it found. It cannot report the existence of
another minimum somewhere else on the ridge. Only sampling can.

**"Both fits give χ² near 1, so either is fine."** They are not
interchangeable if they imply different physics — a 20 Å dense oxide and a 60 Å
hydrated one are different claims about the sample.

**"I'll just widen the bounds and refit."** Wider bounds do not help an
optimizer cross a barrier. They usually make it worse by adding unphysical
territory to explore.

**"The layer must be there, so I'll fix its thickness at the literature value."**
Defensible, but then say so: it is an assumption, not a measurement, and the
uncertainty on everything downstream inherits it.

## Red Flags

- A thin layer reported with a precise SLD *and* a precise thickness from an
  amoeba fit. One of those numbers is not measured.
- An adhesion layer or oxide at exactly its lower thickness bound.
- Any parameter railed to a bound in the model that "lost" a BIC comparison.
- A thin-layer SLD outside the range of anything the sample could be made of.
- Two committed specs differing only in a thin layer's starting value, with
  materially different fitted structures and comparable χ².
- Roughness larger than about half the layer it bounds — the layer is being
  smeared out of existence.

## Verification

```bash
nrw model preview <spec>          # how many free parameters, and their bounds
nrw fit run <script> --method dream --samples 100000 --burn 10000
```

Then, on the fit page or the `-err.json`:

1. **Look at the (ρ, t) correlation** for every thin layer. A ridge means quote
   the product.
2. **Check nothing is at a bound.** `nrw serve` shows the parameter table with
   its 68% intervals; an interval clipped at a bound is not an interval.
3. **Run at least two SLD modes** for any layer under the resolution limit and
   compare with `nrw diff`. If they disagree structurally at similar χ², the
   degeneracy is real and unresolved — report it rather than picking one.
