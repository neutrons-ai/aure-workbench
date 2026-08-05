---
name: nrw-model-spec
description: >
  Write an nrw-model/1 spec: one layer stack, any number of steady-state
  states and time-resolved series, with the parameter sharing stated rather
  than hand-coded.
  USE FOR: creating or editing a model, deciding how a parameter should be
  shared, expressing multi-angle co-refinement, debugging a validation error.
  DO NOT USE FOR: choosing a constraint form for a time series (see
  tnr-functional-constraints) or reflectometry physics (see
  neutron-reflectometry).
version: 2
metadata:
  facility: SNS
  instruments: [REF_L, BL-4B]
  techniques: [reflectometry, time-resolved]
  tags: [model, spec, schema, corefinement, refl1d, yaml]
---

# Writing an nrw-model/1 Spec

## Overview

A spec declares a model; `nrw model generate` writes the refl1d script. You
never write the Python — that is deliberate. A generated script can be checked
against the spec, and hand-written co-refinement is where the errors live: the
reference this replaces is 343 lines, about 200 of them copy-pasted parameter
tying, with another user's home directory baked into one variant and a
`# DON'T FORGET TO UPDATE THE DATA DIRECTORIES ABOVE` comment at the bottom.

The equivalent spec is 62 lines and produces a numerically identical problem.

Three ideas carry the design.

**One stack, many states.** Declare the layers once; states instantiate them.

**Parameter identity is the primitive.** Every slot — `(state, measurement,
attribute)` — declares *which parameter object* it reads, with `per`:

| `per` | one parameter per… | use for |
|---|---|---|
| `model` | the whole problem | a solvent SLD that cannot change |
| `state` | each state or series | most structural parameters |
| `measurement` | each angle segment or time slice | per-segment normalisation |

**Within a state, angle segments share automatically.** `per: state` means one
parameter that every segment of that state reads. You never write the aliasing,
and that default alone removes ~180 lines from the reference.

**A series is a state with a time axis.** Adding `series:` and `constraints:`
is what makes time-resolved data first-class.

## When to Use

Whenever creating or changing a model. Also when a validation error is unclear
— the messages point at the rule, and the rules are here.

## Process

### 1. Start from the sample

`nrw model new <SAMPLE> --name <model>` scaffolds a spec from `sample.yaml`.
Or copy [assets/example-corefine-tnr.yaml](assets/example-corefine-tnr.yaml),
which is the full worked case: two OCV states, three angle segments each, and
fifteen tNR slices constrained between them.

### 2. Declare the stack, ambient first

```yaml
materials:
  THF: {rho: 6.2}
  Cu:  {rho: 6.3}
  Si:  {rho: 2.07}
stack:                                # ambient -> substrate
  - {name: THF, material: THF, thickness: 0,   roughness: 25}
  - {name: Cu,  material: Cu,  thickness: 500, roughness: 5}
  - {name: Si,  material: Si}
```

The thickness and roughness here are the **starting values**. `range` says
where a parameter may go; the stack says where it starts.

### 3. Set the probe convention

```yaml
probe: {resolution: moderator, dq_is_fwhm: true}
```

- `dq_is_fwhm: true` — the 4th column of every REF_L file is FWHM. Getting this
  wrong scales all resolution by 2.355.
- `resolution: moderator` uses the SNS emission-time polynomial, which is what
  the existing REF_L scripts do. `angular_only` sets dL = 0. **Pick the one your
  previous fits used**, or new results will not be comparable with old ones.

### 4. Declare the measurements

```yaml
states:
  - {name: ocv1, run: 218386, segments: auto, thetas: [0.45, 1.2, 3.5],
     data_dir: samples/Sample6/data/steady}
series:
  - name: tnr
    run: 218389
    reduced_dir: samples/Sample6/data/tnr/218389
    theta: 0.6
    time_from: reduction_json          # or `filename` for r<run>_t<sec>.txt
    select: {labels: ["sequence_*_eis_*"]}
```

`segments: auto` globs `REFL_<run>_<i>_*_partial.txt` per angle and fails if a
file is missing or ambiguous — which is the check that replaces the
DON'T-FORGET comment.

### 5. Declare the parameters

```yaml
parameters:
  - {path: THF.rho, range: [4, 7], per: model}          # never changes
  - {path: Cu.thickness, range: [400, 600], per: state, in: [ocv1, ocv2]}
  - {path: probe.intensity, value: 1.0, pm: 0.1, per: state}
```

Paths are `Layer.attr` where `attr` is `thickness`, `roughness`, `rho` or
`irho`, plus `probe.{intensity, background, theta_offset, sample_broadening}`.
Note `roughness`, not refl1d's `interface`.

`in:` restricts to named groups. It also accepts a **single measurement** as
`group#index`, and a measurement-level declaration outranks a group-level one:

```yaml
  - {path: probe.intensity, value: 1.0, pm: 0.1, per: state}
  - {path: probe.intensity, range: [0.5, 1.1], per: measurement, in: [ocv1#2]}
```

That says "the segments share a normalisation, except the 3.5° one" — a real
pattern, and exactly what the hand-written reference does.

### 6. Constrain the series

See `tnr-functional-constraints`. In short:

```yaml
constraints:
  - series: tnr
    form: linear_in_index
    from: ocv1
    to: ocv2
    paths: ["*.roughness", CuOx.thickness, Ti.rho]
```

### 7. Check before you fit

```bash
nrw model validate spec.yaml    # paths, layers, bounds, missing files, DOF
nrw model preview  spec.yaml    # the parameter table and how it is grouped
nrw model preview  spec.yaml --build   # also builds it and reports chisq
nrw model generate spec.yaml
```

`preview` is the one that saves time: it says *47 free parameters, 1183 data
points* before you spend an hour on DREAM.

## Rationalizations

| Excuse | Rebuttal |
|---|---|
| "Editing the generated `.py` is quicker than editing the spec." | It is, once. Then the spec and the script disagree, `nrw check` flags it, and you are back to not knowing which file is real. Edit the spec, or `nrw model fork` to take ownership properly. |
| "I'll add a `USE_OXIDE = True` flag so one file covers both cases." | A toggle inside a model means the file no longer identifies what it produced. Two specs, two hashes, two records. The schema deliberately cannot express it. |
| "I'll set `per: measurement` everywhere, it's the most flexible." | It multiplies your parameter count by the number of segments and lets the fit absorb noise. `per: state` is right for structure; reach for `measurement` only where the physics differs. |
| "The validator complains a constraint anchors on a constant — I'll just fix the bounds." | Read it again: it means the endpoint state has no free parameter for that path, so the interpolation would run between two fixed numbers and freeze every slice. That looks like a working fit and is not one. |
| "`post_build` is easier than working out the schema." | It is supported, but every use is flagged, and it is a report that the schema is missing something real. Say what you needed. |
| "I'll copy the spec and change a number for the variant." | Good — that is the intended workflow. Two files, two hashes, two sets of results. |

## Red Flags

- A path both listed in `parameters` and named in a constraint's `paths` for
  the same measurement. Rejected, because whichever ran last would silently win.
- `linear_in_index` on a series whose slices are unevenly spaced in time.
- A parameter with no `range`, `pm`, or `fixed` — rejected, because it would be
  silently frozen.
- `resolution` changed from what earlier fits on this sample used.
- Free parameters approaching a tenth of the data-point count.
- A generated `.py` edited by hand; `nrw model generate` refuses to overwrite it.
- An absolute path anywhere in the spec.

## Verification

- [ ] `nrw model validate` reports no errors.
- [ ] `nrw model preview` shows the parameter count and grouping you intended —
      particularly that segments share what they should.
- [ ] `nrw model preview --build` reports a finite starting chi-squared.
- [ ] The generated script's `_check_links()` passes: `python <model>.py`.
- [ ] `resolution` and `dq_is_fwhm` match the convention used for earlier fits.
- [ ] Any constraint's `from`/`to` states have that path free.
