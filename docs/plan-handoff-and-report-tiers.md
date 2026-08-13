# Plan: the interactive handoff, and three-tier reporting

Two changes to nr-workbench, both motivated by measurements taken from the
`jen-jun2026` / `sample1` run: `nrw agent run sample1` unattended, then an
interactive Claude Code session that took the analysis to publishable form.

Nothing here is to be applied to `jen-jun2026`. It is written to be executed
upstream and then tested in a fresh project.

> **Status: implemented.** Everything in parts B and C is built and tested;
> the surface it produced is listed under "What was built" at the end, along
> with the two places the plan was wrong. Part D is the acceptance test, and
> is still to run.
>
> One finding changed a design during implementation and is worth recording
> here because the plan asserted the opposite: **harness settings do not
> interpolate `${PATH}`** in an environment value. The string is passed through
> literally, verified by probing a real session. Writing `"<bin>:${PATH}"` as
> B2 originally proposed would have set `PATH` to that literal string and
> broken every command in the project.

---

## Part A — What the reference run actually cost

Numbers from the interactive takeover session's transcript under
`~/.claude/projects/` (268 tool calls), and from the artefacts it left behind.

**A1. Orientation was ~28 tool calls before the first substantive action.**
The whole prompt was one sentence: *"Let's pick up where our nrw agent left off
… co-refine 230539, 230546 and 230550."* Calls 1–28 were `ls`, `sample.md`,
`nrw ls`, the report, four spec files, three skills, and environment probing.

**A2. Ten of those 28 were spent finding the `nrw` binary, and the cost did not
stop there.** `nrw` was not on the PATH of the shell Claude Code spawns. Calls
5, 7, 9, 11–16 searched `~/.venv*`, `~/.pixi/bin`, `~/.zshrc`, `~/git`, and
finally landed on the `nrw` inside a checkout's `.venv/bin`. After that,
**136 of 268 tool calls (51%) carried an `export PATH=…` prefix or an absolute
binary path.** This is the single largest mechanical tax in the session and it
is fixable in the scaffold.

**A3. The state a takeover has to absorb is already computed — and thrown away.**
`nr_workbench/agent/session.py::observe()` builds exactly the digest a fresh
interactive session needs (data vs `sample.md`, quarantined runs, existing
specs, recorded fits, the latest assessment, missing skills, reports already
written). It is composed into the unattended prompt at `.nrw/agent/*-prompt.md`
and is reachable by no command. The interactive session re-derived it by hand.

**A4. `ESCALATIONS.md` was the highest-value file in the project and there is no
rule that says read it first.** It carried two generator bugs (`fixed: true`
silently emits `value=1.0` for *any* value — see items 4 and 4a), a
normalisation offset worked around in-model, a quarantined run, and a retracted
conclusion. A takeover that writes a spec before reading it reproduces the bug.

**A5. The human's six steering messages say what the tooling should have
handled.** In order:

| # | What the human had to say | What it reveals |
|---|---|---|
| 2 | "we should consider adding a background to the models" | a systematics check the agent did not run |
| 3 | "this would have to be a separate report … don't touch the report currently in the reports folder" | **report immutability is not enforced**; the human had to defend a finished artefact by hand |
| 4 | "something might have happened to the film before 230553" | expert hypothesis injection — the thing an expert user is *for* |
| 5 | "Don't forget that fitted parameter table" | the parameter table is not part of any report scaffold |
| 6 | "add the nice plots you made earlier … that was really good" | figures produced by ad-hoc `reports/*.py` scripts are not carried into reports and have no provenance |

**A6. One report, three audiences, written by hand.**
`sample1-what-the-fits-show.md` (368 lines) is simultaneously a peer-level SI
draft and an expert argument, and it explains a few concepts in passing (why a
sub-electrolyte dip cannot be faked; why the slab split is degenerate) with no
system behind which concepts get explained. There is no newcomer entry point at
all. `nrw report`'s `PROMPTS` tuple is audience-blind.

**A7. The project has no idea who it is talking to.** Nothing in `nrw.toml`,
`sample.md` or the scaffold records whether the reader is a reflectometrist, a
statistician, an electrochemist, or a student — so every session guesses, and
every report is pitched at whoever the model imagines.

---

## Part B — Improvement 1: the interactive handoff

Goal: a takeover session's first *three* tool calls put it where call 28 was.

### B1. `nrw handoff [<sample>]` — expose the digest that already exists

- New `src/nr_workbench/commands/handoff.py`, wired as a top-level command.
- Reuse `agent.session.observe(root, sample)` verbatim. Do not fork it — the
  two consumers must not drift.
- Add, beyond `observe()`:
  - the resolved `nrw` executable path and how to put it on PATH (B2);
  - `ESCALATIONS.md` **in full**, first, under a heading that says it is the
    first thing to read and that its items may still be live;
  - `nrw check` status inline (stale inputs, undocumented fits);
  - the last unattended session: transcript path, exit status, wall time,
    whether it was cut off by `--turns` or `--timeout`;
  - the audience block from `nrw.toml` (B4);
  - the reading order (B3), as a numbered list, with the skills named.
- `--json` for machine consumption; `--brief` for the top ~40 lines.
- Sample argument optional: with none, list samples and their state.
- Update `docs/getting-started-with-agent.md` so the documented loop ends with
  `nrw handoff <sample>` rather than "open Claude Code".

### B2. Kill the PATH tax (A2) — this is the cheapest win in the plan

- `nrw init` resolves the absolute path of the running `nrw` and writes it into
  a **`.claude/settings.local.json`** (and the Copilot equivalent), as an `env`
  entry prepending its directory to `PATH`.
  - `settings.local.json`, not `settings.json`: it is machine-local and already
    gitignored, so provenance rule 3 (no absolute paths in committed files) is
    not broken.
  - Add it to the scaffold's `.gitignore` template if it is not already there.
- `nrw doctor` gains a check: *is `nrw` resolvable from a bare non-login shell?*
  It must actually spawn `sh -lc 'command -v nrw'` rather than inspecting
  `sys.executable`, because the failure mode is specifically the shell the
  harness spawns. Failure message names the fix and the file.
- `nrw handoff` prints the resolved path in its first five lines regardless, so
  even a project scaffolded before this change costs one call instead of ten.

### B3. New bundled skill: `analyst-handoff`

Path: `src/nr_workbench/skills/reflectometry/analyst-handoff/SKILL.md`.
House anatomy (Overview / When to Use / Process / Rationalizations / Red Flags /
Verification). Dispatchers in `.claude/agents/` and `.github/agents/` are
generated by `skills_install.py` — no extra work. Add to `SEED_SKILLS` in
`commands/init_cmd.py` so it installs at `nrw init`.

Contents, in order:

1. **Run `nrw handoff <sample>` first.** One call. Everything below assumes it.
2. **Reading order, and why each file exists.** `ESCALATIONS.md` → the reports →
   `sample.md` → `nrw ls` → the specs of the fits you intend to build on. Never
   the results directories first: they are the most numerous and least
   informative per byte.
3. **Read `ESCALATIONS.md` before writing any spec.** State the rule as a rule.
   Include the `fixed: true` case as the worked example of why: a live tooling
   bug recorded there, invisible in `validate` and `preview` output.
4. **What is already true and must not be re-derived.** Every recorded fit is
   immutable and its `NOTES.md` says what it was for. A branch that looks
   unexplored may have been rejected — check the reports before re-running it.
   Disagree in writing with evidence; never silently re-fit to a different
   answer. (This mirrors the unattended prompt's "If work has already been done
   here", which interactive sessions never see.)
5. **What the unattended agent structurally could not do**, so the interactive
   session knows where the real work is: `nrw promote`; anything needing a
   `--force`; anything requiring a judgement call the escalations name; and —
   from A5 — systematics sweeps (background, resolution, scale) that need
   someone to decide they are worth the fits.
6. **Existing reports are finished artefacts.** New work goes in a new report
   (B5, C7). Never edit a report you did not write this session without being
   asked.
7. **How to work with this user** — a pointer into the audience block (B4),
   with the concrete behaviours for each level.
8. **Verification:** you have taken over correctly when you can state, before
   your first fit: the sample's question, which fits answer it today, which
   escalations are still open, and what your first fit adds. If you cannot,
   you are still orienting.

### B4. Record who the user is: `[audience]` in `nrw.toml`

Three independent axes plus free text. They are independent in practice — the
reference user is a reflectometry expert who wanted the Bayesian reasoning
spelled out — so a single novice/expert dial would be wrong.

```toml
[audience]
reflectometry = "expert"      # newcomer | practitioner | expert
statistics    = "practitioner" # newcomer | practitioner | expert
domain        = "expert"      # the science: electrochemistry, polymers, …
role          = "drives"      # drives | collaborates | delegates
notes = "Wants the model-comparison arithmetic shown. Will inject hypotheses."
```

- `nrw init` prompts for these when interactive, defaults to
  `practitioner`/`collaborates` when not, and always writes the block with the
  comment explaining each value.
- `nrw audience [--set axis=value]` to read and edit without hand-editing TOML.
- Consumed by: `nrw handoff` (printed), the `analyst-handoff` skill (how much to
  explain, how often to check in, whether to propose or ask), the unattended
  prompt (`_prompt()` gains an audience line), and the report tiers (Part C —
  which tier is the primary deliverable, not whether the others exist).
- Behavioural mapping, stated in the skill so it is not improvised:
  - `role = drives` → propose, do not ask; the human will redirect. Short
    check-ins after each fit.
  - `role = delegates` → itemized plan up front, confirm before each fit
    sequence, and every number gets a plain-language gloss.
  - `statistics = newcomer` → never quote a σ without saying what it means
    here; prefer the tail probability to the σ when they disagree (as they did
    at −1 V in the reference run).
  - `reflectometry = newcomer` → name the invariant before the slab parameter,
    and always show the SLD profile before the reflectivity curve.

### B5. Small enforcement that removes steering message #3

- `nrw report` already refuses to overwrite prose without `--force`. Extend the
  refusal message to name the alternative: `nrw report <sample> --topic <slug>`
  for a second, independent report.
- Add `nrw report supersede <slug> --by <slug> --reason "…"`, by analogy with
  `nrw model deprecate`: writes a banner at the top of the old report rather
  than deleting it. The reference run's retracted escalation item is the proof
  that superseding-with-the-reasoning-preserved is the behaviour that is wanted.
- Add the `--force`-on-a-report case to the agent guard's refusal list.

---

## Part C — Improvement 2: three report tiers, always produced

One analysis, three renderings, because the reading team is mixed. All three are
always written; the audience block decides which one is named first, not which
ones exist.

### C1. Naming and layout

Flat files sharing a stem, so `reports/*.md` globs keep working:

```
samples/<id>/reports/
  <stem>-technical.md   # the expert record: every branch, all the arithmetic
  <stem>-si.md          # peers; drops into a paper's Supporting Information
  <stem>-plain.md       # newcomer: the conclusion, plus the concepts to read it
  <stem>-figures/       # PNGs referenced by more than one tier (C6)
```

Each carries a `<!-- nrw:tier technical|si|plain stem=<stem> -->` marker on line
3 so tooling can tell them apart without parsing filenames.

### C2. `nrw report` writes all three

- Refactor `PROMPTS` in `commands/report.py` into
  `TIERS: dict[str, tuple[tuple[str, str], ...]]`.
- Default invocation scaffolds all three and reports the three paths.
- `--tier <name>` scaffolds or refreshes one.
- The generated `<!-- nrw:sequence -->` table goes into **technical** in full,
  into **si** trimmed to the reportable DREAM fits only, and into **plain** not
  at all — replaced by a one-line count and a pointer.
- `--force` refresh keeps working per tier, prose untouched.

### C3. Tier scaffolds

**technical** — the record a reviewer or a future analyst argues with:
question · what the offline checks found before any fit · the model, and the
justification for every `per:` scope · the fit sequence with **every abandoned
branch and the evidence that abandoned it** · the statistics, arithmetic shown
(χ²_red, the √χ²_red interval inflation, the BIC difference with `k·ln(n)`
written out, correlation coefficients for every pair above 0.9, posterior shape
where σ and tail probability disagree) · systematics tried and their effect
(background, resolution, scale offsets) · what the data do not support · what
would change the answer · provenance appendix (fit ids, data hashes, environment,
`nrw pack` command).

**si** — drops into a paper: methods paragraph (instrument, geometry, reduction,
the FWHM `dQ` convention stated explicitly) · model description and the fitted
parameter table with inflated intervals — **the table is part of the scaffold**,
which removes steering message #5 · results · the model-comparison table ·
caveats · data and code availability, naming `nrw pack`.

**plain** — for the colleague who owns the chemistry but not the fitting:
what we wanted to know · what we found, in one paragraph with no symbols · how
to read the picture (the SLD profile figure, annotated) · **the concepts you need
to trust this** (C4) · what we are not sure about, and why not · what to read
next. Target 2–3 pages.

### C4. The concept-explainer library — the substantive new piece

The request: *"if two parameters are correlated and drives us to change the
parametrization, we should add a little paragraph explaining the statistics
concepts."* So the explanations must be **selected by what the analysis actually
did**, not by a fixed syllabus.

- Ship one 150–250 word plain-language explainer per concept, under
  `src/nr_workbench/reporting/concepts/<slug>.md`, each with frontmatter:
  `name`, `one_line`, `trigger` (how it is detected), `see_also`.
- Initial set, every one of which fired in the reference run:
  `parameter-correlation` · `degeneracy-and-invariants` (the contrast–thickness
  ridge; why quote total metal, not the Cu/CuOx split) · `chi-squared-reduced` ·
  `interval-inflation` · `bic-model-comparison` · `posterior-tails-vs-sigma`
  (the −1 V "2.5% or 8.7%" case) · `parameter-on-a-bound` ·
  `optimiser-vs-posterior` (amoeba explores, DREAM quotes) ·
  `resolution-and-dq-fwhm` · `back-reflection` · `null-hypothesis-fit` (why
  removing a layer on purpose is evidence) · `background-can-manufacture-a-layer`.
- **Selection is mechanical.** `fitting/assess.py` already detects correlated
  pairs, parameters on bounds, unconstrained posteriors and BIC. Add a
  `concepts: [slug, …]` list to the assessment JSON and to the `nrw:generated`
  block in `NOTES.md`. `nrw report --tier plain` unions the slugs across the
  fits the report cites and inserts those explainers.
- **Generated text explains the concept; it never states the conclusion.** Each
  inserted explainer is followed by a blank prompt: `<!-- and here is what it
  meant for this sample: … -->`. Same principle as the existing scaffold — the
  table is derivable, the analysis is not.
- `nrw report --concepts` lists what would be inserted and why, so the analyst
  can see the detection was right before writing around it.

### C5. Keeping the three from drifting

- `nrw report --check <sample>` verifies: all three tiers exist and share a
  stem; the headline sentence is byte-identical across tiers; every fit id cited
  in `si` or `plain` also appears in `technical`; no tier cites a fit that is
  stale or missing from the index; no number appears in `plain` that is absent
  from `technical`.
- Wire it into `nrw check` so `nrw check` fails a sample with one tier written
  and the others still scaffolding. This is the guard that makes "always produce
  all three" real rather than aspirational.

### C6. Figures get provenance (steering message #6)

The reference run's figures were made by ad-hoc scripts in `reports/*.py`
writing PNGs into `results/<fit_id>/figures/` — a good instinct with no support
behind it.

- Document the convention in `analysis-provenance`: a report figure script lives
  at `reports/<name>.py`, reads only from recorded result directories, and
  writes to `reports/<stem>-figures/`.
- `nrw report figure <script.py>` runs it, records the fit ids it read, stamps
  the PNG's provenance into a sibling manifest, and makes the output findable by
  `nrw whence`.
- The `plain` and `si` scaffolds reference figures by that path, so a figure the
  human liked is carried across tiers instead of being re-requested.

### C7. Feed the tiers back into the loop

- `agent/session.py::_prompt()` — "When you are done" now asks for all three
  tiers, and names `nrw report --concepts` for the plain one.
- `agent/session.py::_observe_report()` — **must be updated or it regresses.**
  With three files saying the same thing, the 6000-character `REPORT_CHARS`
  budget gets spent three times over. Read `technical` in full within budget,
  and reduce `si`/`plain` to their headline sentence plus a note that they
  exist. Same fix in `written_reports()`: a sample counts as reported when
  `technical` has prose, not when any of the three does.
- `web/app.py` — the fit/sample page gains a tier switcher.
- `nrw pack` — include all three tiers in the bundle.

---

## Part D — How we test this in a fresh project

Run the whole loop again on a different sample, and measure rather than judge.

1. `nrw init` a new project; confirm `.claude/settings.local.json` records
   where the binary lives, and that `nrw doctor`'s new shell check passes.
2. Answer the `[audience]` prompts. Deliberately choose a *different* profile
   from the reference user — e.g. `reflectometry = newcomer`,
   `statistics = practitioner`, `role = delegates` — so the adaptation is
   visible rather than assumed.
3. Copy data, write `sample.md` with a `## Fits to perform`.
4. `nrw agent run <sample>` unattended.
5. Start Claude Code with exactly one instruction: *"Take over from the nrw
   agent."* Nothing else. **Do not paste any context.**
6. Record the acceptance metrics:
   - tool calls before the first substantive action (target: ≤ 5, from 28);
   - calls carrying an `export PATH` or absolute binary path (target: 0, from
     136 of 268);
   - was `ESCALATIONS.md` read before the first spec was written (target: yes);
   - was any recorded fit re-run without a stated reason (target: no);
   - did the session need a "don't touch that report" instruction (target: no).
7. At the end, `nrw report <sample>` and check all three tiers exist, that
   `nrw report --check` passes, and that the `plain` tier's inserted concepts
   match what the fits actually hit.
8. Have someone who is *not* a reflectometrist read the `plain` tier cold and
   say back what the answer was. That is the only test of that tier that means
   anything.

## Sequencing

B2 first — it is an afternoon and it removes half the friction. Then B1 and B3
together, since the skill is written against `nrw handoff`'s output. Then B4,
which both parts consume. Part C last, and within it C4 is the piece worth the
most care: the concept library is the part that has no precedent to copy.

---

## What was built

### New commands

| Command | What it does |
|---|---|
| `nrw handoff [<sample>]` | The whole state of play for a session taking over. Reuses `agent.session.observe()` rather than reimplementing it. `--brief`, `--json`. |
| `nrw audience` | Read or set the `[audience]` block. `--ask` walks the axes, `--set axis=value`, `--guidance` prints what it asks an assistant to do. |
| `nrw doctor --fix-path` | Writes a literal `PATH` into machine-local harness settings, after showing exactly what it will write. |
| `nrw report --check [<sample>]` | Verifies the tiers agree. Also runs inside `nrw check`. |
| `nrw report --concepts` | Which explainers this sample's analysis selected, and on what evidence. |
| `nrw report --topic <slug>` | A second, independent report, so new work never overwrites a finished one. |
| `nrw report --tier <name>` | Write one tier rather than all three. |
| `nrw report-figure <script.py>` | Runs a report figure script and records the fits it read and the files it wrote. |
| `nrw supersede <sample> <old> --by <new> --reason` | Banners a report as replaced, in every tier, without deleting it. |

### New modules

- `project/toolpath.py` — locating `nrw`, the shim, `NRW_BIN`, and the
  scrubbed-shell probe.
- `project/audience.py` — the four axes, the comment-preserving TOML writer,
  and the behavioural mapping in `guidance()`.
- `commands/handoff.py` — the handoff renderer.
- `reporting/tiers.py` — the three tier definitions and their section prompts.
- `reporting/concepts.py` — the concept registry, mechanical detection, and
  the reading-order/weight budget.
- `skills/reflectometry/analyst-handoff/SKILL.md` — the takeover standard,
  seeded by `nrw init`.

### Where the plan was wrong

**B2's PATH mechanism.** See the note at the top. The implemented default is
additive and safe — a project-local shim plus a `NRW_BIN` variable — with the
destructive `PATH` override behind `nrw doctor --fix-path`, shown before it is
written.

**C4's detection would have been nearly inert.** The plan had concept
selection read `assessment.json`, a file that did not exist until this work
added it. Every project assessed before then — including the reference one —
would have got only the three coarse synthetic triggers. The fix is a fallback
that recovers finding kinds from the generated block already inside each
`NOTES.md`, scoped to the fences so an analyst's prose is not mistaken for a
finding. On the reference sample that is the difference between 4 concepts
detected and 12.

**C4 shipped prose it should not have.** The first implementation bundled
fourteen ready-made explanatory paragraphs — 20 KB, 3,400 words — for the plain
tier to insert. That was wrong on review and they were deleted. Every one of
those ideas is already covered in the skills the agent reads *before* it fits
anything (chi-squared appears in 9 of them, FWHM in 8, BIC in 6), so the writer
demonstrably has the material; and a canned paragraph with a bolted-on "what it
meant here" reads worse than one paragraph written about the sample. What the
registry keeps is the part that is not reproducible from fluency: which
concepts *this* analysis hit, and the specific caveats a good explanation still
drops — that a BIC comparison is void without equal fitting effort, that dQ is
FWHM not sigma, that a tail fraction and a sigma answer different questions.
Those are `must_cover` requirements on an explanation, not an explanation.

**C1's page budget was missing.** A busy sample detects a dozen explainers, and
inserting all of them made the "two to three pages" plain tier 496 lines, four
times the length of the report they exist to support. Explainers now carry a
reading order, the best-supported six are inserted, and the rest are named in a
comment.

### Numbers

- 1051 tests passing, ruff clean, mypy clean across the new modules.
- 21 new tests for the tiers and superseding, 20 for handoff/audience/toolpath,
  12 for concept selection.
- The reference sample's handoff renders in one command: ~500 lines replacing
  the 28 tool calls it used to take.
