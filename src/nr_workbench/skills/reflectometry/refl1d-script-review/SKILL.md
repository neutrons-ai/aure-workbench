---
name: refl1d-script-review
description: >
  Review a hand-written refl1d co-refinement script for the errors that
  produce a confident wrong answer rather than a crash.
  USE FOR: checking a script before running it, reviewing someone else's model,
  diagnosing a fit that converged to something implausible, deciding whether a
  script is safe to keep hand-maintaining.
  DO NOT USE FOR: writing a new model (use nrw-model-spec, which makes most of
  these structurally impossible) or interpreting a completed fit.
version: 2
metadata:
  facility: SNS
  instruments: [REF_L, BL-4B]
  techniques: [reflectometry, co-refinement]
  tags: [refl1d, review, aliasing, parameters, corefinement, bumps, pitfalls]
  source:
    repo: mdoucet/experiments-2025
    path: jen-oct2025/models/Cu-THF-223918-full-sequence.py
    adaptation: >
      Written from the failure modes present in the hand-written co-refinement
      scripts this package replaces. The aliasing pitfall is the one that has
      produced wrong results in practice.
---

# Reviewing a hand-written refl1d script

## Overview

A co-refinement script ties one set of physical parameters across many
Experiments. The errors that matter are not the ones that raise — those you
find in seconds. They are the ones that leave you with a converged fit, a
plausible χ², and a number that is wrong.

Almost all of them are the same error in different clothes: **two things you
believe are one parameter are actually two**, or the reverse.

`nrw model generate` makes these structurally impossible by construction. This
skill is for the scripts that came before it, and for the forks that will
always exist.

## When to Use

- Before running any script you did not generate.
- When a fit converges but a layer thickness moved somewhere implausible.
- When two states that should share a substrate disagree about it.
- Before trusting a result from a script that has been copy-edited — a
  `-copy.py`, a `-test.py`, a `-v2.py`.
- After `nrw model fork`, whenever you edit the fork.

## Process

Work down this list. The first two find most real errors.

### 1. The aliasing pitfall

This is the one that has caused wrong results in practice.

Co-refinement is expressed by making several Experiments share one `Parameter`
*object*:

```python
exp[1].sample["Cu"].thickness = exp[0].sample["Cu"].thickness
exp[2].sample["Cu"].thickness = exp[0].sample["Cu"].thickness
```

Once that has run, `exp[0]`, `exp[1]` and `exp[2]` all reference the same
object. **Reassigning `exp[0]` afterwards does not propagate** — it rebinds
only `exp[0]`, and 1 and 2 keep pointing at the old object:

```python
# ... 100 lines later, "adjusting the starting value"
exp[0].sample["Cu"].thickness = Parameter(value=520, name="Cu_thickness")
# exp[1] and exp[2] still hold the ORIGINAL parameter. The fit now has two
# free Cu thicknesses where the model says one, and nothing says so.
```

The fit runs. It converges. The degrees of freedom are wrong and the answer is
wrong.

**How to check.** Compare object identity, not value:

```python
shared = [e.sample["Cu"].thickness for e in exp]
assert len({id(p) for p in shared}) == 1, "Cu thickness is not shared"
```

A generated script ends with exactly these assertions (`_check_links()`). Add
them to any hand-written script you intend to keep.

**How to avoid it.** Create every free parameter once, up front, then assign
outward. Never read a parameter off one Experiment to give to another.

### 2. Order of assignment vs. tying

Tying and value-setting are order-dependent, and the safe order is: create,
tie, *then* set values through the shared object.

```python
p = Parameter(value=500, name="Cu_thickness")
p.range(400, 600)
for e in exp:
    e.sample["Cu"].thickness = p  # every Experiment now shares p
p.value = 520  # setting through p reaches all of them
```

Setting `exp[3].sample["Cu"].thickness.value = 520` also works — it mutates the
shared object. Setting `exp[3].sample["Cu"].thickness = 520` does **not**: it
replaces the reference with a float and quietly unties that Experiment.

### 3. What is free, and is it what you meant

```python
print(len(problem.getp()))
for p in problem.parameters():
    print(p.name, p.value, getattr(p, "bounds", None))
```

Three things to look for:

- **Duplicate names.** Two parameters called `Cu_thickness` means the tying
  failed. Names are not enforced unique, so this is a symptom, not an error.
- **A count you cannot explain.** If you expect 21 free parameters and the
  problem reports 24, three things you thought were tied are not.
- **Parameters with no bounds.** An unbounded parameter can wander into
  unphysical territory and drag the rest of the model with it.

`nrw model preview` prints exactly this table from a spec, without running
anything.

### 4. Data paths

```python
DATA = "/Users/jenni/OneDrive/experiments/..."  # someone else's laptop
DATA = os.path.join(os.path.dirname(__file__), "..", "data", "steady", fn)  # fine
```

An absolute path in someone's home directory is the defect this package exists
to remove. It is not portable, and it silently reads a *different* file if the
same path exists on another machine.

### 5. Toggles

```python
free_Ti = True
INCLUDE_TNR = False
```

A boolean at the top of a fit script means the file on disk does not determine
the result. Six months later nothing records which way it was set for the
figure in the paper.

Split them into separate committed scripts, or express them as separate specs.
`nrw fit run` hashes the script, so two settings of a toggle inside one file
produce two different results with **the same** script hash — provenance that
records the wrong thing is worse than none.

### 6. Resolution and the `dQ` column

See `refl-bl4b-instrument`. In short: `dQ` in a REF_L reduced file is FWHM, the
convention here is angular-only (`dL = 0`), and getting either wrong is
absorbed by roughness.

### 7. The bumps export trap

```python
fit(problem, method="dream", export="results/")  # WRONG
```

In bumps 1.0.x this silently skips the entire uncertainty block: no `-err.json`,
no chain, no DREAM diagnostics. It does not warn. Call `export_fit` yourself:

```python
from bumps.webview.server.api import export_fit

result = fit(problem, method="dream", samples=100000)
export_fit("results/", problem, result, basename=problem.name)
```

`nrw fit run` already does this correctly.

## Rationalizations

**"It converged, so it must be right."** Convergence says the optimizer found a
minimum of the function you gave it. If the aliasing is broken, that function
is not the model you described.

**"χ² is fine."** Extra free parameters *improve* χ². A broken tie makes the fit
look better while making the result meaningless.

**"I only changed the starting value."** That is exactly the edit that breaks
aliasing, because it is the one that reassigns a parameter after the ties were
made.

**"The names match, so they're the same parameter."** Names are labels. Two
distinct objects can share one, and refl1d will not complain.

**"It's the same script we've always used."** `-copy.py`, `-test.py` and
`-t0.py` are all "the same script". Check which one produced the figure.

## Red Flags

- Any assignment to `exp[i].sample[...]` or `exp[i].probe...` **after** the
  block that ties parameters together.
- `Parameter(...)` constructed inside a loop over Experiments — that makes one
  per Experiment, which is the opposite of sharing.
- A free-parameter count you have not verified against the model you intended.
- An absolute path containing a username.
- `free_*` or `INCLUDE_*` booleans.
- `export=` passed to `fit()`.
- Two states sharing a substrate that fit to visibly different substrate SLDs.
- A copy-suffixed filename with no note saying how it differs.

## Verification

For a script you intend to keep:

```bash
nrw fit run <script.py> --method amoeba --steps 1 --dry-run
```

That loads it, reports the free-parameter count and the initial χ², and records
nothing. Compare the count against what you intended before running a real fit.

Then add identity assertions for every group you believe is shared:

```python
def _check_links():
    for name, group in [("Cu.thickness", [e.sample["Cu"].thickness for e in exp])]:
        ids = {id(p) for p in group}
        assert len(ids) == 1, f"{name}: {len(ids)} objects, expected 1"


_check_links()
```

Cheap, runs every time the script does, and turns the invariant into something
that fails loudly instead of quietly.

The stronger option is not to hand-maintain the tying at all: write a spec and
let `nrw model generate` emit it. The generated script creates every free
parameter exactly once and never reads one Experiment to assign another, which
removes the failure mode rather than testing for it.
