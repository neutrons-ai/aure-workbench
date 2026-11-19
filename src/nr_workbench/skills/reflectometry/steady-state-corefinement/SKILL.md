---
name: steady-state-corefinement
description: >
  Co-refine several steady-state measurements of one sample so that what is
  physically shared is shared exactly and what changed is free.
  USE FOR: deciding which parameters to tie across states, setting up a
  multi-angle or multi-contrast fit, diagnosing states that disagree about the
  substrate, choosing between per model, per state and per measurement.
  DO NOT USE FOR: time-resolved series (see tnr-functional-constraints) or
  reviewing a hand-written script (see refl1d-script-review).
version: 2
metadata:
  facility: SNS
  instruments: [REF_L, BL-4B]
  techniques: [reflectometry, co-refinement]
  tags: [corefinement, states, shared-parameters, multi-angle, contrast, spec]
  source:
    repo: neutrons-ai/aure
    path: src/aure/skills/multi-state-corefinement/SKILL.md
    adaptation: >
      The tying decisions are AuRE's; the mechanism is not. AuRE configures
      shared_parameters/unshared_parameters lists; nrw-model/1 declares each
      parameter's scope with `per:`, and angle segments alias automatically.
      Rewritten around that, plus the per-measurement override the REF_L data
      turned out to need.
---

# Co-refining steady states

## Overview

Several measurements of one sample share a structure. Co-refinement means
saying which parts are shared, so the data constrains one set of numbers rather
than several independent ones.

In `nrw-model/1` every parameter declares a scope, and that is the whole system:

| `per:` | One value per | Use for |
|---|---|---|
| `model` | the whole problem | substrate SLD, materials that cannot change |
| `state` | each steady state | anything the experiment changed |
| `measurement` | each angle segment or slice | nuisance that is genuinely per-file |

**Angle segments alias automatically.** A state with `segments: auto` becomes
three Experiments sharing one set of structural parameters. You never write the
tying, which is the part that goes wrong by hand.

**Scope shares a value; a stack shares a structure.** `per:` says whether a
layer's thickness is one number or several. Whether the layer is *there* at all
is a different question, answered by the stack: a state whose structure differs
declares its own complete `stack`, and parameters are shared across stacks by
layer name.

## When to Use

- Two or more steady states of one sample: before and after a treatment, two
  contrasts, two potentials.
- Any multi-angle REF_L measurement — that is co-refinement even with one state.
- When states disagree about something that cannot have changed.
- Before adding a free parameter, to check the scope you actually want.

## Process

### 1. Decide what cannot have changed

Start from physics, not from what improves χ².

**Almost always `per: model`:**

- substrate SLD — silicon is silicon;
- material SLDs of anything not chemically altered;
- an adhesion layer buried under the electrode.

**Almost always `per: state`:**

- anything the experiment did: the layer under study, its roughness;
- probe intensity — a separate normalisation per measurement;
- anything in contact with a changed ambient.

**`per: measurement` is rare and deliberate.** It is for a genuine per-file
nuisance, not for structure. If you find yourself wanting a per-measurement
*thickness*, the model is wrong.

**A layer present in one state and not another gets that state's own stack.**
A native oxide reduced away under potential is the common case. Never fake it
by keeping the layer and pinning its SLD at the copper's: write the state's
stack without it (`nrw-model-spec` has the YAML).

```yaml
states:
  - {name: ocv, run: 230539, segments: auto}       # the top-level stack, with CuOx
  - name: reduced
    run: 230546
    segments: auto
    stack:                                         # its own, without CuOx
      - {name: D2O, material: D2O, thickness: 0, roughness: 5}
      - {name: Cu,  material: Cu,  thickness: 358, roughness: 5}
      - {name: Si,  material: Si}
```

**A layer keeps its name in every stack, because sharing is by name.** The `Cu`
in `reduced`'s stack is the `Cu` of the top-level one, so `Cu.rho` `per: model`
is one copper SLD for both states -- which is what makes the comparison of the
two states a comparison of their oxides. Then `CuOx.*` with no `in:` reaches
only `ocv`, and naming `reduced` for it is refused.

### 2. Write it down

```yaml
parameters:
  # shared across everything
  - {path: Cu.rho,   range: [5.0, 7.0], per: model}
  - {path: Ti.rho,   range: [-4.0, -1.0], per: model}

  # one per state -- these are what the experiment changed
  - {path: CuOx.thickness, range: [10, 120], per: state, in: [ocv1, ocv2]}
  - {path: CuOx.roughness, range: [3, 33],   per: state, in: [ocv1, ocv2]}

  # nuisance
  - {path: probe.intensity, value: 1.0, pm: 0.1, per: state}
```

`in:` restricts a declaration to named groups. It matters when a series is
present: `per: state` defaults to *every* group including the series, which
collides with any constraint that owns the same path.

### 3. Override one measurement when the data says to

Sometimes one segment needs its own value. `nrw data overlap` finds these:

```
  ! #2 / #3   +27.58%  (14.8 sigma)
```

Two angle settings measuring the same sample disagree by 27.6% where they
overlap. That is a normalisation error, and the honest response is to give that
one segment its own intensity:

```yaml
  - {path: probe.intensity, value: 1.0, pm: 0.1, per: state}
  - {path: probe.intensity, range: [0.5, 1.1], per: measurement, in: [ocv1#2],
     name: Intensity_386_3}
```

`ocv1#2` targets the third segment of `ocv1`. Measurement-level targeting
outranks the state-level declaration above it, so the general rule stays and the
exception is explicit and visible in the diff.

The alternative — leaving it — means a copper thickness absorbs the scale error.

### 4. Check the parameter table before fitting

```bash
nrw model preview <spec>
```

```
  21 experiment(s) in 3 group(s)
    ocv1           3 measurement(s)
    ocv2           3 measurement(s)
    tnr           15 measurement(s)

  21 free parameter(s), 90 constrained
    Cu.rho@model                   6.3   [5, 7]
    CuOx.thickness@ocv1             60   [10, 120]
    CuOx.thickness@ocv2             60   [10, 120]
    probe.intensity@ocv1#2           1   [0.5, 1.1]
```

The `@` suffix is the scope. Read the whole table once: a parameter appearing
per-state that you meant to share shows up here, and nowhere else until the
answer is wrong.

### 5. Let disagreement be informative

If two states fit to different substrate SLDs, something is wrong — but the
useful move is to find out *what* before tying it. Free it deliberately, look at
how far apart they land, then decide. A parameter tied to hide a discrepancy
still has the discrepancy in it.

## Rationalizations

**"Tie everything, it's more constrained."** Tying something that did change
forces the error into whatever is still free, usually a roughness.

**"Free everything, let the data decide."** The data cannot decide. Each state
has limited Q range and freeing shared structure multiplies the degeneracy
described in `thin-layer-degeneracy`.

**"The two states fit better with separate substrates."** They always will. The
question is whether silicon changed, and it did not.

**"I'll tie it later once the fit is stable."** In a hand-written script that is
exactly the edit that breaks aliasing (see `refl1d-script-review`). In a spec it
is free — change `per:` and regenerate.

**"Each angle segment needs its own thickness."** No physical sample has three
thicknesses. If the segments disagree, run `nrw data overlap` — it is a
normalisation problem.

**"I'll keep the oxide in the reduced state and pin it to the copper SLD; it's
equivalent."** Only while every pin holds, and only to a reader who has been
told. The stack still shows an oxide, model.md tables it, and `nrw check`
reports each pin as contradicting the stack. A state `stack` without the layer
is the same problem with nothing to explain.

## Red Flags

- A structural parameter declared `per: measurement`.
- Two states with materially different substrate or adhesion-layer values.
- A free-parameter count you did not predict before running `preview`.
- `per: state` with no `in:` in a spec that also has a `series:` — it silently
  includes the series.
- A per-segment intensity added with no comment saying which measurement
  justified it.
- Angle segments tied by hand in a generated script — they alias automatically,
  so a manual tie means someone did not trust the generator and may have got it
  wrong.
- A layer absent in one state written as a zero-thickness layer with its SLD
  pinned at a neighbour's, instead of that state's own `stack`.
- The same layer under two names in two states' stacks: nothing is shared
  between them.

## Verification

```bash
nrw model validate <spec>     # schema plus semantic checks
nrw model preview <spec>      # the scoped parameter table
nrw model preview <spec> --build   # builds it, reports the initial chi-squared
nrw data overlap <sample>     # do the segments agree before you fit them
```

Four checks:

1. **The free-parameter count matches what you intended.** Predict it, then look.
2. **Every `@scope` is the one you meant.** This is the whole skill in one line.
3. **Each state is built from the stack you meant.** When a state declares its
   own, `nrw model validate` says so in one line --
   `stacks: reduced declares its own; ocv inherits the top-level stack` -- and
   `nrw model preview` lists every stack beside the states built from it.
4. **Generated scripts assert it at runtime.** `_check_links()` compares object
   identity for every group that should be shared, so a broken tie fails loudly
   rather than producing a plausible wrong answer.
