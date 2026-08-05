---
name: tnr-functional-constraints
description: >
  Choose and configure the functional form that ties a time-resolved series'
  parameters to its steady-state endpoints.
  USE FOR: deciding between linear, logistic, exponential, piecewise, free or
  fixed for a tNR series; deciding which parameter to free; judging how many
  degrees of freedom the data supports.
  DO NOT USE FOR: writing the rest of a spec (see nrw-model-spec) or assessing
  whether the run changed at all (see tnr-change-assessment).
version: 2
metadata:
  facility: SNS
  instruments: [REF_L, BL-4B]
  techniques: [reflectometry, time-resolved]
  tags: [tnr, constraints, corefinement, logistic, time-resolved, dof]
---

# tNR Functional Constraints

## Overview

A tNR series has too little counting time per slice to fit independently: fifty
slices with three free structural parameters each is 150 parameters that will
happily absorb noise. The fix is to co-refine the series with the steady-state
measurements either side of it and make each slice's parameters a **function**
of the endpoints, so only the endpoints are fitted.

That is what the hand-written `linear_constraints()` does, and what
`constraints:` declares:

```yaml
constraints:
  - series: tnr
    form: linear_in_index
    from: ocv1                      # the OCV before
    to: ocv2                        # the OCV after
    paths: ["*.roughness", CuOx.thickness, Ti.thickness, CuOx.rho, Ti.rho]
```

Fifty slices then cost **zero** extra parameters for those paths: each is an
expression in `ocv1` and `ocv2`, which were being fitted anyway.

## When to Use

After `nrw tnr assess` and before `nrw model generate`, whenever a spec has a
`series:`. The assessment picks the form and the parameter; this says how to
express the choice and what it costs.

## Process

### 1. Take the recommendation from the assessment

`assessment.json` already answers both questions:

| field | tells you |
|---|---|
| `amplitude.trajectory` | which **form** fits |
| `template.implied_change` | which **parameter** to free |

| trajectory | form |
|---|---|
| `monotonic` | `linear_in_time` |
| `sigmoidal` | `logistic` |
| `flat` | none — do not fit a time dependence |
| `non-monotonic` | `piecewise_linear`, or reconsider one template |

`implied_change: thickness` means free a thickness; `sld_contrast` means free
an SLD. Freeing the wrong one produces a fit that converges and means nothing.

### 2. Pick the form

| form | cost | when |
|---|---|---|
| `linear_in_index` | 0 | slices evenly spaced **and** you are reproducing an existing fit |
| `linear_in_time` | 0 | steady change; the right default |
| `logistic` | 2 (`t_half`, `width`) | induction period, rise, plateau |
| `exponential` | 1 (`tau`) | first-order or diffusive approach |
| `piecewise_linear` | K | structure no closed form captures |
| `free` | N | last resort; justify it |
| `fixed` | 0 | pinned to one state throughout |

`nrw model forms` lists them.

**Prefer `linear_in_time` over `linear_in_index`.** They differ whenever slice
spacing is uneven, which is the normal case: `hold` intervals run 30 s and
`eis` intervals ~85 s, so by index the middle slice sits at 0.5 while by time
it may sit at 0.9. Use `linear_in_index` only to reproduce an existing fit.

`nrw model validate` warns when spacing varies by more than 3× and the
constraint uses `linear_in_index`.

### 3. Anchor it

`from` and `to` name the steady states either side. Both must have that path
declared **free** — otherwise the interpolation runs between two constants and
silently freezes every slice. That is rejected rather than warned about,
because the resulting fit looks fine.

```yaml
parameters:
  - {path: CuOx.thickness, range: [10, 125], per: state, in: [ocv1, ocv2]}
constraints:
  - {series: tnr, form: linear_in_time, from: ocv1, to: ocv2,
     paths: [CuOx.thickness]}
```

### 4. Free the one parameter that genuinely moves

Constrain everything, then free exactly the parameter the template implicates:

```yaml
  - {path: Cu.thickness, per: measurement, in: [tnr], init: stack, pm: 100}
```

That is 15 parameters for 15 slices — affordable only because everything else
costs nothing.

### 5. Fit the transition as physics, not as a reading off a plot

With `logistic`, `t_half` and `width` come out of the fit **with
uncertainties**. That is the payoff of the amplitude analysis: `tnr-amplitude`
reports run 218389's a(t) as sigmoidal with an induction period, and this turns
that observation into measured numbers.

```yaml
  - series: tnr
    form: logistic
    from: ocv1
    to: ocv2
    t_half: [0, 4000]        # bounds, in seconds
    width: [30, 1000]
    paths: [CuOx.thickness]
```

### 6. Count the cost before fitting

```bash
nrw model preview spec.yaml
```

## Rationalizations

| Excuse | Rebuttal |
|---|---|
| "I'll use `free` — let the data decide." | With 50 slices that is 50 parameters against data that cannot support 3. It will converge, and the trajectory you read off it will be noise. |
| "`linear_in_index` is what the old script used, so it's fine." | It is fine *for reproducing that fit*. For new work with uneven spacing it puts slices at the wrong fraction. Use `linear_in_time`. |
| "A linear ramp is the safe default." | Only if the amplitude says `monotonic`. Forcing a line through a sigmoid hides the induction period and the completion — both of which are the physics. |
| "I'll constrain everything including the parameter that changes." | Then nothing is free to move and the fit will push the endpoints to absurd values to compensate. Constrain the many; free the one. |
| "`logistic` has more parameters so it fits better." | Two more, and they are physically meaningful. Use it when the trajectory *is* sigmoidal, not to improve chi-squared. |
| "The endpoints aren't free but the interpolation still runs." | It runs between two constants, so every slice is frozen at a fixed value. This is rejected for exactly that reason. |

## Red Flags

- A form contradicting `amplitude.trajectory`.
- A freed parameter contradicting `template.implied_change`.
- `linear_in_index` where slice spacing varies by more than ~2×.
- `free` used without a stated justification.
- Free parameters exceeding about a tenth of the data points.
- A constraint on a run whose variogram was flat — nothing changed, so there is
  no trajectory to model.
- `t_half` bounds that do not span the run, or `width` bounds narrower than the
  slice spacing.

## Verification

- [ ] The form matches `amplitude.trajectory` from the assessment.
- [ ] The freed parameter matches `template.implied_change`.
- [ ] `from` and `to` both declare that path free — `nrw model validate` checks it.
- [ ] `nrw model preview` shows the parameter count you expected, with the
      series contributing only what you intended.
- [ ] No `linear_in_index` warning from `nrw model validate`.
- [ ] After fitting, any `t_half`/`tau` sits inside its bounds rather than
      pinned at one.
