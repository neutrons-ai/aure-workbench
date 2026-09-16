---
name: aure-first-fit
description: >
  Get a scientist's first fit of a sample by interviewing them for what only
  they know, handing it to AuRE, and bringing the result back as an ordinary
  model spec.
  USE FOR: a sample with data but no model yet, "can you fit this?", the first
  steady-state fit after `nrw init` and copying data in, proposing a stack when
  nobody knows what the layers are.
  DO NOT USE FOR: refining a model that already exists (see nrw-model-spec),
  time-resolved series (see tnr-change-assessment), or deciding whether a
  finished fit is trustworthy (see refl1d-script-review).
version: 2
metadata:
  facility: SNS
  instruments: [REF_L, BL-4B]
  techniques: [reflectometry]
  tags: [first-fit, aure, onboarding, model-building, stack, workflow]
---

# A First Fit with AuRE

## Overview

Someone has run `nrw init`, copied reduced data into
`samples/<id>/data/steady/`, and asked you to fit it. The long path —
`nrw model new`, then write the stack by hand — stalls on the one thing the
tooling cannot supply: **what the sample is made of**.

AuRE closes exactly that gap. Given a plain-English sentence and a data file it
iterates intake → analysis → modeling → fitting → evaluation until the fit is
good, and hands back a fitted layer stack. It is efficient at a *first good
fit*, and that is all it is for here.

Two consequences shape everything below.

1. **AuRE's answer is a proposal, not a result.** It has no provenance record,
   and this project's offline fit checks are better than its own. So an AuRE run
   is reconnaissance; the fit that counts is the one `nrw fit run` records.
2. **The description is the whole input.** AuRE builds the entire model from one
   or two sentences. Nothing in the data says whether the sample sits in D₂O or
   air, or which side the beam came in. If those sentences are wrong, everything
   downstream is wrong and nothing will say so.

## When to Use

When a sample has data on disk and no model yet, and someone wants a fit.

Do not reach for it to refine a model that already exists — editing the spec and
re-fitting is faster and keeps the history. Do not use it for a time-resolved
series: run `nrw tnr assess` first and read `tnr-change-assessment`.

It needs a language-model endpoint. Without one AuRE cannot run at all, and the
honest answer is `nrw model new --print-prompt`, which asks **you** to propose
the stack from `sample.md` and the project's skills. That path is not a
consolation prize — you can read every file in the repo, which AuRE cannot.

## Process

### 1. Check what is actually there

```bash
nrw doctor                 # is there an endpoint? is aure installed?
nrw sample scan <ID>       # what data is on disk
```

If `nrw doctor` shows no `llm` endpoint, stop and use
`nrw model new --print-prompt` instead. Say so rather than running something
that will fail five steps later.

### 2. Interview the scientist — six questions

**Ask before doing anything else, and ask all of it at once.** These are the
inputs AuRE cannot derive, and a run started without them is a guess wearing a
chi-squared.

1. **What is the stack, substrate upwards, with rough thicknesses?**
   e.g. "50 nm Cu on 5 nm Ti on silicon."
2. **What is it in?** Air, D₂O, H₂O, dTHF, an electrolyte. Name the solvent and
   its deuteration — "THF" and "d8-THF" differ by 6 in SLD, and the difference
   lands in a layer if it is wrong.
3. **Does the beam enter through the substrate?** A solid/liquid cell is
   measured through the wafer; a film in air is not. This decides the stack
   order, and getting it backwards fits, converges, and reports a chi-squared in
   the hundreds with nothing naming the cause.
4. **Which run?** Skip it if the scan found only one.
5. **Anything you already suspect?** A native oxide, a swollen layer, an
   adsorbed film. AuRE ranks these as candidate structures and tests them.
6. **How long can this take?** Minutes, or can it run properly?

### 3. Get the answers into `sample.md` — but do not write them yourself

**`sample.md` is the scientist's file. An assistant must not edit it.** It is
the live record of an experiment in progress, and something you add can
silently overwrite something they added a minute ago. Hand them the answers in
a form they can paste, or just tell them which section is empty, and let them
write it.

Questions 1–3 belong under `## Description` and `## Details`; question 5 under
`## Fits to perform`. The template already has these sections.

This is not bookkeeping. It is the step that makes the rest work:

- `nrw aure new` **reads them from there**, and refuses if `## Description` is
  empty. Prose that nothing reads back does not get written — measured on the
  first real beamtime through this tool, where every optional note field came
  back empty and the one required field was filled in every time.
- The next person to open this sample gets the answer without asking again.

### 4. Write the setup

```bash
nrw aure new <ID> --run <run>
```

It prints two lists, and they mean different things:

- **read from the files** — the run, its segment files, their order. Exact.
  Do not edit these.
- **read from sample.md** — the description, the hypothesis, the geometry.
  Yours, and the part worth checking.

### 5. Read the setup back before running it

Open `samples/<ID>/aure/<name>/setup.yaml` and check the two things the data
cannot tell anyone:

- Does `sample_description` name **every** layer, the ambient medium, and the
  right deuteration?
- Is `back_reflection` right? If the command warned that it was not set and the
  sample is in a liquid cell, fix it now.

### 6. Run it

```bash
nrw aure run samples/<ID>/aure/<name>/setup.yaml
```

Default budget is `quick` — few steps, one refinement — because the first
question is whether the description was right, not how well it can be polished.
`--budget standard` is AuRE's own defaults and takes considerably longer.

### 7. If the first pass is poor, retry deliberately

Re-running the same thing and hoping is not a strategy. Retry with
`--mode-enumeration` when any of these is true:

- chi-squared is poor and the residuals still show fringes;
- the stack has a layer under ~30 Å;
- a roughness has grown to a large fraction of its own layer's thickness.

All three are the same failure: a thin layer sits on a contrast×thickness ridge
where one optimiser run can land in the wrong basin and report a confident wrong
answer. `thin-layer-degeneracy` is the standard; `--mode-enumeration` is the
mechanism, and it re-seeds each thin layer's SLD across discrete levels.

If instead the description was wrong, fix `sample.md` and go back to step 4. A
better optimiser cannot rescue a sample that is in the wrong solvent.

### 8. Bring it back under provenance

```bash
nrw aure import samples/<ID>/aure/<name>/output --sample <ID> --name <name>
nrw model validate samples/<ID>/models/<name>.yaml
nrw model generate samples/<ID>/models/<name>.yaml
nrw fit run samples/<ID>/models/<name>.py --method amoeba --note "..."
```

`nrw model validate` independently checks the geometry against the measured
critical edge — it will say "beam enters Si last in the stack, i.e. through the
substrate". If that sentence contradicts what the scientist told you in question
3, believe neither and go back and ask.

### 9. Say what it means

```bash
nrw assess <fit_id>
nrw note <fit_id> --showed "..." --caveat "..."
```

The caveat that always applies to a first fit: **this stack was proposed, not
measured.** A layer AuRE added to improve chi-squared is a hypothesis until
something independent supports it.

## Rationalizations

| Excuse | Rebuttal |
|---|---|
| "I'll put the sample description straight in the setup YAML, it's faster." | Then it exists in one file that nothing else reads, and the next session asks the scientist the same six questions. `sample.md` is read by `nrw aure new`, `nrw model new --from-notes`, `--print-prompt` and the agent session. Write it once, where all of them look. |
| "They said 'THF', I'll just put THF." | d8-THF is ~6.35 and protonated THF is ~0.4. Whichever you guessed wrong, the fit absorbs it into a layer SLD and still converges. Ask. |
| "Back reflection isn't mentioned, so it's front." | Silence is not evidence. A solid/liquid cell is the common case on this beamline and is almost never spelled out. Ask, and if nobody knows, `nrw model validate` compares the ordering against the measured critical edge. |
| "chi-squared is 1.2, the model is right." | It means the curve is described, not that the structure is. A thin layer in the wrong basin also fits — that is what `thin-layer-degeneracy` is about — and a good chi-squared on a proposed stack is the most convincing wrong answer available. |
| "AuRE already fitted it, so I don't need `nrw fit run`." | Then there is no fit record, no input hashes, no environment, and nothing `nrw whence` can trace a figure back to. The AuRE run is reconnaissance. |
| "I'll re-run with more steps until chi-squared comes down." | If the residuals show fringes or a layer is pinned, more steps optimise the wrong model harder. Step 7 says which retry to reach for, and when the answer is to fix the description instead. |
| "I'll tidy the angles in the imported spec — 1.201 should be 1.2." | It came from the file's `# Meta:` header. Theta sets the resolution through `dT = dq/q · tan(θ)`, so a "tidied" angle is absorbed into roughness. |

## Red Flags

- A setup written with `## Description` containing only the template's comments.
- `sample_description` that names no ambient medium.
- `back_reflection` absent on a sample measured in a liquid cell.
- A first-fit spec promoted, or quoted in a report, without a second fit.
- An imported spec whose `thetas` have been rounded to 0.45 / 1.2 / 3.5.
- A run repeated with a bigger budget and no change to the description or the
  physics knobs — the same run, costing more.
- A layer under ~30 Å reported without `--mode-enumeration` ever having been
  tried, or without `thin-layer-degeneracy` having been read.
- An AuRE output directory imported from a run whose `final_state.json` records
  an error.

## Verification

Before telling anyone they have a fit:

- [ ] `sample.md` answers all six questions in §2, in the file — not only in
      the conversation.
- [ ] The setup's `sample_description` names every layer, the ambient medium
      and its deuteration.
- [ ] `nrw model validate` reports the geometry, and it agrees with what the
      scientist said.
- [ ] The fit that is quoted came from `nrw fit run` and has a `fit_id`.
- [ ] `nrw assess <fit_id>` has been run and its findings read — especially
      parameters at their bounds, which on a proposed stack usually means the
      stack is wrong rather than the bound.
- [ ] `nrw note` records that the stack was AuRE's proposal, and what would
      have to be true for it to be right.
- [ ] Every layer AuRE added beyond what the scientist described is named
      explicitly as a hypothesis, not reported as a finding.
