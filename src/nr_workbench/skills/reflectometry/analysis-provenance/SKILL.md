---
name: analysis-provenance
description: >
  Keep every result traceable to the script, data and environment that produced
  it, and know how to answer "what made this figure?".
  USE FOR: running a fit, deciding whether a result can be cited, tracing a
  figure, marking a result final, responding to a stale-result warning.
  DO NOT USE FOR: the project directory layout (see nr-workbench-project) or
  reflectometry physics (see neutron-reflectometry).
version: 2
metadata:
  facility: SNS
  instruments: [REF_L, BL-4B]
  techniques: [reflectometry]
  tags: [provenance, reproducibility, fitting, records, workflow]
---

# Analysis Provenance

## Overview

This project exists because a previous folder of scripts made it impossible to
say which one produced a published figure. The machinery that prevents a repeat
is simple and it only works if you go through it.

Every fit writes an **immutable directory** containing:

```
results/<fit_id>/
  manifest.json   what ran, with chi-squared and settings
  model.py        the script exactly as executed
  inputs.json     every input file with its sha256
  env/            exact package versions, and a git patch if the tree was dirty
  fit/            the bumps output
  figures/        stamped with the fit_id, so they survive being copied out
  NOTES.md        the only file here you may edit
```

`fit_id` is a UTC timestamp plus a hash of the run's identity, so it sorts
chronologically *and* identifies its content — two runs of the same script on
the same data share a suffix.

## When to Use

Whenever you run a fit, cite a result, or are asked where a number came from.
Read it before running your first fit in a project.

## Process

### 1. Run fits through `nrw`

```bash
nrw fit run samples/<id>/models/<name>.py --method dream --samples 5000 \
    --seed 12345 --note "why this run exists"
```

Hand-written scripts work as they are — no migration, no schema. The script
must define a module-level `problem = FitProblem(...)`.

`nrw` observes which files the script actually opens, so inputs are recorded
exactly even when the path is built at runtime from a run number.

### 2. Expect an identical re-run to be refused

If nothing changed — script, inputs, settings, environment — `nrw` refuses and
points at the existing result. That is the mechanism that stops twelve
near-identical directories accumulating. Use `--force` when you genuinely want
a replicate; it is recorded as one.

### 3. Trace anything with `whence`

```bash
nrw whence figures/fig3.svg      # a figure, even one copied out of the project
nrw whence samples/S4/data/steady/REFL_230597_combined_data_auto.txt
nrw whence <fit_id>
```

On a **data file** it lists every fit that consumed it — the "this file changed,
what do I need to redo?" direction.

### 4. Treat a STALE result as unusable

A result is stale when an input's sha256 no longer matches what the fit
consumed. `nrw ls` marks it, `nrw whence` says so in red, and `nrw check` exits
non-zero.

Stale means the number on the plot was computed from bytes that are no longer
on disk. Re-run before citing it. Do not reason about how much the data
"probably" changed.

### 5. Mark the answer explicitly

```bash
nrw promote <fit_id> --as final --reason "converged; SLD band excludes null"
```

Latest is not final and lowest chi-squared is not automatically final — a
person decides, and the reason is the part worth keeping. Promotion is refused
on a stale fit. Superseding an earlier decision records both; the history of
what was once considered final is provenance too.

### 6. Check before you commit or publish

```bash
nrw check
```

### 7. Package it before you send it

```bash
nrw pack <fit_id>
```

Sending a result directory sends a description of a fit, not a fit: it records
the hashes of its data rather than the data. A bundle carries the measurements
at the paths the frozen script expects, and a `verify.py` that applies the
recorded parameters and checks chi-squared. Refused when an input has drifted
— a bundle asserts that its data produced its result.

## Rationalizations

| Excuse | Rebuttal |
|---|---|
| "I'll run bumps directly, it's faster." | It is a few seconds faster and produces a result nobody can trace. Every shortcut here is how the previous repo happened. |
| "I'll record the provenance afterwards." | Afterwards the environment has moved and the data may have been re-reduced. The record is only true at the moment it is written. |
| "The data only changed slightly, the fit is still basically right." | Then re-running is cheap. "Basically right" is not a claim the hashes can support, and STALE is not a warning to reason around. |
| "I'll just edit the generated script, it's quicker than the spec." | Use `nrw model fork`. It gives you a hand-owned script that still has a full record. Escaping the generator must not mean escaping provenance. |
| "`--reason` is obvious from the chi-squared." | Chi-squared says which fit was numerically best, not why it is the answer. The reason is what a reader in a year needs. |
| "I'll re-run with `--force` to get a cleaner number." | Running until you like the answer is not a method. If a re-run is warranted, say why in `--note`. |

## Red Flags

- A figure in a report that `nrw whence` cannot resolve.
- A result cited while `nrw ls` shows it STALE.
- A `results/` directory edited after the fact.
- `nrw check` failing and the failure worked around rather than fixed.
- A promotion with a reason like "best fit" or "final".
- Several fits with the same run key, none marked as replicates.
- A fit whose record lists no data inputs — the script read nothing, or read it
  in a way that could not be observed. Either way the record is incomplete.

## Verification

Before citing or sharing a result:

- [ ] `nrw check` exits zero.
- [ ] `nrw whence <figure>` names a fit, and its inputs are FRESH.
- [ ] The fit is promoted, with a reason that says why rather than what.
- [ ] `env/versions.json` records the versions, and a patch is present if the
      tree was dirty.
- [ ] Any hand-owned script is a registered fork, not an edited generated file.
