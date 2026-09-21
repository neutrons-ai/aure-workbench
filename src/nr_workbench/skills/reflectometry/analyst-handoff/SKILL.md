---
name: analyst-handoff
description: >
  Take over an analysis an unattended session started, without re-deriving what
  it already established or walking into what it already hit.
  USE FOR: the first turns of any interactive session in a project that has
  already been worked on, picking up after `nrw agent run`, orienting when
  someone says "carry on from where the agent got to".
  DO NOT USE FOR: the project layout itself (see nr-workbench-project) or
  deciding what to fit next (see neutron-reflectometry and
  steady-state-corefinement).
version: 2
metadata:
  facility: SNS
  instruments: [REF_L, BL-4B]
  techniques: [reflectometry]
  tags: [orientation, handoff, takeover, session, escalations, provenance]
---

# Analyst Handoff

## Overview

An unattended session is handed a composed prompt: every offline check already
run, the fit chain, the reports, the skills to read. An interactive session
gets a sentence — *"let's pick up where the agent left off"* — and has to
rebuild all of it.

On the reference beamtime project that rebuild took **28 tool calls before the
first substantive action**, and ten of those were spent looking for the `nrw`
binary. All of it was derivable, and almost all of it is now one command.

The expensive failures in a takeover are not slowness, though. They are:

- **Re-deriving a conclusion someone already paid for.** A branch that looks
  unexplored was often tried and rejected, with the reason in a report rather
  than in the numbers.
- **Walking into a known trap.** The reference project's `ESCALATIONS.md`
  recorded a live code-generation bug that silently wrote the wrong value into
  every fixed parameter. It was invisible in `validate` and `preview` output.
  A session that wrote a spec before reading that file reproduced it.
- **Overwriting finished work.** A report is a deliverable someone has read and
  approved. It is not a scratchpad to be updated in place.

## When to Use

At the start of **every** interactive session in a project that already has
fits in it — not only after `nrw agent run`. Yesterday's session is as much a
predecessor as an unattended one, and left the same kinds of trace.

Skip it only in a project with no recorded fits and no reports, which is a
fresh start rather than a handoff.

## Process

### 1. One command, first

```bash
nrw handoff <sample>
```

It prints, in this order: how to invoke `nrw` here, the declared task, the
escalations in full, how the last unattended session ended, the integrity
check, any fits with nothing written down, the offline checks recomputed now,
who is reading, and the reading order.

Do not begin by listing directories. Everything that survey would tell you is
in there, and several things you would not have thought to look for are too.

**If `nrw` is not on your PATH, the handoff says so and gives you the
invocation to use.** Do not go hunting through `~/.venv`, `~/.pixi` or
`~/.zshrc` for the binary — that search is the single most expensive
non-event ever measured in one of these sessions.

### 1a. If you just cloned this project, set yourself up first

Everything above assumes the project directory already works. A **fresh clone
is different**, and `nrw handoff` cannot help until one step is done — because
that step is what makes `nrw` runnable at all.

A clone deliberately arrives *without* the two files that say where `nrw`
lives. `.nrw/bin/nrw` and `.claude/settings.local.json` hold absolute paths, so
they are gitignored and belong to whoever ran `init`, not to the project. So:

```bash
# 1. Install nr-workbench itself. `nrw init` cannot bootstrap the tool that
#    provides it, so this is genuinely first. Skip if `nrw` is already on PATH.
#    On Windows: irm https://raw.githubusercontent.com/neutrons-ai/aure-workbench/main/install.ps1 | iex
curl -fsSL https://raw.githubusercontent.com/neutrons-ai/aure-workbench/main/install.sh | sh

# 2. In your clone of the *analysis* project:
nrw init --check     # what a real init would change, before it changes it
nrw init             # writes .nrw/bin/nrw and NRW_BIN for THIS machine
nrw doctor           # confirm what it found
cp .env.example .env # only for `nrw model new --from-notes` / AuRE
```

`nrw init` on an existing project is an upgrade, not a re-scaffold: it keeps
the project's identity and reports every templated file as `unchanged`. What it
always redoes is the machine-local part, which is exactly what you need.

Two things to watch:

- **Version skew.** `init` replans every skill and agent file from *your*
  installed nr-workbench. If that differs from the version which scaffolded the
  project, those files come back as `upgrade` and you get a diff nobody asked
  for. Run `nrw init --check` first; if it is not clean, agree a version with
  the other analyst before applying.
- **A `PATH` that is not yours.** If `init` warns that
  `.claude/settings.local.json` sets a `PATH` starting somewhere unfamiliar, it
  was committed from someone else's machine. `init` refuses to rewrite a `PATH`
  you may have set deliberately, so clear it yourself: `nrw doctor --fix-path`
  to replace it, or delete the entry.

Then run `nrw check`. It enforces provenance rule 3, so it is also how you find
out whether the project you just cloned has absolute paths committed in it.

### 2. Read the escalations before you write a spec

Not after a fit disagrees with you. `ESCALATIONS.md` is where an unattended
session records what it could not decide, and its items are *live* until a
person closes them. Expect to find:

- tooling bugs it worked around, and the idiom it used instead;
- runs it quarantined, and why;
- systematic offsets it absorbed into the model rather than fixed;
- conclusions it later retracted — read those to the end, because the
  retraction is usually more informative than the original claim.

An escalation that names a workaround is telling you to use that workaround
too. It has not been fixed just because it has been written down.

### 3. Establish what is already true

Read in the order `nrw handoff` gives, which is deliberately not the order the
file tree suggests:

1. `ESCALATIONS.md` — what a person still has to decide.
2. `samples/<id>/reports/` — the conclusions, and which branches were abandoned.
3. `samples/<id>/sample.md` — the sample, and the declared task.
4. `nrw ls`, then the `NOTES.md` of **only** the fits you intend to build on.

`results/` is the biggest directory in the project and the least informative
per byte: from outside, an abandoned exploration and the reportable fit look
identical. Read it last, and selectively.

You have absorbed the handoff when you can state, before your first fit:

- the sample's question;
- which recorded fits answer it today, by id;
- which escalations are still open;
- what your first fit adds that is not already there.

If you cannot say all four, you are still orienting. Say so and keep reading —
that is a much cheaper turn than a fit nobody needed.

### 4. Know what the unattended session could not do

The gap between what was asked and what was delivered is rarely laziness. It is
usually structural, and it is where the real work is:

| Refused to it | So look for |
|---|---|
| `nrw promote` | Nothing is marked final. Deciding that is the scientist's. |
| Any `--force` | A blocked path it worked around rather than through. |
| Anything needing a judgement call | An escalation naming the decision. |
| Sustained systematics sweeps | Background, resolution and scale tests it had no mandate to run. On the reference project, adding a free background was the scientist's first instruction to the takeover session. |

### 5. Do not silently redo work

If you disagree with a recorded conclusion, **say so in writing, with the
evidence, in a new report** — do not quietly re-run the fit until it agrees
with you. The reference project's most valuable artefact is an escalation whose
author retracted it in the same session and left both versions in place, with
the reasoning for the reversal. That is the standard.

Re-running a fit is legitimate when you say why, in advance, and record it.

### 6. Existing reports are finished artefacts

New work goes in a **new** report:

```bash
nrw report <sample> --topic buried-change-before-230553
```

Do not edit a report you did not write this session unless you are asked to.
When a new report supersedes an old one, say so in both directions rather than
deleting the old:

```bash
nrw report supersede <old-slug> --by <new-slug> --reason "..."
```

### 7. Write for the person who is actually reading

`nrw handoff` prints the `[audience]` block and what it implies. It is three
independent axes — reflectometry, statistics, domain — plus how this person
wants to work, because a reflectometry expert who wants the Bayesian reasoning
spelled out is a common reader and a single novice/expert dial gets them wrong
twice.

If nobody has set it, the handoff says so. Ask once, early, and record it with
`nrw audience --ask`. It costs one question and changes every paragraph you
write afterwards.

## Rationalizations

| Excuse | Rebuttal |
|---|---|
| "I'll skim the reports later — let me get a fit running first." | The reports are what stop you running a fit that has already been run and rejected. Later is after you have spent the beam time. |
| "`ESCALATIONS.md` is just a log of problems, not instructions." | It is a list of live traps and the workarounds currently in force. The reference project's generator bug was in there, and it silently wrote a plausible wrong number into any spec that ignored it. |
| "The agent's conclusion looks wrong; I'll just re-fit and see." | Then you have two answers and no record of why they differ. Say what you think is wrong and what would show it, then run the fit that settles it. |
| "I'll update the existing report with the new findings." | Someone has read and approved that document. New topic, new report; supersede the old one explicitly if it is now wrong. |
| "The scientist is clearly an expert, I don't need to ask about audience." | Expert in *what*? The three axes are independent, and the common case is deep in one and deliberately rusty in another. |
| "`nrw` isn't on PATH; I'll find it." | `nrw handoff` already told you the invocation. Ten tool calls have been spent on this search before. |

## Red Flags

- Five tool calls into a session and `nrw handoff` has not been run.
- A spec written before `ESCALATIONS.md` was read.
- A fit re-run with no note saying why it was re-run.
- An edit to a report from a previous session that nobody asked for.
- A `nrw ls` listing showing fits with nothing written down, left that way.
- Explaining a concept at length to someone the `[audience]` block calls an
  expert in it — or quoting a bare sigma to someone it calls a newcomer.
- Searching the filesystem for the `nrw` binary.

## Verification

Before your first fit of a takeover session:

- [ ] `nrw handoff <sample>` has been run and read.
- [ ] `ESCALATIONS.md` has been read to the end, including retracted items.
- [ ] You can name the fit ids that answer the sample's question today.
- [ ] You can name what your first fit adds that is not already recorded.
- [ ] You know which invocation of `nrw` works in this shell.
- [ ] You know who is reading, or you have asked.

Before you finish:

- [ ] Every fit you ran has a note saying what it was for — including abandoned ones.
- [ ] New findings are in a new report, not spliced into an old one.
- [ ] `nrw check` exits zero, or the exceptions are recorded and explained.
