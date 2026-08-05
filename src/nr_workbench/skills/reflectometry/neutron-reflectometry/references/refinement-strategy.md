# Refinement strategy

Reference material for the `neutron-reflectometry` skill. Consult when a fit is
above the acceptance threshold, or when per-segment χ² values are uneven in a
multi-segment co-refinement.

## General priority order

Work down this list. Each step is cheaper and more reversible than the one
after it, and stopping early is common.

1. **Constrain unphysical parameters first.** If a fitted value is far from
   nominal — Ti thickness at 5× its deposited value, say — tighten *that*
   parameter to a physically realistic range before changing anything else. A
   parameter that has wandered is usually acting as a proxy for something the
   model is missing, and pinning it down often reveals what.
2. **Widen bounds on parameters hitting limits.** A pinned parameter is telling
   you the true value lies outside its box. Widen it — but only in the
   physically plausible direction.
3. **Adjust starting values.** Seed from the previous iteration's best fit
   wherever those values are physically reasonable.
4. **Check the ambient SLD.** A fitted ambient that deviates from the expected
   value for the stated solvent is a very common cause of high χ², and it needs
   no structural change at all. Flag it and constrain it. Suspect unstated
   deuteration.
5. **Enable `sample_broadening`** for multi-segment data when the signs below
   are present.
6. **Structural changes are a last resort.** Add or remove a layer only when all
   three hold:
   - χ² is still > 10 after parameter adjustments, **and**
   - residual fringes clearly indicate an unmodelled layer, **and**
   - BIC supports the added complexity.
7. **Never make two structural changes at once.** One layer at a time, so the
   effect of each is attributable.

## Multi-segment co-refinement

Fitting per-angle `_partial` files with angle-based probes unlocks two
probe-level parameters that are unavailable when fitting a combined file.

### `sample_broadening`

An extra angular divergence component, in degrees, added to the Q resolution of
a probe segment. It accounts for sample curvature, waviness, or alignment
issues that broaden features beyond the instrumental resolution.

**Enable when:**

- Per-segment χ² values are uneven, and particularly when the **low-Q segment
  is significantly worse** (χ² more than about 2× the best segment).
- The critical edge is rounder or more smeared in the data than in the model.
- Structural adjustments and intensity normalisation have not resolved the
  per-segment imbalance after one or two iterations.
- Structural parameters are drifting to unphysical values — an adhesion layer
  inflating 5×, an SLD far from nominal. This usually means the fitter is using
  structural parameters as a proxy for missing resolution broadening.

**Do not enable when:**

- Fitting a single combined file. There is no angle information; probes are
  Q-based and the parameter has nothing to act on.
- All segments fit equally well.
- χ² is already below the acceptance threshold.

**Typical range:** 0.0 to 0.5 degrees. Start there and widen only if the fitted
value reaches the upper bound.

### `theta_offset`

A small correction to a segment's incident angle, in degrees, accounting for
sample misalignment or goniometer calibration error.

**Enable when:**

- The fit is poor specifically in the **overlap region** between adjacent
  segments — a visible discontinuity in the stitched data.
- There is a systematic shift between segments that intensity normalisation
  alone cannot explain.

**Do not enable** without clear evidence of angular misalignment.

**Typical range:** −0.02 to 0.02 degrees. This is a small correction; a fitted
value beyond ±0.1° points at a real calibration problem that should be raised
rather than absorbed into the model.

### Priority order for uneven segments

When one segment fits much worse than the others:

1. **Intensity normalisation** — widen the intensity bounds if a segment is
   hitting its limit.
2. **`sample_broadening`** — the most common cause, especially when the low-Q
   segment is worst.
3. **`theta_offset`** — only if the overlap regions show misalignment.
4. **Structural changes** — only if neither of the above resolves it and
   residual fringes indicate a missing layer.
