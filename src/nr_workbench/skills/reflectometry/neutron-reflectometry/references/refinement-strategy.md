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
a probe segment. Full treatment in the **`sample-broadening`** skill, including
the test that separates instrumental smearing from structural damping. The short
version:

**Scope it `per: measurement`, not `per: model`.** On BL-4B the dominant cause is
a beam-definition/aperture effect that only bites at very small incident angles,
so it is a property of the *angle*, not of the sample. refl1d adds ω to the
divergence, giving `dQ/Q = (dθ_nominal + ω) / tan θ`, which blows up as θ → 0 — so
one shared ω necessarily over-smears the lowest-angle segment or under-smears the
rest. Use `per: model` only with independent reason to think the cause is sample
curvature or a pressed cell window, which genuinely are one width for all angles.

**Enable when:**

- The critical edge is rounder or more smeared in the data than in the model.
- Per-segment χ² values are uneven — **but check normalisation first** (see the
  priority order below). An uneven low-angle segment is at least as often a scale
  error, and broadening applied to a scale error damps every fringe in the fit.
- Structural parameters are drifting to unphysical values — an adhesion layer
  inflating 5×, an SLD far from nominal. This usually means the fitter is using
  structural parameters as a proxy for missing resolution broadening.

**Do not enable when:**

- Fitting a single combined file. There is no angle information; probes are
  Q-based and the parameter has nothing to act on.
- All segments fit equally well.
- The fringe *contrast* is wrong but equally so at equal Q in two overlapping
  segments. That is a Q-only effect — interfacial width, a lateral thickness
  distribution, or a relative-resolution floor — and no angular broadening can
  express it. Run the equal-Q test in `sample-broadening` before reaching here.

**Typical range:** `[0.0, 0.15]` per segment. The older 0.5 was for a single
shared value; per segment the lowest angle carries most of it and the highest
should come out near zero.

**Check the fitted values against each other.** They should *decrease* with
increasing incident angle — that pattern is the signature of the aperture effect
and the reason to believe the parameter. If they instead scale as `tan θ`, the
cause is a constant relative `dQ/Q`, i.e. the reduction's `dq_over_q` is too
small: a reduction bug for `docs/ground_truths.md`, not a sample property. And
where a total-reflection plateau exists, the roll-off before Q_c measures the
resolution directly — check against that rather than inferring it from χ².

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

1. **Per-segment scale.** Check the overlap ratio against the neighbouring
   segment (`nrw data overlap`). A flat-in-Q offset is a normalisation error, not
   physics: give that segment its own `probe.intensity` with `per: measurement`.
   Do this first — it is the cheapest and the most often the answer.
2. **Scale that varies *within* a segment.** If the required scale changes with
   wavelength across one segment — typically at the short-λ edge of the band — no
   intensity parameter can absorb it. Trim the band or re-reduce. Treating it as
   broadening inflates ω and damps every fringe in the problem.
3. **`sample_broadening`**, `per: measurement`.
4. **`theta_offset`** — only if the overlap shows a fringe-*position* shift
   rather than an amplitude one.
5. **Structural changes** — only if none of the above resolves it and residual
   fringes indicate a missing layer.

Steps 1 and 2 come before 3 because the fit cannot tell a scale error from a
resolution error: both make one segment fit worse, and it will spend whichever
parameter you left free.
