---
name: refl-reduced-headers
description: >
  Read the metadata REF_L writes into a reduced data file's header instead of
  assuming it.
  USE FOR: finding a measurement's incident angle, which direct beam
  normalised it, the resolution and scaling the reduction applied, when it was
  taken; deciding what to do when a file has no header.
  DO NOT USE FOR: the physics those numbers feed into (see
  refl-bl4b-instrument) or reading the data columns themselves.
version: 2
metadata:
  facility: SNS
  instruments: [REF_L, BL-4B]
  techniques: [reflectometry, data-reduction]
  tags: [header, metadata, theta, angle, direct-beam, norm-run, provenance, parsing]
---

# Reading a reduced file's header

## Overview

Every reduced steady-state REF_L file records how it was made, in one JSON
object on a `# Meta:` line. Incident angle, direct-beam run, resolution,
scaling factor, timestamp — all of it, exactly, already on disk.

So the incident angle is not something to infer from a filename, assume from
the usual settings, or ask a model to guess. It is a field. **Read it.**

The one that bites: **angles are stored in radians**. `"theta": 0.00785` is
0.45°.

## When to Use

- Building a probe, which needs the angle.
- Writing or checking a model spec's `thetas:`.
- Finding out which direct beam normalised a segment.
- Investigating why two angle segments disagree about the same sample.
- Meeting a file whose header you do not recognise.

## Process

### 1. The header, in full

```
# Experiment IPTS-34347 Run 218386
# Reduction 2.2.0.dev47+d202502071725
# Run title: CuPt_d8-THF_FullQ-218386-1.
# Run start time: 2025-04-20T13:42:43.100443667
# Theta offset: 0
# Meta:{"wl_min": 2.7497, "wl_max": 9.4986, "q_min": 0.01038, "q_max": 0.03589,
#       "theta": 0.007853609528660925, "start_time": "2025-04-20T13:42:43.1004",
#       "experiment": "IPTS-34347", "run_number": "218386",
#       "run_title": "CuPt_d8-THF_FullQ-218386-1.", "norm_run": 218274,
#       "dq0": 0, "dq_over_q": 0.027176415243227114, "sequence_number": 1,
#       "sequence_id": 218386, "q_summing": false,
#       "scaling_factors": {"a": 4.0, "err_a": 0, "b": 0, "err_b": 0},
#       "tof_weighted": false, "bck_in_q": false, "theta_offset": 0}
# DataRun   NormRun   TwoTheta(deg)  LambdaMin(A) ...
# 218386    218274    0.899957       2.74977      ...
# Q [1/Angstrom]        R                     dR                    dQ [FWHM]
```

The fields worth knowing:

| Field | Meaning |
|---|---|
| `theta` | Incident angle, **radians**. 0.007853 → 0.4500° |
| `norm_run` | The direct-beam run this segment was divided by |
| `sequence_number` | Which angle segment, 1-based |
| `sequence_id` | Run number of the measurement as a whole |
| `dq_over_q` | Fractional resolution as reduced |
| `scaling_factors.a` | Constant the reduction multiplied this segment by |
| `q_min` / `q_max` | Q range |
| `start_time` | ISO-8601, and what a geometry lookup needs |
| `theta_offset` | Any offset the reduction already applied |

The fixed-width table below it repeats some of this. `TwoTheta(deg)` is
**twice** theta and already in degrees — 0.899957 for a 0.45° measurement — so
it is a usable fallback when the JSON block is missing.

**One fact is not in the JSON block: whether `dQ` is FWHM or sigma.** It is in
the column-title line, further down the header:

```
# Q [1/Angstrom]        R                     dR                    dQ [FWHM]
```

`dq_over_q` gives the *magnitude* of the resolution; only this line gives the
*convention*. Every reduction so far writes `FWHM` and the intention is to move
to `sigma`. The two differ by 2.355 — enough to broaden or sharpen every fringe,
and a factor the fit absorbs into roughness rather than reporting. So it is read,
not remembered:

| Field | Meaning |
|---|---|
| `dq_convention` | `"fwhm"`, `"sigma"`, or `None` when the file does not say |
| `dq_column_label` | The label exactly as written |
| `dq_is_fwhm` | The same thing as a bool, `None` when unstated |

`None` is deliberately not `True`: a caller that needs the answer must decide and
record what it decided. An *unrecognised* label raises `HeaderError` rather than
falling back, because a silent 2.355 is the failure mode this skill exists to
prevent.

### 2. Read it

```python
from nr_workbench.instrument.header import read_header

h = read_header("samples/S1/data/steady/REFL_218386_1_218386_partial.txt")
h.theta  # 0.45 -- degrees, converted from the radians on disk
h.norm_run  # 218274
h.scaling_factor  # 4.0
h.source  # "meta", "table", or "none"
```

Always check `source`. `none` means the file had no header and every field is
`None` — that is information, not a failure.

By hand, it is three lines:

```python
import json
import math

for line in open(path):
    if line.startswith("# Meta:"):
        meta = json.loads(line[len("# Meta:") :])
        theta_deg = math.degrees(meta["theta"])
        break
```

### 3. Know which files have no header

**Time-resolved slices carry none at all.** No `# Meta:`, no table, nothing.
Neither the `*_eis_reduction.json` sidecar nor the tNR template XML records an
angle either.

But the angle is still on disk, one directory across: **a tNR run is also
reduced as a summed dataset into `data/steady`, under the same run number.**
That file has the full header.

```python
from nr_workbench.instrument.header import theta_for_run

theta, source = theta_for_run(sample / "data/steady", run=218389)
# (0.5997, 'REFL_218389_4_218389_partial.txt')
```

For run 218389 the measured angle is **0.5997°**, not the 0.6 anyone would have
assumed. Small, but it feeds `dT = dq/q · tan(θ)` and costs nothing to get right.

### 4. Use `norm_run` when segments disagree

`nrw data overlap` reports run 218386's 3.5° segment 27.6% away from the 1.2°
segment. The headers say why it is worth looking there:

```
218386 <- 218274      # segments 1 and 2: adjacent direct beams
218387 <- 218275
218388 <- 218338      # segment 3: measured much later
```

Different angles legitimately use different direct beams, so this is where to
look first, not a diagnosis. The `scaling_factors.a` values (4.0, 16.0, 196.0)
are the reduction's own normalisation and are worth reading alongside.

### 5. When you meet a header you do not recognise

Parse what you know and **fail loudly** on what you do not. Do not guess an
angle, and do not ask a model to read one out of prose.

The reason is specific: theta sets the resolution of every point through
`dT = dq/q · tan(θ)`. A wrong-but-plausible angle does not raise — it broadens
or sharpens every fringe, and the fit compensates with roughness. You get a
converged fit, a reasonable χ², and a wrong interface width.

So the rule is: an exact field beats an inference, every time. Where a model
genuinely helps is reading an *unfamiliar* format once and writing a parser for
it — which is a code-generation task, with a test, not a per-file inference.

## Rationalizations

**"The angles are always 0.45, 1.2, 3.5."** They are on this beamline, usually.
The measured values for run 218386 are 0.4500, 1.2010, 3.5003, and for 218393
they are 0.4499, 1.2009, 3.5002 — close, but different runs and different
numbers. A two-segment measurement at other settings would be silently wrong.

**"0.6 is close enough to 0.5997."** It is, for that run. But you did not know
it was 0.5997 until you read it, and the next run may not be.

**"I'll just ask the model to read the header."** The value is in a JSON field.
A parser is exact, offline, deterministic and testable; a model call is none of
those and can return a plausible number that silently corrupts the resolution.

**"theta is in degrees, everything else is."** It is in radians. 0.00785 is not
a small angle in degrees, it is 0.45°.

**"The file has no header, so I'll use the default."** Check `data/steady` for
the same run number first — for a tNR series the header is there.

**"`dQ` is FWHM at REF_L, everyone knows that."** It has been, and the move to
sigma is intended. A convention held in your head is one that silently stops
being true, and this one stops being true without a single fit failing — the
resolution is simply wrong by 2.355 and the roughness quietly absorbs it. The
file states it. Read the file.

## Red Flags

- A spec whose `thetas:` are round numbers when the files record otherwise.
- An angle taken from a filename or a sequence number.
- `theta` used without a radians-to-degrees conversion — a 0.0079 in a spec.
- A resolution computed from an assumed angle.
- Segments disagreeing in `nrw data overlap` with nobody having looked at
  `norm_run`.
- A parser that silently substitutes a default when a header is missing, rather
  than reporting it.
- `probe.dq_is_fwhm` in a spec that nobody traced to a column-title line.
- Runs from different reduction versions co-refined in one spec without checking
  that they agree on the `dQ` convention.

## Verification

```bash
nrw data features <file>       # reports the Q range actually measured
nrw model new <sample> --name <n>   # reads every angle from its file's header
```

`nrw model new` prints a warning naming any file that recorded no angle, so a
scaffolded spec tells you which of its `thetas:` are measured and which are
assumed.

`nrw data check` reports the `dQ` convention per file, and flags a sample whose
files disagree — that set cannot be co-refined, because `probe.dq_is_fwhm` is one
boolean for the whole spec.

Four checks:

0. **`probe.dq_is_fwhm` matches the column titles.** `grep 'dQ \[' <file>` on any
   segment the spec fits. If they disagree, every resolution in the fit is wrong
   by 2.355 and the roughnesses are absorbing it.
1. **Every `thetas:` entry traces to a header.** If one is a round number and
   the rest are not, it is the assumed one.
2. **`theta` converted from radians.** Anything under 0.1 in a spec is radians
   that were not converted.
3. **A series' angle came from the summed dataset**, not from a default. Check
   `data/steady` holds a file with the series' run number.
