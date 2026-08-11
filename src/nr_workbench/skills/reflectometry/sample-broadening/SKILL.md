---
name: sample-broadening
description: >
  Decide whether a reflectivity curve is smeared more than the reduction says,
  and attribute that smearing to the right cause before it is absorbed into a
  thickness or a roughness.
  USE FOR: setting probe.sample_broadening and choosing its `per:` scope,
  diagnosing a low-angle segment that fits worse than the others, deciding
  whether damped fringes are instrumental or structural, judging whether a
  fitted roughness is carrying a resolution error.
  DO NOT USE FOR: the angular-only convention and the dQ column itself (see
  refl-bl4b-instrument), or whether a thin layer is resolvable at all (see
  thin-layer-degeneracy).
version: 2
metadata:
  facility: SNS
  instruments: [REF_L, BL-4B]
  techniques: [reflectometry]
  tags: [sample_broadening, resolution, divergence, aperture, smearing, roughness, fringe-contrast, probe]
---

# Sample broadening and where extra smearing really comes from

## Overview

The reduction writes one number for the Q resolution — `dq_over_q` on the
`# Meta:` line, as FWHM — and the angular-only convention turns it into an
angular divergence. That number is a **calculation from nominal settings**, not
a measurement. When the data are smeared more than it says, the fit will find
somewhere to put the difference, and the place it usually puts it is a
roughness or a layer thickness. You then quote a structural number that is
carrying an instrumental error, with a tight interval on it.

`probe.sample_broadening` is the parameter that gives that extra smearing its
own home. It adds an angular width ω, in degrees, to the segment's divergence.

The whole difficulty is that **several different physical causes produce extra
smearing, and only some of them are the instrument**. They are separable, but
only by a test that most people never run. Section 2 is that test.

## When to Use

Reach for this when:

- one segment — almost always the lowest angle — fits materially worse than
  the others;
- the critical edge is rounder in the data than in any model you can fit;
- the model's Kiessig fringes are deeper than the data's;
- a roughness has railed on a bound, or is larger than half the layer it
  bounds, and you are about to decide what that means;
- you are choosing the `per:` scope for `probe.sample_broadening`.

Not for the angular-only convention itself, and not for deciding whether a
thin layer exists.

## Process

### 1. On BL-4B, sample broadening is per segment, not per model

The tempting reading is that ω is a property of the sample — its curvature or
waviness — and therefore one number for the whole problem. On this beamline
that reading is usually wrong.

The dominant contribution here is a **beam-definition / aperture effect that
only bites at very small incident angles**. At 0.37° the illuminated footprint
is long, the defining apertures no longer clip the beam the way the nominal
geometry assumes, and the accepted angular spread is much wider than the slit
calculation predicts. By 3.5° the effect has gone.

That has a hard consequence for the spec. Because refl1d adds ω to the angular
divergence, a *single shared* ω translates into a relative Q resolution

```
dQ/Q  =  (dθ_nominal + ω) / tan θ
```

which is **not** the same at every angle — it blows up as θ → 0. One shared ω
fitted against three segments therefore lands on a compromise: it over-smears
the low-angle segment and leaves the high-angle segments under-smeared, or the
reverse. Neither is what any physical cause looks like.

So, on BL-4B:

```yaml
  # aperture-limited broadening: an effect of the incident angle, not of the
  # sample, so each angle segment gets its own.
  - {path: probe.sample_broadening, range: [0.0, 0.15], per: measurement}
```

Expect the fitted values to be **largest at the lowest angle and near zero at
3.5°**. That pattern is the signature of the aperture effect and is the reason
to believe the parameter rather than suspect it.

If instead the fitted ω comes out roughly proportional to `tan θ`, that is not
an aperture effect at all — it is a *constant relative* resolution `dQ/Q`, i.e.
the reduction's `dq_over_q` is simply too small. Say so in the note; it is a
reduction bug, not a sample property, and it belongs in
`docs/ground_truths.md`.

**`per: model` is only right** when you have independent reason to believe the
cause is sample curvature or mosaic — a bent wafer, a pressed cell window —
because those genuinely are one angular width shared by every segment.

### 2. Separate instrumental smearing from structural damping

Both broaden features. They are distinguishable, and the test is cheap.

Extra smearing and extra structural disorder damp the fringe contrast with
different dependences:

| Cause | Damping of fringe amplitude | Depends on |
|---|---|---|
| Aperture / curvature (fixed ω) | `exp(−(ω d / tan θ · Q)² / 2)` | **angle** — worst at low θ |
| Moderator emission time (`dλ/λ ∝ 1/λ`) | grows toward short λ | **angle**, at fixed Q |
| Constant relative resolution (`dQ/Q = f`) | `exp(−(f Q d)² / 2)` | **Q only** |
| Interfacial width σ | `exp(−Q² σ²)` | **Q only** |
| Lateral thickness distribution Δd | `exp(−(Q Δd)² / 2)` | **Q only** |

The discriminating measurement is the **overlap region between two adjacent
segments**. Two segments at different incident angles cover the same Q there.
Measure the ratio of the data's fringe amplitude to the model's, *independently
in each segment, inside the shared Q window*:

```python
# in the shared window: regress log10(data) on [1, Q, Q^2, A],
# where A is the model's own fringe modulation (log10 theory minus its
# smooth part).  The coefficient on A is the amplitude ratio k.
# k = 1 -> the model reproduces the fringe depth
# k < 1 -> the model's fringes are too deep
```

- **k differs between the two angles** → the cause depends on incident angle.
  It is instrumental: `sample_broadening`, or a moderator term the angular-only
  convention discards.
- **k agrees between the two angles** → the cause is a function of Q alone.
  `sample_broadening` will not fix it, and forcing it to try will corrupt the
  low-angle segment. Look at interfacial width, a lateral thickness
  distribution, or a relative-resolution floor.

Run this **before** widening a roughness bound and before adding a broadening
term. It costs one pass over the `*-refl.dat` files and it is the difference
between attributing a real effect and hiding it.

### 3. Do the normalisation first

An uneven low-angle segment has two common causes and broadening is only one of
them. A segment whose overall scale is wrong tilts the fit, and the fit answers
with smearing.

Order of work:

1. **Per-segment scale.** Check the overlap ratio between neighbouring
   segments (`nrw data overlap`). A flat-in-Q offset is a scale error: give
   that segment its own `probe.intensity` with `per: measurement`.
2. **Scale that varies across the segment.** If the required scale changes with
   wavelength *within* one segment — typically at the short-λ edge of the band
   — no intensity parameter can absorb it. Trim the band or re-reduce. Treating
   it as broadening will inflate ω and damp every fringe in the problem.
3. **`theta_offset`**, if the overlap shows a fringe-position shift rather than
   an amplitude one.
4. **Then** broadening.

### 4. Sanity-check the fitted value against the critical edge

Where a total-reflection plateau exists, ω is directly measurable and does not
need to be inferred from χ². Below Q_c the reflectivity is exactly 1; the
plateau's observed roll-off *before* Q_c is resolution, and nothing else.

A plateau that is already 10% down at 0.9 Q_c implies a total FWHM `dQ/Q` of
roughly 0.1 — several times the typical reduced `dq_over_q`. If the fitted ω
implies far less smearing than the edge shows, something else in the model is
absorbing it. If it implies far more, ω is soaking up a scale error instead.

## Rationalizations

**"Broadening is a sample property, so `per: model` is more physical."** Only if
the cause is the sample. On BL-4B the dominant cause is the incident-angle
aperture effect, and one shared ω cannot represent it — it necessarily
mis-smears at least two of the three segments.

**"The low-Q segment fits worst, so it needs broadening."** It is the most
likely candidate, and it is also the segment most likely to be mis-normalised,
because it has the longest footprint and the weakest direct beam. Check the
overlap first. Broadening applied to a scale error damps fringes everywhere.

**"I'll widen the roughness bounds; the fit will sort out which it is."** It
will not. Roughness and a relative-resolution term damp fringes with the same
`exp(−cQ²)` form over any realistic Q range. The fit picks whichever is
cheaper, silently, and the interval it reports is the interval of the wrong
parameter.

**"`sample_broadening` at 0.0 is the same as not declaring it."** It is not.
Declaring it changes the resolution path refl1d takes, so two otherwise
identical specs are not numerically comparable.

**"ω came out large, so the sample is badly bent."** Only if it came out large
*at every angle*. Large at 0.37° and zero at 3.5° is the aperture effect.
Proportional to `tan θ` is a wrong `dq_over_q` in the reduction.

## Red Flags

- `probe.sample_broadening` declared `per: model` on multi-angle partials from
  BL-4B, with no stated reason to think the cause is sample curvature.
- A fitted ω that implies a relative resolution *smaller* at the lowest angle
  than at the highest — the aperture effect cannot do that.
- A shared ω on its upper bound while one segment still fits worse than the
  others: the parameter is being used as a scale factor.
- Fringe contrast mismatched by more than ~20% with no overlap-window test run.
- A roughness quoted with a tight interval on a fit where the fringe-amplitude
  ratio was never measured.
- ω large enough to be a visible fraction of the incident angle (say > 10%)
  with no corresponding roll-off in the observed critical edge.

## Verification

- [ ] The overlap ratio between every pair of adjacent segments was checked for
      a flat-in-Q offset **before** broadening was enabled.
- [ ] The required scale is constant across each segment's wavelength band; any
      band edge that is not was trimmed, not absorbed.
- [ ] `per:` scope is `measurement` unless a sample-level cause is named in the
      spec comment.
- [ ] The fitted ω decreases with increasing incident angle, or the deviation
      from that pattern is explained in the note.
- [ ] The fringe-amplitude ratio was measured in a shared Q window in both
      segments, and the Q-only vs angle-dependent verdict is recorded.
- [ ] Where a total-reflection plateau exists, the fitted resolution is
      consistent with the observed edge roll-off.
