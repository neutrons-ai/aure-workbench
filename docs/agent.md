# Running the harness unattended

Overnight during a beamtime, data keeps arriving and nobody is watching. This
is how to set nr-workbench up so a coding harness works the queue by itself,
and how to decide whether you want that.

Read the first section even if you skip the rest. It is the part that explains
why this looks the way it does.

---

## What is and is not automated

nr-workbench does not contain a decision policy, and that is deliberate. We
measured. The reference experiment — `apr2025/cu-thf-expt11`, one week of
expert analysis, 25 fits, 17 findings written down as they happened — was
replayed against every automatic check in this package. Of the 17 findings:

| | Count | Meaning |
|---|---|---|
| Reachable by arithmetic | 1 | A program can find it and say what it means |
| Signal detectable, conclusion not | 5 | A program can point at it; a person says what it is |
| Needs judgement | 11 | No artifact on disk distinguishes the right answer |

Two results from that corpus say the same thing from the other side:

- **χ² ranks it backwards.** Both promoted fits are *worse* in χ² than the best
  fit in their arm (1.285 against 1.193; 1.698 against 1.411). A loop that
  optimises χ² gets both of the decisions that produced the paper wrong.
- **A pinned parameter implies three different correct actions.** In one fit,
  three parameters sat on their bounds. `Ti.rho` at −2.0 is bulk titanium and
  widening it would have destroyed the result — it *was* the result.
  `CuOx.rho` on its floor meant the material assignment was wrong.
  `Cu.roughness` carried no information at all. Nothing in the artifacts tells
  them apart.

So the checks report; the harness decides; you promote. What this package adds
around the harness is four things: **observations** it reads before it starts,
**limits** it cannot talk past, a **bounded session**, and a **transcript**.

---

> **Want the walkthrough instead?**
> [docs/getting-started-with-agent.md](getting-started-with-agent.md) does the
> setup step by step on real data — one sample first, then the overnight case.
> This page is the reference.

## Setting it up

### 1. Scaffold or upgrade the project

```bash
nrw init            # in an existing project, this is safe and idempotent
```

That writes `.claude/settings.json`, which is where the limits live. A project
scaffolded before this existed will not have it; `nrw init` adds it without
touching anything you have edited. Check with:

```bash
nrw doctor
```

```
  ✓ harness       2.1.156 at ~/.local/bin/claude
  ✓ agent limits  PreToolUse hook + 2 deny rule(s)
```

Those two lines fail independently, and the combination worth noticing is a
harness with no limits — `nrw agent run` works and nothing stops a promotion:

```
  ✓ harness       2.1.156 at ~/.local/bin/claude
  ! agent limits  no .claude/settings.json; run `nrw init` to add the hook
                  that refuses promote, --upload and --force
```

### 2. Say what you want fitted

An unattended session reads `## Fits to perform` in the sample's `sample.md`
and does that and nothing else. **With that section empty, `nrw agent run`
refuses to start.** This is the most important line in the design: an agent
that chooses for itself what is interesting is the failure everything else is
arranged against, and the only reliable place to stop it is before it begins.

Write it in words, not in spec syntax:

```markdown
## Fits to perform

Co-refine the two OCV states with the tNR run measured between them. The Cu
layer should thicken monotonically — a linear-in-time constraint on
Cu.thickness. I do not know whether the oxide is real; try it both ways and
tell me which the data supports.
```

Everything in `sample.md` matters here, not just this section — the
measurement table is what the header checks are compared against, and the
alignment notes are what decide whether `probe.theta_offset` is `per: model` or
`per: state`.

### 3. Look at the prompt before you trust it

```bash
nrw agent run Sample4 --dry-run
```

This composes the session and prints it without running anything. You get the
declared task, the offline observations, the skills it will read, and the
limits — the whole of what the harness will see. Reading it once is worth more
than any amount of configuration.

### 4. Run one session

```bash
nrw agent run Sample4
```

```
Session finished (exit 0); transcript .nrw/agent/20260810-231402Z-Sample4.jsonl
  ! ESCALATIONS.md exists -- read it before promoting anything
```

This is one session over one sample, and it exits. It does not wait for
anything — if a measurement's files are still arriving, it says so in the
prompt and lets the agent work around it rather than refusing, because you
asked for the session and may know something the timestamps do not.

If you want the waiting *and* a single session — the usual beamtime shape,
"analyse this one as soon as it is complete, then stop":

```bash
nrw agent watch Sample4 --max-sessions 1
```

That polls until the measurement has settled, runs one session, and returns
immediately afterwards rather than sitting out another poll interval.

The prompt and the full transcript are kept under `.nrw/agent/`. Options:

| | |
|---|---|
| `--dry-run` | Compose and print; start nothing |
| `--turns N` | Cap on harness turns (default 60) |
| `--model NAME` | Model to run (default: the harness's own) |
| `--timeout SECONDS` | Kill the session after this long |

---

## The limits, and why there are two of them

Three actions are refused: `nrw promote`, `nrw isaac export --upload`, and any
`--force`.

The first two are obvious. The third is the one worth explaining: **every
forcing flag in nr-workbench exists because a check said no** — drifted inputs,
a hand-edited script, an identical run already recorded, a directory somebody
else wrote. An agent reaching for `--force` has arrived at exactly the
situation a person is meant to see.

Two independent mechanisms enforce them:

1. **A `PreToolUse` hook** in `.claude/settings.json` running `nrw agent
   guard`. It exits 2, which blocks the call before it runs and shows the model
   why. It splits on `&&`, `;` and `|` first, so `nrw ls && nrw promote abc`
   does not slip past.
2. **`NRW_AGENT=1`**, which `nrw agent run` sets on the session itself. Under
   it, all three refuse from the inside — `--force` is checked once at the top
   of the CLI rather than in each command, so a subcommand added next year is
   covered without anyone remembering to.

Both, because a hook can be misconfigured and an environment variable can be
unset, but both failing silently at the same time is a different order of
accident. Neither is a sandbox — a determined process can do anything the user
can. They stop the plausible mistake, which is the one that actually happens.

The guard reads the whole command line, not its first few words. These are all
refused, and every one of them was allowed by the first version:

```bash
if true; then nrw promote abc --reason x; fi
for f in *.py; do nrw fit run $f --force; done
cd samples/S1; ls; nrw fit run m.py --force
A=1 nrw promote abc
python -m nr_workbench.cli promote abc
bash -c 'nrw promote abc'
```

None of those is obfuscation — they are how batched shell work looks when an
agent writes it. Things that only *mention* a refused word still run:
`grep promote src/`, `echo 'do not promote this'`, `nrw ls --sample
promotion-study`.

Every refusal names `ESCALATIONS.md`, at the project root. An agent that is
only told "no" retries; one that is told where to write the decision has
somewhere to put it. Read that file first in the morning.

To check any command without running it:

```bash
nrw agent guard --command "nrw promote abc123 --reason 'best chisq'"
```

### Giving it more room

`.claude/settings.json` is yours to edit. Removing the hook entry removes the
first mechanism; unsetting `NRW_AGENT` would remove the second, but `nrw agent
run` sets it deliberately, so the honest way to allow promotion is to promote
by hand after reading what the session did. That is the workflow this is built
for.

---

## What the session reads before it starts

Every check below is deterministic, offline, and needs no model. They run
during `nrw agent run` and their output goes into the prompt. You can run each
one yourself:

| Check | Command | Catches |
|---|---|---|
| Headers against `sample.md` | `nrw data reconcile <sample>` | A run mislabelled in the notes; a different direct beam; a time-resolved reduction filed as a steady state |
| Spec self-consistency | `nrw check --contradictions` | A constraint whose `form` contradicts the measured trajectory; roughness ranges that permit σ > t/4; parameters silently held at scaffold defaults |
| Fit assessment | `nrw assess <fit-id>` | Parameters on bounds, unconstrained posteriors, ρ–t correlations above 0.8, a layer swallowed by its own interfaces |

This is where automation earns the most. In the reference experiment, the
errors that cost the most time were visible in the file headers **before the
first fit ran** — a run written up as OCV that was not, which sent five fits
down the wrong path, and a 27.6% segment normalisation difference. Nothing was
looking at them. These checks help whoever is driving, agent or not.

---

## Running all night: `nrw agent watch`

`nrw agent run` is one session. The watcher decides *when* to start one and
over what, and nothing else — it is a scheduler, not a second decision-maker.

```bash
nrw agent watch                     # every sample
nrw agent watch Sample4 Sample7     # or a few
nrw agent watch --dry-run           # what each measurement is waiting for
```

The dry run is the one to look at first. On the reference experiment:

```
expt11
    run 218386 (steady)  done         already fitted
    run 218389 (steady)  quarantined  run 218389 appears as both a steady-state
                                      measurement and a time-resolved series.
                                      One of the two is a filing accident, and
                                      which one is a question for a person.
    run 218393 (steady)  done         already fitted
    run 218397 (steady)  done         already fitted
    run 218389 (series)  done         already fitted
```

That is a finished experiment: nothing to do, and one run held back. Note run
218389 appearing twice — the real 130-slice series *and* a stray whole-run
reduction of it that was filed under `data/steady/`. The watcher reports both,
because the pair is itself the fingerprint.

Quiet time is read from the files' own modification times, so a single poll
tells the truth. A `--dry-run` that had to watch for changes across polls would
report everything as "arriving" no matter how old it was.

### Three questions, and only three

**Has it finished arriving?** A steady-state measurement is three angle
segments written minutes apart. A daemon reacting to file *creation* fits the
first segment alone and gets a perfectly plausible answer out of a third of the
data. So a run is started only once its files have been unchanged for
`--settle` seconds (default 300). Polling, not `watchdog`: these land on NFS,
and reduction takes minutes anyway.

For a time-resolved series, counting slices says nothing — they arrive one at a
time across the whole electrochemistry. The reduction sidecar is the only thing
that knows how many to expect, so the series is complete when every interval it
names has a file.

**Is it trustworthy?** Any one of three fingerprints quarantines a run:

- it appears as both a steady-state measurement and a time-resolved series;
- its angle segments are not contiguous from 1;
- a segment's subrun is not `run + segment - 1`.

The asymmetry is deliberate. A run wrongly held back costs one question in the
morning; a run wrongly fitted costs a night. The third fingerprint is why the
watcher parses filenames rather than counting files —
`REFL_218386_2_218387_partial.txt` is the second angle segment of run 218386,
and a program that never reads that string cannot notice when it is wrong.

A quarantined run is also named in the session prompt, since a session is
per-sample while the quarantine is per-run: the watcher can hold one
measurement back and still start a session over the sample containing it.

**Has anybody looked at it yet?** Read off the recorded fits, not a state file.
A state file is a second source of truth that goes stale the moment you fit
something by hand, and then the daemon repeats work you already did.

### Options

| | |
|---|---|
| `--dry-run` | Report what each measurement is waiting for; start nothing |
| `--settle N` | Seconds files must be unchanged (default 300) |
| `--poll N` | Seconds between polls (default 60) |
| `--max-sessions N` | Stop after N sessions (default: until interrupted) |
| `--session-timeout N` | Kill one session after N seconds (default 7200) |
| `--turns N`, `--model NAME` | Passed through to each session |

One session at a time, and never a second for a sample whose first is still
running. Concurrency buys nothing here — the beam is slower than the fits — and
costs the thing that matters, which is a transcript you can follow.

---

## The agent does not need an LLM endpoint

nr-workbench can be pointed at a language-model endpoint (`LLM_PROVIDER`,
`LLM_API_KEY`; `nrw doctor` reports what it sees). Three commands use it: `nrw
assess` asks whether fitted values look physically sensible, `nrw model new
--from-notes` proposes a stack, and `nrw isaac export` writes condition
sentences.

**None of them consults it while an agent is driving.** Under `NRW_AGENT=1`
each hands the work to the harness instead:

| Command | Endpoint configured, no agent | Agent driving |
|---|---|---|
| `nrw assess` | the endpoint judges | reports the measurable findings and says the judgement is yours |
| `nrw model new --from-notes` | the endpoint proposes a stack | scaffolds the spec and prints the instruction, as `--print-prompt` does |
| `nrw isaac export` | the endpoint writes condition sentences | uses the measurement table; write the conditions there |

This is not caution about the endpoint. It is that **the harness is already a
language model, and the better one.** Sending the same question to a second,
weaker model and handing its answer back does not add a check — it replaces
the judgement you wanted with a worse one, and then presents it as evidence
inside the harness's own context.

The consequence worth stating plainly: **you do not need an endpoint
configured to run the agent.** The three commands above work; they simply
route the judgement to the thing already doing the judging. An endpoint is
still useful when nobody has a harness open.

---

## Without a Claude Code subscription

Two different things get called "bringing your own model", and only one of
them substitutes for the harness.

### An endpoint is not a harness

The `LLM_BASE_URL` / `LLM_API_KEY` endpoint nr-workbench can use is a
completions API: you send text, you get text back. A harness is a tool-using
loop — it reads a file, runs `nrw fit run`, looks at what came out, and
decides what to do next. You cannot substitute the first for the second
without writing the loop in between, and writing that loop is precisely what
this package does not do, because the measurement says a good harness beats
one we would write.

So `nrw agent run` needs a harness. `LLM_BASE_URL` will not stand in for it,
and the error says so rather than leaving you to conclude the install is
broken.

### You may not need a subscription

Claude Code is not subscription-only. It authenticates with an
`ANTHROPIC_API_KEY` (billed per token), and it runs against **Microsoft
Foundry**, **Amazon Bedrock** and **Google Cloud's Agent Platform (Vertex)**
using your organisation's own cloud credentials and billing. If your
institution already has an Azure, AWS or GCP account with Claude models
enabled, that is enough.

All of it is selected by environment variables, and `nrw agent run` passes the
**whole environment** through to the harness. So there is nothing to configure
in nr-workbench: set the provider variables in your shell (or in the daemon's
unit file — see the warning below) and the agent uses them.

#### Microsoft Foundry

In the [Foundry portal](https://ai.azure.com/), create a resource and a
deployment for each Claude model you want, noting the deployment names. Then:

```bash
export CLAUDE_CODE_USE_FOUNDRY=1
export ANTHROPIC_FOUNDRY_RESOURCE=your-resource-name
export ANTHROPIC_FOUNDRY_API_KEY=your-azure-api-key
```

Omit the API key to use Microsoft Entra ID instead — Claude Code falls back to
the Azure default credential chain, so `az login` works locally and a managed
identity works on a server.

**Pin your model versions.** Foundry has no startup model check, so an
unpinned alias that is not deployed in your account fails at the first
request rather than at launch. Set these to *your deployment names*:

```bash
export ANTHROPIC_DEFAULT_OPUS_MODEL='claude-opus-4-8'
export ANTHROPIC_DEFAULT_SONNET_MODEL='claude-sonnet-5'
export ANTHROPIC_DEFAULT_HAIKU_MODEL='claude-haiku-4-5'
```

This matters more for a beamtime than for interactive use: a wrong model name
surfaces at 2am as a session that failed on turn one.

#### Amazon Bedrock

```bash
export CLAUDE_CODE_USE_BEDROCK=1
export AWS_REGION=us-east-1
```

Authentication uses your normal AWS credentials.

#### Google Cloud's Agent Platform (Vertex)

```bash
export CLAUDE_CODE_USE_VERTEX=1
export CLOUD_ML_REGION=us-east5
export ANTHROPIC_VERTEX_PROJECT_ID=your-project-id
```

#### Behind a gateway or proxy

A corporate proxy is `HTTPS_PROXY`. An LLM gateway is `ANTHROPIC_BASE_URL`, or
the provider-specific `ANTHROPIC_FOUNDRY_BASE_URL` /
`ANTHROPIC_BEDROCK_BASE_URL` / `ANTHROPIC_VERTEX_BASE_URL`. With Bedrock or
Vertex, add `CLAUDE_CODE_SKIP_BEDROCK_AUTH=1` or
`CLAUDE_CODE_SKIP_VERTEX_AUTH=1` when the gateway handles cloud auth itself.

#### Check it before the beam does

Run `claude` interactively once and type `/status`. It names the provider and
the resource or base URL it resolved. Do that before an overnight run; a
provider misconfiguration looks exactly like a broken agent from the outside.

> **A daemon has no shell profile.** `nrw agent watch` started from systemd,
> launchd or cron does not read `.bashrc` or `.zshrc`, so provider variables
> set there are invisible to it. Put them in the unit file, the plist, or a
> wrapper script named by `NRW_HARNESS`. This is the most likely reason an
> agent that works by hand does nothing overnight.

These variable names are current as of writing and come from Claude Code's own
documentation — [Microsoft Foundry](https://code.claude.com/docs/en/microsoft-foundry),
[Amazon Bedrock](https://code.claude.com/docs/en/amazon-bedrock),
[Vertex](https://code.claude.com/docs/en/google-vertex-ai),
[LLM gateways](https://code.claude.com/docs/en/llm-gateway). Those pages are
the authority; this one will go stale first.

#### Which model the session uses

`nrw agent run --model NAME` passes straight through to the harness. On a
third-party provider that is a *deployment name*, not an Anthropic model ID.
Leave it off and the harness uses whatever your `ANTHROPIC_DEFAULT_*_MODEL`
pinning resolves to.

### Or point us at a different harness

```bash
export NRW_HARNESS="my-agent"                       # a name or a path
export NRW_HARNESS="claude --settings /etc/site.json"   # or a command
export NRW_HARNESS="$HOME/bin/harness-wrapper"      # or a wrapper script
```

`nrw doctor` reports what resolved, and marks it when the override is in play:

```
  ✓ harness       2.1.156 at ~/.local/bin/claude --settings /etc/site.json  [NRW_HARNESS]
```

**What we promise, and what we do not.** Claude Code is the only harness this
is tested against. The contract your command has to meet is:

```
<harness> -p @<prompt-file> --max-turns N --output-format stream-json --verbose
          --permission-mode bypassPermissions [--model M]
```

…reading the prompt from the named file, and emitting Claude Code's
newline-delimited JSON events on stdout. A wrapper script that translates
those arguments is the intended seam, and one was verified end to end.

If your harness emits a different event format, everything still works except
the progress lines, which go quiet — `describe_event` returns nothing for a
shape it does not recognise rather than failing. The transcript, the fits and
the records are unaffected, because those come from `nrw` commands, not from
the harness's output.

**The two limits do not depend on the harness.** `NRW_AGENT=1` is set on the
session whatever runs it. The `PreToolUse` hook is Claude Code's mechanism, so
a different harness must provide its own equivalent — if yours cannot, the
environment variable is the only layer left, and `nrw agent run` will still
refuse to start without a configured hook. That refusal is deliberate; read it
as "this harness is not yet set up safely", not as a bug.

### If you only have an endpoint

Everything except `nrw agent run` and `nrw agent watch` works exactly as
documented, by hand — and the endpoint powers `nrw assess`'s judgement,
`nrw model new --from-notes`, and `nrw isaac export`'s condition sentences,
since those only step aside when a harness is driving.
[docs/getting-started.md](getting-started.md) is that workflow end to end.

---

## Permissions, and why the hook still holds

The harness runs with `--permission-mode bypassPermissions`. That is not a
shortcut; without it an unattended session accomplishes nothing.

Measured: a 45-turn run with only a `deny` list configured spent every turn
being told **"This command requires approval"** — headless mode has nobody to
approve — and then tried to write itself a `.claude/settings.local.json` to
get out. Not one `nrw` command ran.

Bypassing turns off Claude Code's permission layer, including the `deny` rules
in `.claude/settings.json`. It does **not** turn off the two mechanisms this
package relies on, which was verified under that exact flag:

```
CALL:   Bash {"command": "nrw promote abc123 --reason test"}
RESULT: PreToolUse:Bash hook error: Refused by nr-workbench (promote): …
```

Hooks are independent of permissions, and `NRW_AGENT=1` refuses from inside
`nrw` regardless. The `deny` list was always the weakest of the three layers,
and the only one that needed a person at a keyboard to mean anything.

Because the hook is now load-bearing, `nrw agent run` **checks it before every
session** and refuses to start without it. A session can edit
`.claude/settings.json`; checking once would leave the next session
unprotected, and a previous session having removed it is worth knowing before
you trust what it wrote.

---

## Compute, during a beamtime

The session is told to fit with `--method amoeba` while the beam is running and
to use DREAM only once a fit is good and uncertainties are wanted. That matches
how this is done by hand and by AuRE, and the reason is throughput: amoeba
keeps pace with arriving data, DREAM does not. One fit at a time.

Nothing enforces this — it is guidance in the prompt, not a limit, because a
harness that decides a long DREAM run is the right call at 2am is probably
right.

---

## When it goes wrong at 3am

The watcher is the only part designed to run for eight hours with nobody
watching, so its failure handling is deliberate rather than incidental:

- A **poll that raises** — a sample directory removed mid-run, an NFS `stat`
  that failed — is reported and retried. It does not end the night.
- A **measurement that cannot be assessed** is quarantined with the exception
  in its reason, not skipped silently and not fatal to the other samples.
- A **session that hangs** is killed at `--session-timeout`, and killed as a
  process group, so the `refl1d` fit it started dies with it rather than
  carrying on writing into the project.
- A **session that fails** for any other reason is reported and the loop moves
  to the next sample.

What is *not* handled that way: a run whose files look incoherent. That stops
being fitted entirely and waits for you. A false quarantine costs one question
in the morning; a false pass costs the night.

---

## Reading it in the morning

The output rule is one page per sample under `samples/<id>/reports/`, rewritten
rather than appended, plus `ESCALATIONS.md` at the root. This is a property of
the design, not a preference. Forty individually defensible records are
collectively unreadable, and once you stop reading them, every other safety
property here is a formality.

The order to read in:

1. `ESCALATIONS.md` — what it could not decide.
2. `nrw ls --sample <id>` — every fit, newest first, with what changed between
   each and the one before.
3. `samples/<id>/reports/` — what it concluded.
4. `nrw whence`, `nrw diff`, `nrw assess` — anything you want to check.

Then promote by hand, with a reason:

```bash
nrw promote 20260810-231402Z-4f2a8c1e --as final \
    --reason "oxide layer is real; excluding it costs 0.4 in chisq and leaves a residual at 0.08"
```

The reason is the part that matters in a year, which is most of why this step
is still yours.

---

## Attribution

There is no agent badge on a fit record, on purpose. `provenance.command`
already records the exact command line that produced every fit, so the record
answers "what produced this" without a new field — and a flag saying
`agent: true` would invite exactly the wrong habit of trusting a record more
because a person's name is on it. Judge the fit by the fit.

---

## What this deliberately does not do

- **Decide anything.** No Python scoring loop. The evidence above says a
  harness is better at this, and a worse copy of it inside nr-workbench would
  be used instead of the good one.
- **Promote or upload.** Both refused, twice.
- **Rewrite `sample.yaml`.** `nrw sample scan` stays a human gesture — it is
  the one that removes a mis-filed run from the register, which is a curation
  decision.
- **Offer an MCP surface.** The CLI is the tool surface and the harness already
  drives it; a second surface is drift with no new capability.
