---
name: nrw-preflight
description: >
  Establish that a project and a sample exist before acting on any request
  that touches data, models, fits or reports -- and ask the scientist rather
  than improvising when one is missing.
  USE FOR: the first step of any analysis request, deciding whether to run
  `nrw init` or `nrw sample new`, resolving a sample name the scientist gave
  loosely, answering "where do I put this data?".
  DO NOT USE FOR: reflectometry physics (see neutron-reflectometry), project
  layout and provenance rules once you are in a project (see
  nr-workbench-project), or resuming a sample that already has fits (see
  analyst-handoff).
version: 2
metadata:
  facility: SNS
  instruments: [REF_L, BL-4B]
  techniques: [reflectometry]
  tags: [preflight, setup, guardrail, project-layout, orientation, cli]
---

# Preflight

## Overview

A scientist asks for something reasonable — *"fit my data"*, *"what changed
during the electrochemistry?"*, *"make me a report"* — and the machinery to do
it may simply not be there yet. There may be no project in this directory. The
sample they named may not exist, or may exist under a different spelling.

The failure mode is not that the work is hard. It is that improvising around
the gap **looks like progress and produces something unusable**: a `samples/`
directory made with `mkdir` that `nrw scan` does not recognise, data copied
somewhere plausible that no fit will ever find, a refl1d script written by hand
whose results carry no provenance — which is the one thing this project exists
to provide.

Two facts have to be established before anything else, in this order:

1. **Which project?** — the directory holding `nrw.toml`.
2. **Which sample?** — a directory under `samples/` holding `sample.md`.

Neither is yours to guess, and neither is yours to create unasked.

## When to Use

At the **start of every request** that would read or write sample data, create
a model, run a fit, assess a series, or write a report. Before `nrw handoff`,
which assumes both facts are already settled.

Skip it for questions that touch no project state: reflectometry physics, what
a parameter means, reading a file the scientist named by full path.

## Process

### 1. Establish the project

Any `nrw` command tells you. `nrw doctor` is the cheapest:

```bash
nrw doctor
```

If you are not in a project, every command says so in the same words:

```
No nrw.toml found in /home/you/scratch or any parent directory.
Run `nrw init` to create a project here.
```

**Do not run `nrw init` because you saw that message.** It writes about
seventy files into the current directory, and whether *this* directory is the
right one is the scientist's call — a beamtime folder, a fresh clone, or a
directory they happened to be in when they opened the terminal all look
identical from here. Getting it wrong leaves a stray project root that will
shadow the real one.

Ask, and ask for what `init` actually needs:

> This directory isn't an nr-workbench project yet. Shall I set one up here
> with `nrw init`? I need a beamtime label and the IPTS number
> (`nrw init --beamtime june2026 --ipts IPTS-34567`) — or tell me where your
> project is and I'll work there instead.

### 2. Establish the sample

Sample-taking commands name the ones that exist:

```
No sample 'S9' in /home/you/beamtime. Existing samples: S1, S4.
Use one of those, or create it with `nrw sample new S9`.
```

That list is the whole decision. Three cases:

| What you see | What it means | What to ask |
|---|---|---|
| The name is close to an existing one (`S4` vs `Sample4`) | Almost certainly a spelling difference | "Did you mean `Sample4`? That's the one in this project." |
| The name is genuinely new | They may want it created, or may be in the wrong project | "There's no `S9` here — the project has S1 and S4. Shall I create `S9`, or did you mean one of those?" |
| There are no samples at all | A fresh project | "This project has no samples yet. What should I call this one — and is the reduced data ready to copy in?" |

**Do not create the sample silently.** `nrw sample new <ID>` is cheap and
reversible, so the cost of asking is one turn — but a sample created under the
wrong id becomes the directory name in every later result path, report and
provenance record, and renaming it afterwards means rewriting all of them.

### 3. Only then proceed

With both facts settled:

- `nrw handoff <sample>` if the sample has history — it prints the state of
  play, and [`analyst-handoff`](../analyst-handoff/SKILL.md) says what to do
  with it.
- [`nr-workbench-project`](../nr-workbench-project/SKILL.md) for where files
  belong and which command does what.

### 4. Say what you established

One line, so the scientist can correct you before the work rather than after:

> Working in `/home/you/beamtime` on sample `Sample4`.

## Rationalizations

Each of these has been the reasoning behind a real mess:

- *"They obviously want a project here, I'll just run `nrw init` and mention
  it."* — Obvious to you, in a directory you did not choose. Seventy files,
  and a project root that shadows the real one.
- *"`samples/S9` doesn't exist, so I'll `mkdir` it."* — A sample is not a
  directory. It is `sample.md`, `sample.yaml` and seven subdirectories, and
  `scan`, `handoff`, `fit` and `report` all key off them. `nrw sample new`
  exists for exactly this.
- *"They said sample 4, and `samples/S4` isn't there, so I'll create it."* —
  `Sample4`, `S4` and `sample4` are three different directories and the
  project already contains one of them. Ask.
- *"I'll write the refl1d script directly, it's faster than the spec."* —
  Faster to the first number, and the number is unattributable. That is the
  failure this whole project was built to end.
- *"Preflight is ceremony; the error message would have told me anyway."* —
  It would have told you *after* you had copied data into the wrong place.
- *"I already checked earlier in this session."* — If a command since then
  changed the project, or the scientist switched samples mid-conversation, you
  checked something else.

## Red Flags

Stop if you catch yourself:

- typing `mkdir`, `touch` or `cp` anywhere under `samples/`;
- running `nrw init` in a directory whose suitability nobody confirmed;
- answering *"where should I put this data?"* without having run a command
  that says;
- picking between `S4` and `Sample4` by reading the request again rather than
  by asking;
- reporting progress on a sample you have not seen in a command's output;
- treating "no such sample" as a problem to route around rather than a
  question to ask.

## Verification

Before you take any action that writes:

- [ ] You can name the project root, and it came from a command's output.
- [ ] You can name the sample id, and it appeared in a command's output —
      not only in the scientist's message.
- [ ] Anything that had to be created was created by `nrw init` or
      `nrw sample new`, after the scientist said yes.
- [ ] You told them which project and which sample you are working in.

Afterwards, both facts are checkable:

```bash
nrw doctor              # names the project root and counts the samples
nrw handoff <sample>    # succeeds, rather than listing the ones that exist
```
