---
name: nr-workbench-project
description: >
  Navigate and work inside an nr-workbench project: where files belong, which
  command does what, and the provenance rules that keep every result traceable.
  USE FOR: orienting in a workbench project, deciding where to put a file,
  choosing the right nrw command, understanding why a result is or is not
  reproducible.
  DO NOT USE FOR: reflectometry physics (see neutron-reflectometry) or
  assessing time-resolved data (see tnr-change-assessment).
version: 2
metadata:
  facility: SNS
  instruments: [REF_L, BL-4B]
  techniques: [reflectometry]
  tags: [project-layout, provenance, workflow, cli, orientation]
---

# nr-workbench Project

## Overview

This project was scaffolded by `nrw init`. It exists because the previous way
of working — a directory per beamtime, each with its own layout, and fitting
scripts named `…-corefine-tNR-test.py`, `…-t0.py`, `… copy.py` — made it
impossible to say which script produced a published figure.

Two rules fix that, and everything below follows from them:

1. **One layout, always.** `samples/<id>/` is the only place sample work lives.
   Beamtime is metadata in `sample.yaml`, never directory structure.
2. **Results are immutable and attributed.** A fit writes a new directory that
   is never overwritten, recording the spec, the script, every input file's
   hash, and the environment.

## When to Use

Read this at the start of any task in a workbench project — before creating a
file, running a fit, or answering "where should this go?". It is the map.

Do not use it for physics or for interpreting data; it describes the machinery
only.

## Process

### 1. Find your bearings

```bash
nrw doctor          # environment, versions, project integrity
nrw sample new S4   # create samples/S4/ with the standard layout
```

The project root is the directory holding `nrw.toml`. Everything else is
resolved relative to it — never write an absolute path into a committed file.

### 2. Put files in the right place

| What | Where | Who owns it |
|---|---|---|
| Sample context, prose | `samples/<id>/sample.md` | **You / the scientist.** Free text. |
| Measurement register | `samples/<id>/sample.yaml` | Machine. Maintained by `nrw sample scan`. |
| Reduced steady-state data | `samples/<id>/data/steady/` | Instrument. Committed. |
| Reduced tNR slices | `samples/<id>/data/tnr/<run>_<binning>/` | Instrument. Committed. |
| Raw NeXus | `samples/<id>/data/raw/` | Instrument. **Gitignored** (large). |
| Model spec | `samples/<id>/models/<name>.yaml` | You. **Source of truth.** Committed. |
| Generated fit script | `samples/<id>/models/<name>.py` | Machine. Derived, hash-guarded. Committed. |
| tNR assessment output | `samples/<id>/assessments/<label>/` | Machine. |
| Fit results | `samples/<id>/results/<fit_id>/` | Machine. **Immutable** except `NOTES.md`. |
| What one fit was and showed | `samples/<id>/results/<fit_id>/NOTES.md` | **You.** `nrw note <fit>`. |
| How the fits relate; reports | `samples/<id>/reports/*.md` | **You.** `nrw note --sample <id>`. |
| Skills | `skills/<domain>/<name>/` | Shared. Read by both Claude Code and Copilot. |

### 3. Respect the ownership boundary

Files marked *Machine* above carry a generated header or a lock entry. Editing
one by hand does not corrupt anything, but it does break the link between a
result and what produced it, so the tooling will flag it and refuse to mark
that result final.

If you need a generated script to do something the spec cannot express, use
`nrw model fork`. That gives you a hand-owned script that **still** gets a fit
record, input hashes, and full provenance. Forking is cheap and supported;
silently editing a generated file is what we are trying to eliminate.

### 4. Record what you learn, next to what it is about

Findings about **a sample** — what a fit showed, why a model was rejected, how
two results relate, what a number does and does not mean — go with that sample,
not in a project-wide file. Two places, by scope:

```bash
# about one run: what you were testing, what it showed, what to distrust
nrw assess <fit_id>                      # the automatic checks, written into its NOTES.md
nrw note <fit_id> -m "the oxide is at its floor; conditional, not measured"

# about the sample: the argument across several fits
nrw note --sample <id> --title "why the tNR is fitted alone" -m "..."
```

Name fit ids in the prose. Nothing else is needed to link them — `nrw ls`, the
fit page and `nrw pack` all find a report by the ids it mentions, so citing
`20260807-163359Z-0103d9c7` in a sentence is what attaches the note to that fit.

**Do this as you go, not at the end.** The reason a fit was abandoned is worth
more than the fit, and it is the first thing forgotten. A run you are about to
discard still deserves one line saying why.

`docs/ground_truths.md` stays, narrowed to what is **not** about any sample:
tooling quirks, instrument behaviour, a convention the whole project follows.
If a finding names a run number or a fit id, it belongs to the sample.

## Rationalizations

| Excuse | Rebuttal |
|---|---|
| "I'll just tweak the generated `.py`, it's faster than editing the spec." | That is exactly how twelve near-identical scripts appeared last time. Edit the spec and regenerate, or `nrw model fork` to take ownership explicitly. |
| "I'll add a `USE_OXIDE = True` flag so one script covers both cases." | A toggle inside a fit script means the file no longer identifies what it produced. Two specs, two hashes, two records. |
| "This sample is from a different beamtime, it needs its own folder layout." | Beamtime is a field in `sample.yaml`. Four bespoke layouts is the problem this project replaces. |
| "The data is huge, I'll point the spec at my Downloads folder." | Absolute paths outside the project break for everyone else and for you on another machine. Put data under `samples/<id>/data/`, or symlink it there. |
| "I'll organise the results later." | Later never comes, and by then the environment has moved. `nrw fit run` costs nothing extra and records everything at the moment it is still true. |

## Red Flags

- A `.py` file under `models/` with no matching `.yaml`, and not registered as a fork.
- An absolute path (`/Users/...`, `C:\...`) in any committed file.
- A figure in a report that no fit record claims to have produced.
- Two files whose names differ only by `-test`, `-v2`, `-final`, or ` copy`.
- A results directory that has been edited since it was written.
- A data file changed after a fit consumed it, with no re-run.

## Verification

Before considering a piece of work done:

- [ ] `nrw check` exits zero — no drifted scripts, no stale results, no orphans.
- [ ] Any figure you are about to share resolves: `nrw whence <path>` names a fit.
- [ ] Every fit you ran has a `NOTES.md` saying what it was for — including
      the ones that failed or were abandoned.
- [ ] Findings about the sample are in `samples/<id>/reports/`, citing the
      fit ids they are about. Only non-sample findings went to
      `docs/ground_truths.md`.
- [ ] `nrw ls` shows no fit with nothing written down.
- [ ] `git status` shows no unexpected files outside the layout table above.
