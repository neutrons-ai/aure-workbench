# Two agents for neutron reflectometry: a targeted analyst for the loop, a general one for the collaboration

**Draft 2 — seed for circulation. Not submitted.**
Target venue: *Journal of Applied Crystallography* (methods / computer programs).

**Alternative titles**

1. *Ten calls or a hundred: matching agent architecture to the operating regime in reflectometry analysis*
2. *An instruction is not a mechanism: two designs for LLM-assisted neutron reflectometry, and what each is for*
3. *In the loop and out of it: a targeted workflow agent and a general coding agent, measured against the same benchmark*

---

## Abstract

Specular neutron reflectometry recovers a scattering-length-density (SLD) depth profile from a
one-dimensional reflectivity curve. The inversion is non-unique, so analysis proceeds by fitting a
parameterised layer model — and the expensive, expert part is not the optimisation but deciding
*which model to try, and what its result means*. At a user facility this creates a bottleneck: data
are collected in days and interpreted over months.

We describe two systems built by the same group for this problem, and argue that the choice between
them is a choice of *operating regime* rather than of quality. **AuRE** is a targeted agent: a
prescribed workflow in which an LLM makes a fixed set of decisions at predetermined points, and
which writes the model specification itself. **nr-workbench** is the opposite bet — a harness that
lets a general-purpose LLM coding agent carry out the analysis conversationally from three inputs
(the reduced data, a prose description of the sample, and the hypothesis under test), supplying a
*bounding box* (a typed model specification that generates a standalone refl1d script, an instrument
model for SNS BL-4B, and two hard limits the agent cannot talk past), a *method* (21 domain skills
whose structure includes an explicit catalogue of the rationalisations an analyst makes to
themselves), and a *provenance ledger* in which every fit is an immutable record carrying its
inputs' hashes, its environment, its convergence flag, and — as a mandatory field — the reason it
was run. Neither is an autonomous fitting loop.

Several groups have recently built governed agents for scattering analysis, and we do not claim
priority for that design. We contribute two things it currently lacks. First, a **scoring method
that survives parameter degeneracy**: on a published 51-curve benchmark, goodness-of-fit certifies
78% of an autonomous system's answers where a ridge-invariant criterion — the areal
scattering-length content and the depth it sits at — certifies 22%, and no fitted profile is
physically admissible. Systems in this area are evaluated on exactly the statistic that fails here.
Second, a **required** statement of why each run was worth making: measured on a real beamtime, the
same analyst filled an optional per-fit note 0 times in 23 and a required promotion reason 2 times
in 2.

We measure both against a benchmark of seven expert-analysed reflectivity curves, in five
configurations: the targeted agent on a frontier model and on a self-hosted 120B open-weight model,
and the general agent under two coding harnesses and two frontier models. **The targeted agent
matches the best general-agent arm on pass rate using 10 LLM calls per curve against 78–100, at
$0.07 against $12.30, and with no uninterpretable runs against 9 of 21** — and it holds that result
on open weights running on facility hardware. The general agent's strengths are real and lie
elsewhere: it escalates what it cannot decide with the evidence attached, and it reaches analyses
nobody specified in advance. We therefore argue for deploying both — **the targeted agent in the
experimental loop, where latency, cost and completeness decide, and the general agent for the curves
that do not resolve**, where judgement is required and 11 of 17 findings in a curated week of expert
analysis needed judgement no artefact could supply. We set out what transfers to other scattering
techniques and what deploying such systems at a user facility would require.

**Keywords:** neutron reflectometry; data analysis; provenance; large language models; agents;
reproducibility; user facilities

---

## 1. Introduction

### 1.1 The inverse problem, and where the expertise actually sits

Specular reflectivity `R(Q)` is related to the SLD profile `ρ(z)` along the surface normal, but the
measurement discards phase, so the inversion is not unique: distinct profiles reproduce the same
curve (Majkrzak & Berk [VERIFY]). Practice therefore replaces inversion with *model fitting*. The
analyst asserts a stack of layers, each with thickness, SLD and interfacial roughness, and an
optimiser adjusts the parameters. Mature packages — refl1d/bumps [VERIFY], refnx [VERIFY],
BornAgain [VERIFY], Motofit [VERIFY] — solve the parameter problem well.

They do not solve the *structural* problem: which layers the model should contain. Nor do they solve
the interpretive problem that follows: whether a fitted number means what it appears to mean. Both
are where the expert time goes, and both are where the errors that survive into the literature are
made.

Two properties of reflectometry make this acute.

**Degeneracy is the normal case, not the pathology.** Below the real-space resolution limit
`t_min ≈ 2π/Q_max` — about 30 Å for a typical BL-4B `Q_max ≈ 0.2 Å⁻¹` — reflectivity constrains a
thin layer mainly through the *product* `Δρ·t`, not through `ρ` and `t` separately. Many `(ρ,t)`
pairs along a constant-`Δρ·t` ridge fit essentially equally well, and which one an optimiser reports
is an accident of its starting point. A fitted thickness quoted from such a ridge is a coordinate,
not a measurement, and §6.4.3 shows two frontier models making a structurally similar error together
— quoting a solvent contrast that was an artefact of an under-specified stack.

**Goodness-of-fit ranks candidate models badly.** In a curated corpus of one week of expert analysis
of an operando Cu/THF experiment (25 fits, 17 findings recorded as they were made), *both* of the
fits the analyst promoted as the result were **worse in χ² than the best fit in their own arm**
(1.285 against 1.193; 1.698 against 1.411). An automated loop that optimises χ² gets both of the
decisions that produced the paper wrong. This is not a criticism of χ²; it is a statement about what
model selection in this technique requires.

### 1.2 The facility bottleneck

At the SNS Liquids Reflectometer (REF_L, BL-4B) a user typically has a few days of beamtime and then
months of analysis. The facility is judged on published science per beam-day, and the rate-limiting
step is almost never data collection. It is the interpretation that happens after everyone has gone
home, often by a student who is not the technique's expert, using scripts inherited from a previous
student.

The condition of that inheritance is worth stating plainly, because it motivated this work. Before
nr-workbench, our own practice was a directory per beamtime, each with its own layout, containing
fitting scripts named `…-corefine-tNR-test.py`, `…-t0.py`, `… copy.py`. It had become impossible to
say which script produced a published figure. A hand-written time-resolved co-refinement ran to 341
lines, roughly 200 of them copy-pasted parameter tying, with absolute paths belonging to a different
user baked in.

### 1.3 Why naive LLM automation fails here

The obvious response — wrap an LLM around the fitting engine and let it iterate — is what we built
first, and §3 reports what happened. The short version is that the failure was structural rather
than a matter of model capability:

- an agent optimising a scalar objective optimises the wrong thing (§1.1);
- an agent with no memory of what it already tried repeats itself, and, worse, contradicts itself
  without noticing;
- an agent told in a prompt not to do something will sometimes do it anyway, because **an
  instruction is not a mechanism**;
- and the outputs that most need review are the ones that look most like they were checked.

### 1.4 Contribution

We present two systems built by the same group for the same science — **AuRE**, a targeted agent
that drives a prescribed analysis workflow, and **nr-workbench**, a bounding box and skill library
that lets a general coding agent do the work conversationally — measure them against a common
benchmark, and make four claims.

1. **A scoring method for agent-produced reflectometry models that survives parameter
   degeneracy** (§5.5), and the finding it produces: on a published 51-curve benchmark,
   goodness-of-fit certifies **78%** of an autonomous system's answers where a ridge-invariant
   criterion certifies **22%**. Systems in this area are currently evaluated on χ² or on
   per-parameter agreement, and both are wrong for the thin layers where the science is.
2. **Mandatory-reason provenance**, and the measurement that motivates it: the same analyst filled
   an optional per-fit note 0 times in 23 and a required promotion reason 2 times in 2 (§5.2).
   Execution provenance is now common; a *required* statement of why a run was worth making is not,
   and it is the field that makes the record readable months later.
3. **Two negative results that bound what any such system can claim.** On a curated week of expert
   analysis, 11 of 17 findings needed judgement no artifact could supply, and χ² ranked that corpus
   *backwards* — both promoted fits are worse in χ² than the best in their arm (§6.1).
4. **The head-to-head benchmark, and a result that is regime-dependent rather than a winner.**
   Five configurations analysed the same seven expert-analysed curves: a targeted workflow agent
   (AuRE) on a frontier model and on self-hosted open weights, and a general coding agent
   (nr-workbench) under two harnesses and two frontier models (§6.4). The targeted agent matches
   the best general-agent arm on pass rate using **10 LLM calls per curve against 78–100**, at
   **$0.07 against $12.30**, with **no unscoreable runs against 9 of 21**, and it holds that result
   on a 120B open-weight model running on facility hardware. The general agent's advantages are
   real but lie elsewhere — it escalates what it cannot decide, writes the reports, and reaches
   analyses nobody specified in advance. **Neither design dominates; they suit different operating
   regimes**, and §6.7 states which for which.

We do **not** claim priority for the governed-agent-with-audit-trail design in scattering, which
several groups arrived at concurrently (§2.5.1), nor for the harness-plus-skill-library
architecture, which is prior art.

We are explicit throughout about what is measured and what is asserted. The system is roughly six
months old in its current form; the evaluation in §6 is real but preliminary, and §6.5 proposes the
controlled study we have not yet run.

---

## 2. Background and related work

> **Reviewer note.** The reference list in this draft was assembled without access to a
> bibliographic database and every entry is marked **[VERIFY]**. Authors, venues, years and DOIs
> must be checked before circulation. Claims of priority in §2.5 in particular need a systematic
> search.

### 2.1 Reflectometry analysis software

refl1d and bumps [VERIFY] provide the layer-model forward calculation and the optimisers (including
the DREAM population MCMC sampler) that nr-workbench drives. refnx [VERIFY] offers a comparable
Python modelling and Bayesian inference stack; BornAgain [VERIFY] extends to off-specular and GISAS;
Motofit [VERIFY] and Anaklasis [VERIFY] serve adjacent communities. nr-workbench is not an
alternative to any of these. It generates refl1d scripts and records what happened when they ran.

### 2.2 Machine learning for reflectometry

Neural-network approaches invert or initialise reflectivity curves directly. These learn a map
from curve to parameters for a *fixed structural family*, which is precisely the assumption that
fails when the structural question is the open one. They are complementary to this work: a
well-trained initialiser would be a good tool for the agent to call.

### 2.3 Autonomous experimentation and AI agents in science

Coscientist [VERIFY], ChemCrow [VERIFY] and the A-Lab [VERIFY] demonstrated LLM agents planning and
executing experimental campaigns. Autonomous *steering* of scattering measurements — Gaussian-process
driven acquisition such as gpCAM [VERIFY], and the autonomous microscopy and spectroscopy work of
Kalinin, Ziatdinov and co-workers [VERIFY] — addresses the question of what to measure next. Our
problem is downstream and different: the data already exist, and the open question is what model
explains them and whether the answer is trustworthy.

### 2.4 Provenance and reproducibility


### 2.5 Agentic coding harnesses, memory, and skill libraries

The immediate intellectual debt is to agentic *software* systems. ReAct [VERIFY] established
interleaved reasoning and tool use; Reflexion [VERIFY] added self-critique over episodes; Voyager
[VERIFY] introduced a growing library of reusable skills; SWE-bench and SWE-agent [VERIFY]
established that coding agents succeed in proportion to the quality of their environment feedback.
Production coding harnesses share an unglamorous property that we take as the central design lesson:
**they keep a durable, structured record of what they have already done, and they read it back.**
nr-workbench is, in one sentence, an attempt to give a scattering experiment the same discipline a
codebase gets.

### 2.5.1 Governed agents for scattering analysis — the immediate neighbours

This is now a populated field, and three of its entries are at the authors' own facility. An earlier
draft of this paper claimed priority for the bounded-agent-with-provenance design. That claim was
false and has been withdrawn; what follows is the honest positioning.

**NeuDiff Agent** [VERIFY citation] is the closest. It is a *"governed, tool-using AI workflow"* for
the TOPAZ single-crystal diffractometer at SNS which restricts actions to allow-listed tools,
enforces fail-closed verification gates, and reports *"complete provenance for inspection, auditing
and controlled replay"*. It measures 435 min of manual work down to 86.5 ± 4.7 min — 4.6–5.0×,
with repeated runs, error bars and two LLM backends — and validates its output with checkCIF.

**SasAgent** [VERIFY] is a multi-agent LLM system driving SasView tools (SLD calculator, model data,
RAG documentation, bumps fitting) for small-angle scattering, from the same division as the present
authors. **EQSANS-CLI** [VERIFY] states a thesis close to §4.1 of this paper in its abstract — *"the
CLI is the authoritative executor, and the agent's only job is to translate human intent into
commands on a stable contract"* — driven by a single skill document loaded into an external agent.
**Rongzai Agent** [VERIFY] pairs an LLM with a knowledge base and GSAS-II for Rietveld refinement at
CSNS and reports better R_wp than human specialists on 3 of 5 samples. **AtomisticSkills** and
**ColPackAgent** [VERIFY both] apply the harness-plus-skill-library pattern in atomistic simulation
and colloidal packing.

Two consequences, both of which make this a narrower paper than the one first drafted.

**The harness-plus-skill-library architecture is not novel**, and neither is a governed agent with an
audit trail in scattering. §4.1 and §4.2 should be read as an instantiation of a pattern the field
arrived at concurrently, not as its introduction.

**What is not yet claimed by any of them is the reason.** Each of the systems above records what was
executed. None requires the analyst to state, as a condition of the run happening, *why this attempt
was worth making* — the distinction §5.2 shows is the difference between 0-of-23 and 2-of-2 capture.
Execution provenance answers "what ran"; the ledger described here is built around the claim that
"why it ran" is the field that makes the record useful, and the one nobody fills unless compelled.

### 2.5.2 A note on checkability

There is a second, less comfortable distinction, and we state it as a general principle rather than
as a criticism of any group.

Of the systems above, the ones whose implementations we could obtain vary widely in how much of
their governance is realised in code. In one released implementation, a claim of complete provenance
with controlled replay corresponds to a run-state JSON file written beside the run, and the
repository contains no test suite despite configuring one. We note this not to diminish the work —
its scientific result stands, its external validator is real, and its evaluation protocol is better
than ours — but because the paper you are reading argues that **an instruction is not a mechanism**,
and that argument applies to claims about mechanisms too.

We therefore propose a norm rather than a boast: *a governance claim should be checkable in the
released artifact.* Every quantitative claim in §5 and §6 of this paper is traceable to a record in a
public repository, and §6.3 lists the defects that audit turned up in our own work. We would ask to
be held to the same standard.

### 2.6 Pipeline orchestration, and the class of system it defines

The dominant architecture for LLM-driven scientific automation is what we will call a **fixed-graph
pipeline**: a predetermined set of stages, executed in a predetermined order, with LLM calls
embedded at chosen points as components. Frameworks such as LangGraph, LangChain and CrewAI
[VERIFY] make this shape convenient, but the shape does not depend on the framework — a
hand-written state machine with an explicit node order is the same architecture with fewer
dependencies.

This is the architecture the scattering field has converged on. AuRE (§3) executes six nodes in a
fixed order; NeuDiff Agent executes three (`reduction → refinement → checkcif`); the CLI-plus-skill
systems fix the order in the operator's prompt instead of in code. The framework varies — LangGraph,
a hand-written state machine, a shell contract — and the property does not.

Such a system has three properties that matter for our problem, and they follow from the
architecture rather than from any implementation choice:

1. **It can only perform analyses its graph anticipated.** The LLM chooses *what to put in* each
   stage; it does not choose *which stages exist* or *in what order they run*. Where the
   scientifically correct next action is one the designer did not foresee, there is no path to it.
2. **The LLM's outputs are constrained to the schema of the stage that called it**, which is
   excellent for reliability and fatal for open-ended inference. A structured "refinement
   suggestion" cannot express *"the run labels in the notes disagree with the file headers, stop
   fitting and check."*
3. **State is per-run and per-stage.** Checkpointing a pipeline records *that a stage executed with
   these inputs*. It does not naturally record *why this attempt was worth making*, because the
   pipeline, not the reasoner, decided to make it.

**These are three costs, and each is the same fact as a benefit.** We stated them as limitations
because they were the limitations that made us build the second system, but the benchmark of §6.4
prices the other side and the price is not small. A graph that can only perform anticipated analyses
is a graph that performs them the same way every time, in a known number of calls, for a known cost:
ten LLM calls and $0.07 per curve against 78–100 and $12.30, at the same pass rate. Outputs
constrained to a stage's schema cannot express an open-ended finding, but neither can they omit a
required one — the general agent failed to declare the probe geometry in 9 of 21 runs, across two
harnesses and two frontier models, where the fixed graph declares it because a node always runs
(§9.10). And per-stage state, whatever it lacks as a scientific record, is what makes the run
resumable from any node and reproducible from its checkpoints.

So property (1) is disqualifying for open-ended analysis and close to a requirement for
in-the-loop operation, and which of those the reader is doing decides which architecture is
correct. §6.7 makes that argument on the measurements.

The alternative — a general agent driving a toolbox, with the ordering emergent — trades
reliability for reach, and is only safe if something else supplies the bounds. That trade is the
subject of §4, which describes the bounds, and §5, which argues that the ledger is what makes the
emergent ordering auditable after the fact.

---

## 3. The targeted agent, and the principle we reversed in building the second system

The authors also develop **AuRE** (*Automated Reflectivity Evaluator*), a fixed-graph pipeline for
the same scientific task. nr-workbench grew out of it, still depends on it, and is in several places
a direct response to it. Because AuRE remains under active development, every claim below is pinned
to a specific commit and was verified against it.

> **Citation convention.** AuRE is cited at commit `3021fee` (2026-08-05), which is the revision
> nr-workbench pins in `upstream.toml`. A later revision (`7ae487a`, 2026-08-14) was inspected while
> preparing this paper and none of the properties discussed here had changed, though the
> orchestration framework had. Software claims are pinned by hash for the same reason the
> dependency is: tags and branch heads move. [VERIFY the SHAs at submission.]

### 3.1 What it is

AuRE takes reduced data plus a prose sample description and produces a fitted refl1d model
autonomously. Orchestration is a state machine over a fixed node order — *intake, analysis,
modeling, fitting, evaluation, finalize* — with routing functions choosing edges and a single
refinement cycle `evaluation → modeling → fitting → evaluation`, terminating on an `acceptable`
verdict or at a bounded iteration count. It is 26,054 lines of Python with 655 tests over 158
commits (February–August 2026).

It is worth noting explicitly that AuRE originally used LangGraph and now uses a hand-written state
machine, with the dependency comment recording the change: *"orchestration is a hand-written state
machine, not a framework."* **None of the analysis in this section depends on which it is** — the
node order is still a literal list, and the properties in §2.6 follow from that, not from the
library. We think this is itself a useful data point: the limits we describe are architectural, and
removing the framework did not remove them.

It is a serious system and not a strawman. Several of its design decisions were right and were kept:

- **The user's hypothesis is deliberately not built into the baseline model.** `-h "there may be an
  oxide on top"` becomes a top-ranked *candidate to be tested*, not a layer that is assumed. This is
  the correct epistemics and nr-workbench preserves it.
- **Ranked structural hypotheses carry provenance** — id, rationale, originating skill, origin
  (user or model), status, and the iteration in which they were tried.
- **Deterministic guardrails override the LLM**: boundary hits widen bounds automatically, a χ²
  regression beyond 5% reverts to the best model, a BIC regression reverts.
- **There is a human in the loop.** In interactive mode the runner blocks after every evaluation and
  accepts free-text feedback, a stop signal, a sampler-step override, or a rewind to an earlier
  checkpoint, and the refinement prompt gives that feedback absolute precedence.
- **Skills as `SKILL.md` files** injected into prompts — 8 skills, 1,213 lines of Markdown — an idea
  nr-workbench inherited wholesale and expanded.

nr-workbench still depends on AuRE for 12 functions across 4 modules, principally probe construction
and feature extraction, reached through a single adapter module.

### 3.2 The design, and where the LLM actually sits

Because §6 measures AuRE as a peer rather than a predecessor, its architecture is worth stating
precisely rather than by contrast.

**Seven nodes, one loop, one terminal step.** `NODE_ORDER` is a literal list — *intake, analysis,
modeling, fitting, evaluation, finalize* — and each node has a routing function that chooses the next
edge. `finalize` deliberately has no router, which is what makes it terminal: the loop breaks when a
node has nowhere to route. The refinement cycle is
`evaluation → modeling → fitting → evaluation`, ending on an `acceptable` verdict or a bounded
iteration count. An eighth node, `final_fit`, is intentionally absent from both registries and is
invoked once, explicitly, after `finalize`: it re-fits the selected model with DREAM so the reported
parameters carry a posterior rather than an optimiser's endpoint.

**The LLM is consulted at three nodes out of seven.** Intake, modeling and evaluation hold every
call site — nine in total across the package. `analysis`, `fitting`, `finalize`, `final_fit`,
`refinement`, `routing`, `model_builder` and `hypotheses` contain no LLM call at all. What that
means concretely: the model is *constructed* deterministically from a structured `ModelDefinition`
(JSON, not a generated script) by `model_builder`, fitted through bumps, selected by `finalize`
against explicit tier rules, and polished by DREAM — with a language model contributing only the
sample parse, the ranked structural hypotheses, the refinement proposal, and the verdict on a
completed fit. Six prompt templates cover all of it.

**Every call passes one chokepoint.** `llm/timeout.py::invoke_with_timeout` wraps the provider call
with a timeout and bounded retries, which is where the per-call ledger of §6.4 is hooked; the run's
`llm_calls.jsonl` therefore records node, model, duration, token counts and failures for every call
including the ones that errored. This is the symmetric artefact to nr-workbench's session record,
and it is what makes the two systems comparable at all.

**The record is a checkpoint tree, not a ledger.** Each completed node writes
`checkpoints/NNN_<node>.json` under the run directory, beside `run_info.json`, the `refl1d_output/`
export and a terminal `final_state.json`. A run is resumable from any checkpoint, and thresholds
that terminate the loop are pinned into the state on the first pass — a resumed run keeps the χ²
ceiling it was launched with rather than inheriting the resuming shell's. This is genuine execution
provenance. What it is not is a *cross-run* record: there is no fit registry and no index, so two
runs of one sample are compared by reading two directories (§3.3).

**Deterministic guardrails sit above the model, not inside the prompt.** Boundary hits widen bounds
automatically; a χ² regression beyond 5% reverts to the best model; a BIC regression reverts; an
outer-roughness ceiling and a χ² acceptance floor are read from the environment and pinned. These
are mechanisms in the sense §10 means — they hold whether or not the model agrees with them, and
they are the reason a prescribed workflow can be run unattended a hundred times a day.

Eight `SKILL.md` files (1,213 lines) are injected into the prompts, the idea nr-workbench inherited
and expanded to 21. AuRE also ships `aure mcp-server`, exposing `start_analysis_session`, `run_fit`,
`evaluate_fit`, `modify_model` — the agent-driving-a-toolbox arrangement nr-workbench adopted. At
the pinned commit that path is stale and partly non-functional, so **AuRE contemplated the toolbox
and shipped the pipeline** — a decision about where to spend effort rather than a failure of
imagination, and on the evidence of §6.4 the more useful of the two for the in-the-loop regime.

### 3.3 Where it ran out of road

**The LLM is a component inside a fixed order, not a driver.** AuRE has nine LLM call sites across
three nodes (§3.2), each a hand-written prompt template whose JSON reply is scraped with a regular
expression and validated, with a deterministic fallback behind it. The LLM
never chooses *which* of these runs — the node order does. It does not even build the first model:
that step is deterministic, as AuRE's own architecture diagram states. The consequence is
§2.6(1): AuRE can only do analyses its designer anticipated, and reflectometry analysis of a novel
sample is exactly the case where the next step was not anticipated.

**What the fixed order buys, stated here because §6.4 measures it.** Everything above is written as
a limitation, and in the regime nr-workbench was built for it is one. It is also the reason the same
system analyses a curve in ten LLM calls for seven cents, reaches the same pass rate as the general
agent on the benchmark of §6.4, produces no run whose specification cannot be read back, and holds
that result on a self-hosted open-weight model. A node that always runs is a node that never forgets
to declare the probe geometry. We reversed the principle for the collaborative regime and would
reverse it back for the in-the-loop one; §6.7 sets out which is which.

**Nothing records why a fit was run.** AuRE's `FitResult` has 16 fields. **None of them names a
reason, motivation, or hypothesis.** Persistence is one JSON checkpoint per completed node under a
per-run directory; there is no run store, no fit registry, and no cross-run index anywhere in the
system. (`src/aure/database/` is a materials/SLD lookup table, not a database of runs.) Hypothesis
ids restart at 1 in every run. Two runs of the same sample cannot be compared without a human
reading both directories.

**Its central failure was a sentence in a prompt, and the authors say so.** AuRE's own design
document records an agent that burned its entire iteration budget on parameter tweaks while missing
a ~20 Å native CuO layer, and concludes: *"The root cause was a design mistake, not an LLM
mistake."* The prompt had said to try parameter adjustments before structural changes, and the model
complied.

**The document then draws the opposite conclusion to ours.** AuRE's `approach.md` §6.2 commits to
the principle that *"the fix must not be a heuristic in code… All decision-making about whether to do
a structural change must live in the LLM's prompts and in the skills."* That commitment is the
fulcrum of this paper. nr-workbench's development notes record the counter-principle, learned the
hard way and written down as a ground truth:

> **An instruction is not a mechanism, and one mechanism is not two.**

Telling a model not to do something is a *request*; a pre-execution hook that exits non-zero is a
*limit*. Only the second survives a model that decides the rule does not apply to this case.

**A secondary lesson: a second, weaker model is not a check.** nr-workbench can call a configured LLM
endpoint in three places. When a coding harness is driving, all three hand the work back to the
harness instead, because the harness is the better model on this task, and a plausible second opinion
re-enters the harness's own context as *evidence*. That is worse than no verdict at all.

### 3.4 Evidence discipline, in both directions

AuRE carries a validation harness — batch runner, comparator, inventory and report, six tracked
source files — and it is maintained: the most recent change to it *"score[s] the fit the run
reported, and flag[s] a vetoed one"* (2026-07-28). What it does **not** carry is committed results.
The scoring machinery exists; the scores are not in the repository, so an outside reader cannot
learn from the archive how well the system performs.

We record this about our own work for two reasons. It is the specific failure this paper is
written to avoid — and, less comfortably, it is a failure nr-workbench has only partly escaped. The
measurements in §6.1 and §5.2 are committed, in a ground-truths file; the timing figures in §6.2
were reconstructed for this paper rather than recorded as they happened. A system that tracks every
fit still did not track its own evaluation, which is worth stating plainly in a paper whose thesis
is that tracking is the mechanism.

---

## 4. Design

nr-workbench does not contain a decision policy. It supplies four things around a general coding
agent: a **bounding box**, a **method**, a **ledger**, and a **handoff**.

```
  data ──────────────┐
  sample.md  ────────┤   ┌──────────────────────────────────────────┐
  (description +     ├──▶│  offline, model-free checks              │
   hypothesis)       │   │  headers · reconcile · overlap · tNR     │
  spec (nrw-model/1) ┘   └──────────────────┬───────────────────────┘
                                            ▼
                          ┌────────────────────────────────────────┐
                          │  general coding agent (bounded)        │
                          │  reads: skills, handoff digest, ledger │
                          │  writes: specs, notes, reports         │
                          └───────┬──────────────────────┬─────────┘
                       generate   │                      │  refused:
                       ─────────▶ │  refl1d script       │  promote
                                  ▼                      │  --upload
                          ┌───────────────┐              │  --force
                          │ nrw fit run   │              └────────▶ ESCALATIONS.md
                          └───────┬───────┘                          (human)
                                  ▼
                  immutable result dir + append-only index
                  (script · input hashes · env · χ² · converged · REASON)
                                  │
                                  ▼
                    tiered reports ──▶ human analyst ──▶ redirection
```

*Figure 1 (to be drawn properly).* Data and prose in; a bounded agent drives generation and fitting;
every run lands in an append-only ledger; three refused actions escalate to a human.

### 4.1 The bounding box

**A typed model specification (`nrw-model/1`).** A pydantic-validated YAML document declares
materials, layers, probe, states, time-resolved series, parameters with sharing scope, constraints,
data trimming, and fit settings. It expresses steady-state co-refinement and time-resolved series in
one schema. Its effect on the artefact is large: a **62-line spec replaces 343 lines of hand-written
Python**, and a model gate test drives the generated script and the hand-written reference from the
same parameter vector and agrees on χ² to 1.7 × 10⁻¹⁶ across 21 experiments and 40 free parameters.

**Code generation, not an API call.** The spec generates a readable, standalone refl1d script, which
is then executed. This matters for three reasons: the script is a frozen, human-auditable artefact
that a collaborator can run with only refl1d and bumps; the agent can read and reason about it in
its native medium; and the fit's identity is defined partly by the hash of the code that ran.

**An instrument model.** Facility knowledge is hard-coded rather than inferred: BL-4B's angular-only
resolution convention (`dL = 0`), the reduced-file header layout including the direct-beam run used
for normalisation, the detector geometry (which moved twice), and the fact that incident angle
appears in the header in radians. A spec requesting the unsupported moderator resolution convention
is **rejected with an explanation** rather than silently accepted, because the alternative is a fit
that runs cleanly and produces numbers comparable with nothing.

**Two hard limits.** Three actions are refused to an agent: `nrw promote`, `nrw isaac export
--upload`, and any `--force` flag. The third is the interesting one — every forcing flag in the
system exists because a check said no, so an agent reaching for one has arrived at exactly the
situation a person is meant to see. Enforcement is deliberately doubled: a `PreToolUse` hook that
exits 2 before the command runs, *and* an `NRW_AGENT=1` environment variable under which the CLI
refuses from the inside. A hook can be misconfigured and a variable can be unset; both failing
silently at once is a different order of accident. The guard splits a command line on `&&`, `;`,
`|` and `&` before judging it, because `nrw ls && nrw promote abc` is how batched shell work
actually looks. Neither mechanism is a sandbox. They stop the plausible mistake.

Every refusal names `ESCALATIONS.md`. An agent told only "no" retries; one told where to write the
decision stops.

**A refusal to start.** An unattended session reads `## Fits to perform` in the sample's notes and
does that and nothing else. **With that section empty, the session refuses to start.** An agent that
chooses for itself what is scientifically interesting is the failure everything else is arranged
against, and the only reliable place to stop it is before it begins.

### 4.2 The method: skills, and the anti-rationalisation structure

Twenty-one domain skills ship (4,5xx lines of Markdown; 12 seeded into a new project, the rest
available on demand), against AuRE's 8. Each follows a fixed anatomy enforced by a test:
**Overview / When to Use / Process / Rationalizations / Red Flags / Verification**.

The novel sections are the fourth and fifth, and we believe they are the most transferable idea in
the system. **Rationalizations** enumerates the excuses an analyst — human or model — makes to
themselves, each paired with its refutation. From `thin-layer-degeneracy`:

> *"BIC says the layer isn't needed."* BIC compares best achievable fits. If the complex model was
> not optimised well, BIC is comparing a good fit against a bad one and the conclusion is about the
> optimizer.
>
> *"I fixed the SLD, so the thickness is now well determined."* Its interval got narrow, which is not
> the same thing. It is a conditional interval — conditional on a value you chose and the output does
> not mention — and the ridge is still there.
>
> *"The oxide came out 11 Å, so there is an 11 Å oxide."* Read the profile before you believe that.
> If its interfaces are wider than it is, the nominal SLD is attained nowhere and the 11 Å is a
> coordinate on a shape, not a layer thickness.

**Red Flags** lists observable states that should stop the analysis — "an adhesion layer or oxide at
exactly its lower thickness bound"; "a thin layer's thickness compared between states when its SLD
was fixed, without the comparison being made on `Γ = t·Δρ`". These are written in the imperative and
in terms of things visible in the output, so they are checkable.

Critically, a dozen of those stated rules were then **promoted from prose into code**. A
contradiction checker compares machine-readable spec declarations against machine-readable
assessment outputs — 25 distinct automated finding kinds across fit assessment, spec contradiction,
and notes/header reconciliation. Two design rules from that work are worth quoting:

- **Bound it, do not check it.** Roughness coherence is tested against the *declared ranges*, not
  the fitted values: "a range permitting σ > t/4 guarantees the fit can go there, and it will."
- **Silence on absence.** A missing assessment is not a contradiction. A checker that fires on
  incompleteness fires on every project mid-beamtime, and then nobody reads it.

### 4.3 Model-free checks that run before any model exists

The highest-value component is also the least glamorous: deterministic, offline, LLM-free checks
whose output is placed in front of the agent before it starts.

A reconciler compares what the reduced files say about themselves (the `# Meta:` block: operator's
run title, direct beam, scaling, angle) against what the scientist's notes claim those runs were.
From a real beamtime's own record: `sample.md` listed run 218393 as `OCV` and did not mention 218397
at all; the run titles say `CA-realigned` and `OCV-after`. The analyst's own note records that this
*"sent five fits down the wrong path."* Separately, run 218389's segment was normalised against
direct beam 218277 where the others used 218274, which is why its scale sat at ~0.85 — recorded in
the findings as *"a different direct beam, not physics."*

Both discrepancies were available before the first fit ran. **Neither needed a model, a fit, or a
language model — only for something to look.** The first is a string comparison; the second is a join
on one field.

A separate assessment (`tNR`) characterises time-resolved series *before* any model is fitted —
variogram, PCA/KL decomposition, amplitude and interval structure — answering "did anything change,
where in Q, and monotonically?" from the data alone, and emitting machine-readable fields
(`amplitude.trajectory`, `template.implied_change`) that the contradiction checker later tests
proposed specs against.

### 4.4 The handoff

The system's output is not a fit. It is a set of documents pitched at named readers, plus a
machine-composed digest for whoever picks the work up next.

**Three tiers, always scaffolded.** A *technical* tier for someone who will argue with the result
("show the arithmetic rather than its conclusion, and give every abandoned branch the sentence that
says why it was abandoned"); a *paper* tier — methods paragraph and parameter table; a *summary*
tier for the collaborator who owns the chemistry and does not fit reflectivity. All three always
exist, because the *team* is mixed even when the person asking is not. A checker exists to stop them
drifting into three different answers.

**A declared audience.** Four independent axes recorded in project configuration — reflectometry,
statistics, and domain fluency each `newcomer|practitioner|expert`, plus a `role` of
`drives|collaborates|delegates`. Independent because a reflectometry expert who wants the Bayesian
model comparison spelled out in full is a real and common reader, and a single novice/expert dial
gets that reader wrong twice. These change what is explained, and refuse nothing.

**A composed takeover digest.** `nrw handoff <sample>` prints, in order: how to invoke the tool here,
the declared task, the escalations *in full*, how the last unattended session ended, the integrity
check, fits with nothing written down, the offline checks recomputed now, the audience, and a
reading order. It exists because a measured takeover session spent **28 tool calls before its first
substantive action**, of which 10 were spent looking for the `nrw` binary — and 136 of that session's
268 tool calls (51%) carried a `PATH` workaround. All of it was derivable; the digest was already
being computed for unattended sessions and was reachable by no command.

---

## 5. The provenance mechanism

This section is the core claim.

### 5.1 What is recorded

One fit writes one immutable directory. Nothing in it is ever rewritten except `NOTES.md`, which is
explicitly the human's.

```
samples/<sample>/results/<fit_id>/
  manifest.json   provenance envelope (21-field record)
  model.py        frozen copy of the script that ran
  inputs.json     every input file with its sha256
  spec.yaml       the specification it was generated from
  env/            versions.json, requirements.txt, project.patch (if the tree was dirty)
  fit/            bumps export: *.par, *-err.json, *.mc.gz chain, *-refl.dat, *-profile.dat
  figures/        *.svg
  NOTES.md        the only mutable file here
```

The record carries 21 fields. Beyond the obvious (χ², free parameters, points, timestamps, command
line, artefacts) four deserve comment:

- **`converged` is three-valued.** `False` means the sampler warned; `True` means a sampler ran and
  was quiet; **`None` means the fitter does not test convergence at all**. An optimiser has no
  opinion, and recording that as `True` asserts something nobody checked. An earlier version
  hard-coded `converged: True`, which meant the assessment was handed a false value on the exact axis
  that disqualified a fit — on the reference corpus, the one non-converged fit had a *better* χ²
  (1.264) than the published answer (1.285), so the field decides which of the two a reader trusts.
- **`stack`** — the layer structure as `THF|Cu|Ti|Si` — is stored rather than derived, because the
  index outlives the result directory, and the structure is the one thing that makes a listing row
  mean anything after the artefacts are gone.
- **Four separate digests** define run identity: script, inputs, settings, environment. Their
  combination is a `run_key`; two fits sharing one differ in nothing that should change the answer,
  so the second is a replicate rather than new work, and is refused by default. A separate
  `data_digest` covers the measurements *without* the script, so the system can distinguish "the
  data changed underneath this result" (→ `STALE`) from "somebody edited the model."
- **`models`** maps bumps' positional export index to model name, because bumps numbers its exports
  by position and ignores the experiment name.

An append-only JSONL index holds a 19-key summary line per fit. Because it is append-only, deleted
results are *marked*, not removed.

### 5.2 The reason, and why it must be mandatory

The system separates two things that were once one field. A fit's `--note` answers **why this run**,
and lands in a `## Why this run` section that the blank-note detector can see. A separate,
always-present blockquote records *what ran*. Generated prose — from the automated assessment — is
fenced in `nrw:generated` markers and does **not** count as a human having thought about the fit,
because a note holding only machine prose looks written to anyone skimming it.

The reason this distinction is enforced rather than encouraged is a measurement. On the first real
beamtime run through the tool (23 fits, 25 result directories, one analyst):

| surface | required? | used |
|---|---|---|
| `nrw fit run --note` | optional | **0 of 23** |
| `results/<fit_id>/NOTES.md` | optional | **0 of 25** |
| `nrw promote --reason` | **required** | **2 of 2**, both substantial |

The same analyst wrote 609 lines of findings in a project-level notes file over the same period, and
the two promote-reasons are per-fit notes in all but name — one cites another fit by id, quotes
intervals, and flags two caveats. **Nobody was unwilling to write. The optional surfaces asked
nothing and nothing read them back.**

This is, we think, the most practically important finding in the paper, and it generalises past
reflectometry: *provenance fields that are optional are not filled, by humans or by agents. The
reason a run happened must be a required argument of the thing that runs it.*

### 5.3 Context economics: why the ledger is a capability, not bookkeeping

An LLM agent's binding constraint is its context window. Measured on an operando Cu/D₂O project
(`jen-jun2026`, 52 recorded fits), a full result directory has a median size of 7.67 MB across 46
files — dominated by the DREAM posterior chain. Three progressively compressed views exist:

| view | size per fit | compression |
|---|---|---|
| full result directory (median) | 7,671,819 B / 46 files | 1× |
| index line (19 keys) | ~736 B | ~10,000× |
| `nrw ls` row | ~246 B | ~31,000× |
| agent-prompt fit-chain entry | ~107 B (~53 tokens, `cl100k_base`) | **~72,000×** |

At ~53 tokens per fit, the 40 most recent fits — the cap `FITS_LISTED` imposes on the composed
prompt — cost the agent about 2,100 tokens; the full 52 would cost about 2,750. Either is trivially
affordable at the start of every session. Reading the same history from the artefacts is not
possible at any budget.

Two honest qualifications. The agent does **not** see the whole history: the block is truncated at
40 entries and says so (*"Recorded fits (52 total, oldest first)"*), so on a long-running project
the oldest work is out of view unless explicitly queried. And the compression ratio is flattered by
the posterior chain, which no agent would ever read; against the record's metadata alone
(`manifest.json` + `inputs.json` + `env/`) the ratio is nearer 100×. The operative claim is not the
ratio but the absolute cost: **knowing what you already did is a ~2,000-token line item**, and that
is what makes it affordable to always do it.

This is the mechanism. An agent that can afford to know what it already did behaves differently from
one that cannot: it stops re-running variants, it notices that a change made things worse, and it
can answer "have we tested this?" without a human remembering. The compression is what makes the
knowledge affordable, and the *mandatory reason* is what makes the compressed line worth reading.

We are careful about the strength of this claim. The compression ratios are measured. The claim that
they *change agent behaviour* is supported by the design history (§5.4), not by a controlled
ablation. §6.5 proposes that ablation.

### 5.4 A behavioural failure the ledger made visible

In one session, twelve consecutive fits failed to improve. Five of them **differed from their
predecessor in nothing but the optimiser**. The session was reaching for the fitter menu instead of
reverting the model edit that had broken things — and the evidence had been on screen the whole
time: `chisq 1.306 → 16.58`, which reads as a neutral fact rather than an alarm.

Two changes followed, and both are mechanisms rather than instructions. The fitter choice was
reduced from everything bumps offers to exactly **two, with their roles named**: `amoeba` explores,
`dream` quotes. `de`, `lm` and `newton` work perfectly well; they were removed because *a menu is an
escape hatch*, and with two fitters the next thing to change is obviously the model. Off-menu
fitters remain reachable by running bumps on the generated script directly — a deliberate speed bump
that leaves the result outside the provenance record. Separately, a χ² regression beyond a factor
1.5 now prints `(12.7x WORSE)` and names the model edit as the suspect.

This episode is the paper in miniature: the ledger did not prevent the failure, but it is what made
the failure *legible*, and legibility is what allowed a mechanism to be built against it.

---

## 5.5 Scoring a fitted model when its parameters are degenerate

Every system in §2.5.1 is evaluated against reference fits, and the evaluations share a weakness
that §1.1 predicts. This section proposes a replacement and reports what it finds.

### 5.5.1 Why per-parameter agreement is the wrong criterion

The natural way to score a recovered model is to compare each layer's thickness, SLD and roughness
against a reference within tolerances. For a layer thinner than `t_min ≈ 2π/Q_max` this is wrong in
both directions. Reflectivity constrains such a layer through the *product* `Δρ·t`; a fit that lands
elsewhere on that ridge describes the same physical amount of material and is scored as **two
failures**, while a fit whose product is wrong can pass both bounds by sitting near the reference in
each coordinate separately. The failure is not hypothetical: on the corpus below the thin native
oxide shows 188% mean roughness error and 31% thickness error where thick copper shows 12%.

Goodness-of-fit is no better as a gate, for the reason §6.1 gives — on a curated corpus it ranks the
expert's own promoted answers backwards.

### 5.5.2 Two invariants

We score instead on quantities read off the reconstructed SLD profile, so that neither requires the
fitted and reference models to have the same number of layers — which matters, because merging a
thin overlayer into its neighbour is legitimate and defeats layer matching on most cases.

**How much material**, the areal scattering-length content relative to the ambient:

```
Γ = ∫ [ ρ(z) − ρ_ambient ] dz            (10⁻⁶ Å⁻¹)
```

`Γ` is invariant along the contrast–thickness ridge: trading `ρ` against `t` leaves it unchanged.

**Where it sits**, the depth of the distribution:

```
z̄ = ∫ z |ρ(z) − ρ_ambient| dz  /  ∫ |ρ(z) − ρ_ambient| dz            (Å)
```

`Γ` alone is insufficient — moving a layer from the top of a stack to the bottom preserves the
integral exactly — so the pair is the metric. The weight is `|ρ − ρ_ambient|` rather than the signed
deficit, because a stack containing layers both denser and lighter than the ambient has a signed
integral passing through zero, and a centroid divided by it diverges precisely where the structure
is most interesting. `z̄`'s tolerance is principled: reflectivity cannot place material more finely
than `2π/Q_max`, so nothing tighter is a real disagreement.

A third check, whether the fitted profile excurses outside the media bounding a layer, is **reported
and not gated** — the reference structures carry the same erf-tail artifact on 5 of 9 cases, so
gating on it measures the slab-plus-roughness parameterisation rather than the fit. Where the
reference is equally affected the check abstains rather than passing.

### 5.5.3 What it finds

Applied to a published autonomous system's output on this corpus (9 scored cases; see §6.6 for the
corpus and the caveats):

| criterion | passes |
|---|---|
| χ² ≤ reference + margin | **7/9 (78%)** |
| Γ (areal content) within tolerance | 4/9 (44%) |
| depth `z̄` within resolution limit | 3/9 (33%) |
| **both invariants** | **2/9 (22%)** |
| layer count correct | 3/9 (33%) |
| profile physically admissible | **0/9 (0%)** |
| median Γ error | 21% |
| median depth error | **35.6 Å** |

Goodness-of-fit certifies three and a half times as many answers as the physics does. The median
depth error exceeds the resolution limit, so more than half the fits place the material further from
its true position than the technique can resolve — while reporting χ² near unity.

We offer this as the evaluation instrument the field currently lacks, and note that it is
technique-agnostic in form: any scattering method with a degenerate forward model has an analogous
invariant (§7.1).

---

## 6. Evaluation

### 6.1 How much of expert analysis is automatable? A measurement

A curated corpus — one week of expert analysis of the Cu/THF experiment, 25 fits, 17 findings written
down as they were made — was replayed against every automatic check in the package:

| | Count | Meaning |
|---|---|---|
| Reachable by arithmetic | 1 | A program can find it and say what it means |
| Signal detectable, conclusion not | 5 | A program can point at it; a person says what it is |
| Needs judgement | 11 | No artifact on disk distinguishes the right answer |

The worked example of the third row: in one fit, three parameters sat on their bounds and each
implied a *different* correct action. `Ti.rho` at −2.0 is bulk titanium, and widening the bound
would have destroyed the result — it *was* the result. `CuOx.rho` on its floor meant the material
assignment was wrong. `Cu.roughness` carried no information at all. **Nothing in the artefacts tells
them apart.**

This measurement is why nr-workbench contains no decision policy. It is also the number we would most
like other groups to reproduce for their own techniques, because it sets the ceiling on what any
automation of this kind can claim.

### 6.2 Time to interpretation, measured from the ledger

Because every fit id is a UTC timestamp, the ledger reports on the system's own throughput. Sessions
below are wall-clock spans from first to last fit on a day.

| Dataset | Fits | Sessions | Wall-clock span | Output |
|---|---|---|---|---|
| Cu/THF (`cu-thf-expt11`), re-analysis of April 2025 data | 23 | 2 (6–7 Aug 2026) | 5 h 09 m + 3 h 09 m = **8 h 18 m** | SI-grade analysis document |
| Cu/D₂O (`jen-jun2026`), **new** data | 50 | 1 (12 Aug 2026) | **3 h 16 m** | three reports, publishable finding |
| Cross-experiment synthesis | (2 correction re-runs) | 14–15 Aug 2026 | — | joint report |

Spans are first fit *start* to last fit *start* on a day, consistently. (An earlier draft of this
table mixed start-to-start and start-to-finish conventions and understated the first row by nearly
three hours; the convention matters more than it looks.)

The comparison that motivates the speedup claim is the first row: the **same experiment**, whose
original expert analysis took **one week**. Against a nominal 40-hour week, 8 h 18 m is a factor of
**~4.8** — a useful speedup, and materially less than an order of magnitude.

**How to read these numbers, and how not to.** They are wall-clock spans between recorded fits, not
human effort — some fits ran unattended, and time spent thinking between sessions is invisible. "One
week" is the analyst's own characterisation of the original campaign, not a stopwatch measurement.
The two analyses are not independent: the second had the benefit of the first's conclusions. And the
analyst is the same person and the tool's author. **We therefore claim these as an existence proof
and an upper bound on elapsed time, not as a controlled speedup measurement.** §6.5 says what the
controlled version would look like.

What we do claim without hedging: the numbers were *computable at all*, by a script, from the
provenance record, months after the fact. That is not true of any prior workflow in this group.

### 6.3 Correction rate and audit outcomes

From the benchmark sweeps: a wrong answer reached independently by two frontier models under the
same harness, traced to a specific mechanism (a missing pair of layers absorbed into the solvent
contrast; §6.4.3). It was caught against the expert reference rather than by the system itself, but
the escalation the system wrote states its own evidence, so the error is legible enough to refute.
One further discrepancy self-reported (χ² values in a background comparison table quoted as
1.330/1.528 where the records hold 1.355/1.556) and one bookkeeping defect self-reported (a generator
bug meant `fixed: true` was unusable, so pinned parameters were declared as `value ± tiny` and still
count as free; the effective parameter count is 37 against 40 recorded, and that BIC was computed by
hand).

We report the self-reported defects deliberately. A system whose audit section is empty has not been
audited.

### 6.4 System scale

| | nr-workbench | AuRE (`7ae487a`) |
|---|---|---|
| Source | 35,695 LOC | 25,769 LOC |
| Tests | 1,109 | 635 |
| Test source | 17,843 LOC | [MEASURE] |
| Domain skills | 21 (4.5k lines MD) | 9 |
| CLI | 25 top-level / 51 leaf commands | 16 top-level |
| Development | 69 commits, 5–13 Aug 2026 (9 days) | 156 commits, Feb–Aug 2026 (6 months) |

The last row is offered with a caveat and a claim. **The caveat is substantial and comes first.**
nr-workbench inherited AuRE's domain understanding — the physics of the skills, the probe
construction, the feature extraction it still imports — so this is emphatically not a like-for-like
comparison of construction effort. A large part of what took AuRE six months was *finding out what
the problem was*, and that knowledge was free to its successor. The two systems also overlap in
time: AuRE's most recent commit postdates nr-workbench's, so this is not a succession but two
concurrent lines of work.

The claim, stated narrowly: the second system was built roughly an order of magnitude faster than
the first, by the same author, *under the same discipline it now supplies to its users* — a
repo-root skill library, reviewer subagents, and a ground-truths file that has accumulated over 100
recorded findings. The development methodology and the product methodology are the same
methodology. We offer this as suggestive, not as evidence, and we would not object to a referee
asking for it to be cut.

#### 6.4.1 Cost per analysis — LLM calls and tokens

**[⚠ PROVISIONAL — 7 matched curves of 51. Figures update when the full sweeps close.]**

Cost is where the two designs differ most sharply, and it is recoverable per case from records each
system keeps. The figures below are **matched**: the same seven measured curves analysed by both
systems (`201136, 201144, 201152, 201179, 201290, 201306, 206915`). **The primary comparison is
calls and tokens, not currency** (billing caveat below), and both sides are normalised to the same
token accounting (convention warning below).

| mean per analysed curve | AuRE | nr-workbench | ratio |
|---|---|---|---|
| LLM calls | 10 | 100 | **10×** |
| fresh input tokens | 16,586 | 13,438 | 0.8× |
| cached input tokens | 19,474 | 13,307,896 | **683×** |
| **output tokens** | **1,453** | **110,918** | **76×** |
| total input tokens | 36,061 | 13,321,335 | 369× |
| *notional* cost (see caveat) | *$0.07* | *$12.30* | **181×** |

**Fresh input is the row that does *not* separate them: 16.6k against 13.4k, a ratio below one.**
Both systems put essentially the same quantity of genuinely new information in front of a model to
analyse one curve — unsurprisingly, since it is the same curve and the same sample description. The
physics content of the task is one size, and both architectures find it.

Everything that diverges is **re-reading and generation**. AuRE sends a large prompt a small number
of times: ~10 calls, ~1.7k fresh tokens each. nr-workbench sends a small increment very many times
on top of an enormous cached context: ~100 calls against 13.3M cached tokens. The cleanest single
statistic is **output, at 76×** — it is the one figure confounded by neither caching strategy nor
prompt architecture, and it measures how much each system *thinks out loud* to reach an answer.
That is §9.10's design contrast priced: an agent that authors and revises its own specs converses
its way to a result; a prescribed workflow spends a fixed, small number of calls at predetermined
decision points. Neither is a defect. They price two different products, and a reader choosing
between the designs needs both.

**Cost per *scoreable* analysis.** Two of the seven nr-workbench runs (`201136`, `201290`) at first
produced no scoreable fit at all — the `probe.back_reflection` omission of §9.10 — which put the
cost per *usable* result 40% above the raw figure. Both are now scored, because the omission was
answered on the measuring side rather than in the system under test (§9.10), so the raw and usable
figures coincide at **$12.70 per curve**. The episode is still worth recording: a premium of that
size, arriving only at extraction after a full session, is what an unscoreable rate costs a
facility, whether or not this particular instance was recoverable.

**Billing caveat — currency is not a like-for-like axis.** The nr-workbench figures come from Claude
Code's `total_cost_usd`, a *notional API-equivalent* price computed from token counts; the operator
was on a subscription and did not pay per token. AuRE's will come from metered Azure OpenAI
consumption — an actual invoice. Different accounting bases; report tokens and calls as the
measurement and quote currency only alongside the basis it was computed on.

**⚠ Convention warning — the two token counts are not comparable as reported.** LangChain/OpenAI
(AuRE) reports `input_tokens` as the **total**, with cache reads a *subset*. Anthropic
(nr-workbench) reports `input_tokens` **excluding** cache, listing `cache_read_input_tokens`
separately. Comparing raw fields puts ≈37k against ≈13k and reads as AuRE consuming 3× more input.
The table uses *fresh* input: `input_tokens − cached_tokens` on the AuRE side, as-is on the other.

**Instrumentation.** nr-workbench writes a session record per sample at
`.nrw/agent/<timestamp>-<sample>.jsonl`; its terminal `type: "result"` line carries `num_turns`,
`total_cost_usd`, `duration_ms` and an aggregate `usage` block. *Use that line* — summing `usage`
over individual `assistant` messages double-counts cached reads by roughly 2× and will not
reconcile. AuRE recorded only call counts (`state.llm_calls`), and now writes the symmetric
artefact: a per-call ledger at `<output_dir>/llm_calls.jsonl`, hooked at the single chokepoint every
node passes through and labelled with the node that made the call, so the budget can be attributed
rather than only totalled. Failed calls are recorded too — a timed-out or refused call is still
billed.

**Remaining caveat.** Both arms ran their own model (`gpt-5.4` via Azure; Claude via Claude Code),
so an architecture difference and a model difference are still confounded. The design that separates
them holds the model constant across three configurations — AuRE and an agentic harness against the
*same* Azure deployment, plus a second agentic harness (opencode) against that same deployment —
which is what the pre-registered proxy arms exist for. Only then does a difference in calls or
tokens attribute to the architecture rather than to the model behind it.

#### 6.4.2 Holding the model constant: the caching strategy does not port

**[⚠ PROVISIONAL — 7 matched curves, one sweep per configuration.]**

The caveat closing 7.4.1 has now been tested. Three configurations ran against the **same Azure
`gpt-5.4` deployment**, so architecture is no longer confounded with model: AuRE calling it
directly; Claude Code driving it through a LiteLLM Anthropic-to-Azure bridge (`azure-nrw`); and
opencode driving it (`nrw-opencode`). Two further arms bound the comparison on either side —
`nrw-local`, Claude Code on Anthropic's own API, the only arm whose backend matches the harness's
native provider; and AuRE on **`gpt-oss-120b`**, a 120-billion-parameter open-weight model
self-hosted at the facility, which changes the model while holding the architecture fixed.

Currency uses Azure list rates for the East US Global Standard deployment (input $2.50, cached
input $0.25, output $15.00 per M) and Anthropic list for `claude-opus-5` ($5/$25 per M, 1-hour cache
writes at 2×, reads at 0.1×). The self-hosted arm has no per-token charge.

| per analysed curve (mean of 7) | AuRE gpt-5.4 | **AuRE gpt-oss-120b** | nrw-opencode | azure-nrw | *nrw-local* |
|---|---|---|---|---|---|
| LLM calls | 10 | **10** | 51 | 78 | *100* |
| **full-rate input** | 16,586 | **5,944** | 338,296 | **6,407,519** | *13,438* |
| cache **write** | 0 | 0 | 0 | **0** | *294,869* |
| cache read | 19,474 | 30,976 | 3,239,022 | 1,175,168 | *13,013,027* |
| total prompt | 36,061 | **36,920** | 3,577,317 | 7,582,687 | *13,321,335* |
| output | 1,453 | 6,009 | 23,071 | 83,058 | *110,918* |
| cache hit rate | 54% | 84% | 91% | **15%** | *98%* |
| **cost per curve** | **$0.07** | **~$0** | $2.00 | **$17.56** | *$12.30* |

**The same harness, driving the same model, pays list price on 477× more input tokens once the
backend changes.** Claude Code's full-rate input is 13.4k per curve on Anthropic and 6.41M through
the bridge. Nothing else about the harness, the prompt or the task differs.

The `cache write` row is the mechanism. Claude Code's caching is *explicit*: it marks the
conversation prefix with `cache_control` breakpoints, which Anthropic bills once as
`cache_creation` — at 2× list for the 1-hour TTL Claude Code selects — and then serves back at 0.1×
on every subsequent turn. That is how a 97% hit rate is reached on a conversation that is re-sent in
full at each turn. Azure exposes no explicit cache-write primitive, only automatic prefix matching,
so the breakpoints have nowhere to land: `cache_creation` is **exactly zero in every azure-nrw
session**, and the run falls back on automatic caching, which recovers 17%.

**This is not a defect of the bridge, and we checked rather than assumed.** Azure's automatic
caching is working on the deployment: a 5,212-token request sent to it twice returned
`cached_tokens` = 0 then 4,992, and five identical requests through the bridge hit the cache five
times out of five. LiteLLM's translation is also correct,
including the convention change — it returns `input_tokens: 220` alongside
`cache_read_input_tokens: 4992`, converting OpenAI's inclusive accounting to Anthropic's exclusive
form. Both halves work. What does not survive the port is the *strategy*: automatic prefix caching
cannot substitute for explicit breakpoints once the conversation tail changes every turn.

The consequence generalises past this pair. **An agentic harness's cost model can depend on a
provider-specific caching primitive, and that dependency is invisible until the harness is moved.**
A cost measured on the harness's native provider is not transferable to another backend, even with
the model held constant — which is a caution for any facility planning to run such a harness against
whatever inference it already procures. It also reframes 7.4.1: the call-count and output-token
gaps there are architectural and port cleanly, whereas the cached-input gap is partly an artefact of
which provider the harness was designed against.

Two figures for scale. `nrw-local` costs **$12.30 per analysed curve**, and **77% of that is cache
traffic** — $45.55 of reads and $20.64 of writes against $0.47 of fresh input across seven curves.
Quoting fresh input alone would understate the bill by a factor of 26. And `azure-nrw`, running a
*cheaper* model per token, costs **more** ($17.56) than `nrw-local` on Anthropic — entirely because
91% of its bill is full-rate input that caching would otherwise have absorbed. Strip the caching
advantage and the agentic harness's cost structure inverts.

**Caveats.** One sweep per configuration over seven curves, so these are single measurements, not
means over repeats. opencode publishes no session-terminal `result` record, so its figures are
summed over per-step `step_finish` events rather than read from one aggregate line, and are not
strictly comparable in construction to the Claude Code arms. Cache hit rate is defined here as
`cache_read / (full-rate + cache_write + cache_read)`, which flatters a provider that cannot report
cache writes at all. Currency is list price: any committed-use discount would move all four paid
arms together, and the self-hosted arm's "~$0" is a marginal cost that excludes the capital and
power behind the facility's own GPUs.

#### 6.4.3 One run, four systems: what the harness fixes and what it does not

**[⚠ PROVISIONAL — one worked case, offered as an existence proof, not a rate.]**

Run 206915 is the most informative case in the corpus, because the four
configurations disagree in a way that separates the three things this evaluation
keeps trying to hold apart: the harness, the model, and the scoring.

| | AuRE | nrw-local | azure-nrw | nrw-opencode | expert |
|---|---|---|---|---|---|
| harness | AuRE | Claude Code | Claude Code | opencode | — |
| model | gpt-5.4 | opus-5 | gpt-5.4 | gpt-5.4 | — |
| structure | oxide-only | oxide-only | oxide-only | **SEI + plated** | SEI + plated |
| ambient | — | protonated | protonated | **deuterated** | **6.2, deuterated** |
| χ²_red | 3.83 | 3.34 | 3.33 | **1.09** | 1.05 |

**Same harness, different frontier models, near-identical sessions.** `nrw-local`
and `azure-nrw` differ only in the model behind Claude Code, and they ran what is
almost the same experiment: five fit branches in the same order, with the same
free-parameter counts (16, 16, 19, 16, 15) and the same fitter at each step
(amoeba ×3, then DREAM ×2), converging on χ² of 3.336 and 3.334 — a 0.06%
difference. Both invented the same five model names — `cu-ox-native`,
`cu-ox-diffuse`, `cu-ox-sei`, `cu-ox-final`, `cu-ox-ti-bulk` — which appear
nowhere in the skills, the prompt or the sample notes. Both rejected the SEI and
bulk-titanium branches on BIC, both inflated intervals by √χ² = 1.83, and both
recovered Cu 483 Å, Ti 49.5 Å, SiO₂ 30.6–30.8 Å.

They differed in manner, not in method. The Claude session narrated
continuously — 36 substantive reasoning blocks, incremental and empirical — and
cross-checked its conclusion against evidence outside the fit, noticing that the
data file's run title reads `..._BF4_EtOH_ramp_OCV2`. The GPT session planned
rather than narrated — 7 blocks, mostly restatements of the same five-step
plan — and reached the same place in half the wall-clock (23 min against 47) with
half the output tokens. **This is the strongest evidence we have that the skill
library, not the model, determines the analysis path.** It also bounds what
swapping in a stronger model can buy: on this sample, nothing.

**And they were wrong together.** Both concluded the cell contained a protonated
solvent rather than the deuterated THF the sample notes assume, and both escalated
it as the first item needing a human. The expert reference gives `THF SLD = 6.2`,
i.e. deuterated. The reasoning was locally sound — the ambient fitted protonated
in all five branches, and the run title really does say EtOH — but it is an
artifact of an under-specified stack. 206915 is a *cycled* sample, on which the
notes state that a lithium-rich plated layer replaces the copper oxide and an SEI
forms above it. With those two real layers absent, the fit had nowhere to put
their scattering and drove the ambient down to compensate. Both sessions did test
an SEI branch and rejected it on BIC — but that branch added an SEI *without* the
plated layer and *on top of* an already-protonated ambient, so it tested the wrong
hypothesis and the rejection does not carry.

The fourth arm shows this is recoverable rather than intrinsic to the data:
opencode, on the *same model* as azure-nrw, built the reference structure
(dTHF / SEI / plated / Cu / Ti / SiO₂ / Si), kept the solvent deuterated, and
reached χ² 1.09 against the reference's 1.05. **A confident, well-argued,
carefully escalated wrong answer and the right answer came from the same model in
two different harnesses.** For a system whose claim is that provenance makes an
LLM analyst checkable, that is the case worth dwelling on: the ledger did not
prevent the error, but it made the error legible — the escalation states its
evidence, so a reader can see both why it was believed and what would refute it.

**A scoring bug this case exposed.** All four numbers above are post-fix. The
harness had been scoring the *newest* fit on disk, and an nr-workbench session
ends wherever its last experiment left it — often a branch run in order to be
rejected. Both Claude-harness arms reported χ² 3.33 and were scored 3.82, the
bulk-titanium branch each had discarded. Eight of 21 scored cases were reading a
fit the agent had not put forward. The harness now selects, in order: the fit id
named in the sample's own reports; failing that the lowest-BIC fit, which is the
criterion the agents themselves select on and which reproduces their explicit
designation in 4 of the 5 sessions that recorded one; failing that the newest fit.
The general lesson is the one §6.4.1 already reports for token accounting —
**an agentic system must be scored on what it reports, not on what it last did**,
and the two are not the same artefact.

#### 6.4.4 The frontier model is not doing the work

**[⚠ PROVISIONAL — 7 curves, one sweep each.]**

The arms above vary the harness. This one varies the model and holds the harness fixed: AuRE run
against **`gpt-oss-120b`**, a 120-billion-parameter open-weight model self-hosted at the facility,
on the same seven curves as AuRE-on-`gpt-5.4`.

| | AuRE gpt-5.4 | AuRE gpt-oss-120b |
|---|---|---|
| good | **4 / 7** | **4 / 7** |
| median χ²_red | 1.19 | 1.98 |
| LLM calls per curve | 10 | 10 |
| total prompt per curve | 36,061 | 36,920 |
| output per curve | 1,453 | **6,009** |
| failed calls | 0 | 0 |
| wall-clock per curve (median) | 2.69 min | 2.69 min |
| cost per curve | $0.07 | **~$0** |

**The open-weight model scores the same 4 of 7, and beats the frontier model on two curves**
(201152: 1.36 against 1.96; 201179: 1.09 against 1.11) while losing on two others (201290, 201306).
Case by case the two disagree; in aggregate they tie.

Two structural numbers are more informative than the score. **Both make exactly ten LLM calls per
curve, and both send within 2% of the same prompt volume** (36.1k against 36.9k). That is the
workflow asserting itself: AuRE decides what is sent and how often, so substituting the model barely
perturbs the input side at all. The open-weight model does emit 4× more output for the same task,
reasoning more verbosely to reach equivalent answers, but that is the only large difference and it
falls on the cheap side of the ledger.

Wall-clock is a dead heat — 2.69 min median for each, 18.58 against 18.61 minutes across seven
curves. Self-hosting costs no time here because AuRE's runtime is dominated by refl1d fitting rather
than by inference, which is a property of the architecture rather than of the endpoint.

**This is the result that matters for deployment.** The gap between AuRE and the agentic harnesses in
7.4.2 is not bought with a frontier model: it survives replacing that model with open weights on the
facility's own hardware, at equal quality, no per-token charge, and no data leaving the institution.
For a user facility weighing an agentic assistant against procurement, export control and data
residency, the architecture is what is doing the work — not the vendor behind it.

**Caveats.** Seven curves and a single sweep per arm; 4/7 against 4/7 is an aggregate tie over a
sample far too small to call the two models equivalent, and they differ on four of the seven
individual cases. The median χ²_red is worse for the open-weight arm (1.98 against 1.19), so "equal"
holds on the pass criterion and not on fit quality. `duration_s` was lost from the frontier arm's
stored snapshots by a harness defect, so both wall-clock figures are derived from the first-to-last
timestamp in each run's LLM ledger — a like-for-like span, but one that excludes setup and any
fitting after the final call.

### 6.5 What we have not done, and the study that would settle it

The evaluation above is observational, and weaker in protocol than the neighbouring work: NeuDiff
Agent reports repeated end-to-end runs with error bars across two LLM backends and a fixed prompt
protocol, partitioning user time from machine time, for an effect size (4.6–5.0×) close to ours. We
adopt that protocol below rather than defend ours. A referee is entitled to ask for the following,
and we agree:

0. **The 51-curve benchmark, run end to end** (§6.6). The corpus, its calibrated references with
   per-parameter MCMC uncertainties, and the scoring instrument of §5.5 all exist; what is missing
   is nr-workbench's own arm. Reported with repeated runs and error bars, and with the *unassisted*
   agent separated from the human-assisted completion, so the assisted number cannot absorb the
   unassisted one.
1. **A blind expert comparison.** N datasets, each analysed independently by (a) an expert with
   conventional tools, (b) the same expert with nr-workbench, (c) the bounded agent with a human
   reviewer. Domain experts blind to condition rate the resulting reports for correctness,
   completeness and defensibility. Primary endpoint: expert-hours to a defensible interpretation.
2. **A provenance ablation.** The same agent, the same tasks, with the fit-chain digest removed from
   its context. Endpoints: repeated fits, contradictory conclusions, turns to convergence. This is
   the direct test of §5.3's central claim and it is cheap to run.
3. **A mandatory-field ablation.** `--note` required versus optional, measured on fill rate and on
   the usefulness of the resulting notes. §5.2 is a natural experiment with n = 1 analyst; this would
   make it a designed one.
4. **Cross-operator generalisation.** Every result in this paper comes from one analyst, who is the
   tool's author. Until other people at other instruments use it, the external validity is unknown.

### 6.6 The benchmark corpus

The evaluation instrument of §5.5 is applied to a corpus of 51 reflectivity curves with expert
reference fits, previously used in two published studies [PLACEHOLDER: cite both]. Because the raw
expert values do not sit at the data's χ² minimum — the roughness convention and a sub-unity beam
intensity need re-optimising, giving χ² ≈ 20–40 — each reference is **calibrated once and frozen**:
refl1d is seeded at the expert values and run under a tightly-bounded DREAM refit in which thickness
and SLD stay within a small window of the reference while roughness and beam intensity are freed.
The stored reference carries the refined structure, χ² on the fitting engine's own scale, the
original expert values, and **1-σ uncertainties on every parameter from the MCMC chain**; for copper
this brings the median reference χ² from ≈30 to ≈1.7.

Two limitations of the numbers in §5.5.3 must be stated. They are computed over the **9 scored cases
of a 14-case sweep**, not the full 51 — five runs were lost to infrastructure failure (the local
model server evicting a 65 GB model during long fits), which is an operational rather than a
scientific measure and is excluded from fit-quality denominators. And the tolerances on `Γ` and `z̄`
are first-cut values; they should be derived from the spread of the reference store's own MCMC
uncertainties before publication, so that the criterion is the reference's own precision rather than
a round number. [AUTHOR: both are tracked in the harness configuration and flagged there.]

---

### 6.7 Which design for which regime

The measurements above do not name a winner, and reading them as if they did would be the wrong
lesson. They separate two operating regimes that this field has mostly treated as one.

**In the loop — during the experiment.** Beamtime decisions are made between measurements: is this
film the thickness we asked for, has the layer started to grow, is the next contrast worth the shift.
The requirements are latency, cost, determinism, and completeness — the same analysis, run the same
way, on every curve as it lands, without a person waiting on it. **The targeted agent is the better
instrument here, and by a margin that is not close.** Ten LLM calls per curve against 78–100; $0.07
against $12.30; a prescribed order that reaches the same pass rate; and, because that order writes
the model specification itself, no run that cannot be interpreted afterwards — against 9 of 21 for
the general agent (§9.10). It also runs unchanged on a 120B open-weight model on facility hardware
at the same pass rate (§6.4.4), which for a user facility settles procurement, export control and
data residency in one move. A prescribed workflow is cheap to run a hundred times a day; a
conversational one is not.

**Out of the loop — the collaboration.** Afterwards, the questions are the ones nobody specified in
advance: why does this residual survive every model we can write, is the solvent what the notes say,
does the oxide survive polarisation. Here the fixed order is the binding constraint, and the general
agent's freedom is the point. It installs the skills the sample turns out to need, tests hypotheses
in an order it chooses, escalates what it cannot decide with the evidence attached, and writes the
report tiers a collaborator reads. §6.1's negative result is what makes this regime irreducible:
11 of 17 findings needed judgement no artefact could supply. Judgement needs a collaborator, and a
collaborator needs a system that can be argued with.

**The two failure modes are the mirror of the two strengths.** The targeted agent fails by not
considering what its designer did not: on 206915 it reported the oxide-only structure and never
tested a plated layer, because no node exists that would. The general agent fails by omitting a
premise nothing forces it to state: the same run's specifications never declared
`probe.back_reflection`, across two harnesses and two frontier models (§9.10). One design cannot
leave its rails; the other has none to leave.

Two observations from §6.4 sharpen the choice. First, **the model matters far less than the
architecture**: swapping a frontier model for open weights inside the targeted agent changed the
pass rate not at all, and swapping the frontier model inside the general agent produced sessions so
alike they invented the same five model names in the same order (§6.4.3). Second, **cost is
architectural, not incidental** — the general agent's bill is dominated by re-reading a conversation
that grows every turn, and on a backend without the caching primitive its harness was designed
against it rises to $17.56 per curve (§6.4.2). Neither number is a tuning failure; both follow from
what each design is.

The practical recommendation is therefore not "use this one". It is: **run the targeted agent on
every curve as it arrives, and bring in the general agent for the curves that do not resolve.** The
first is an instrument; the second is a colleague. A facility that deploys only the first will
automate the easy cases and stall on exactly the ones that matter; one that deploys only the second
will pay a hundred times over for answers it could have had in ten calls.

## 7. Transfer to other scattering techniques

The reflectometry specifics are replaceable. What we believe generalises is a set of seven components
and the relations between them.

| Component | Invariant | What changes per technique |
|---|---|---|
| Typed experiment spec | The sample, conditions and hypothesis are *declared and validated*, not inferred from prose each session | The schema's physical vocabulary |
| Forward-model code generation | The executed artefact is readable, standalone, hashable code | The engine (refl1d → GSAS-II, SasView, Mantid) |
| Model-free pre-assessment | Answer "what does the data alone say changed?" before any model exists | The statistic |
| Skill library with rationalisations | The excuses are enumerated and pre-refuted | The excuses themselves |
| Mandatory-reason provenance | Every run records why, as a required field | Nothing — this is fully general |
| Tiered reporting | Mixed teams need several altitudes of the same finding | Which concepts need explaining |
| Never-overwrite scaffolding | The tool runs on live working directories | Nothing |

### 7.1 Worked analogues

**Small-angle scattering (SANS/SAXS).** Two systems already do the agent part — SasAgent drives
SasView tools, EQSANS-CLI exposes reduction on a stable contract for an external agent (§2.5.1) — so
what follows is the part not yet done rather than a proposal to start. The degeneracy analogue is
sharp: form-factor/structure-factor ambiguity, and the polydispersity–size correlation, which is
exactly a ridge — a broader distribution of smaller objects mimics a narrower distribution of larger
ones. The invariant analogous to `Γ` is the **Porod invariant**, which is model-free and should be
the scored quantity when the split is not determined; a `z̄` analogue is the Guinier radius. The
pre-model assessment is Guinier/Porod region identification and a check that the fitted range is
where the model is valid. The "Rationalizations" section writes itself: *"χ² is fine, so the model is
right"* when three different morphologies fit the same I(q).

**Powder diffraction / Rietveld.** Also already begun — Rongzai Agent pairs an LLM with a knowledge
base and GSAS-II and reports better R_wp than human specialists on 3 of 5 samples (§2.5.1), which is
a genuine counterexample to any blanket claim that pipelines cannot beat experts on a scattering
refinement task. GSAS-II and TOPAS are scriptable, so code generation transfers directly, and
Rietveld's culture of recording refinement strategy makes the mandatory-reason field an easy sell.
The degeneracy analogue is preferred-orientation versus site-occupancy correlation, and peak-shape
parameters absorbing structural signal. The pre-model assessment is Le Bail/Pawley extraction — a
genuinely model-free statement of what the pattern contains before any structure is asserted.

**Inelastic / quasi-elastic neutron scattering.** The ridge is the classic one: instrumental
resolution versus intrinsic linewidth, and the number of Lorentzian components. The model-free
pre-assessment is the resolution-deconvolved elastic incoherent structure factor. Mantid provides the
engine. QENS is a strong candidate precisely because the "how many components" question is structural
and contested.

**X-ray reflectometry.** Nearly a drop-in: same forward model, different scattering lengths, no
isotopic contrast variation. It is the natural first port because the physics module is the only part
that changes.

### 7.2 Preconditions

The approach needs: a **fast forward model** (seconds to minutes, so a session can afford tens of
fits); an **existing scriptable fitting engine** (do not write one); **standard file formats** with
machine-readable metadata (the reconciler in §4.3 is only possible because the headers carry the
operator's own labels); and a **well-posed-enough inverse problem** that the open question is
structural rather than existential.

One precondition deserves separating out, because it decides how much governance a technique needs.
**Does an external validator exist?** Single-crystal crystallography has checkCIF, and NeuDiff Agent
can therefore close its loop on an independent verdict — no level A or B alerts is a check nobody in
the loop authored. Reflectometry has no equivalent, and neither does SANS. Where the validator
exists, a fixed-graph pipeline is a reasonable architecture, because the gate at the end catches what
the pipeline could not anticipate. Where it does not, the burden shifts onto the analysis itself:
there is no terminal check, so the discipline has to be distributed through the run — which is the
argument for the invariants of §5.5, the contradiction checks of §4.2, and ultimately for keeping a
human in the loop at all. **A technique without an external validator needs more governance, not
less.**

It transfers badly where a single forward evaluation takes hours (large-scale MD-backed refinement),
where the analysis is dominated by data reduction rather than modelling, or where no scriptable
engine exists and the community works through a GUI. In the last case, build the scriptable path
first; it is worth doing on its own merits.

### 7.3 How to start

The smallest useful first milestone is **not** an agent. In order:

1. **One layout and an immutable result directory** with input hashes and a captured environment.
   Weeks of work; immediately valuable to humans; the foundation for everything else.
2. **A required reason field** on whatever command produces a result. One afternoon. §5.2 is the
   evidence that this is the highest ratio of value to effort in the entire system.
3. **The model-free pre-assessment**, whatever it is for your technique.
4. **The reconciler** — compare file headers against the scientist's own notes. Ours found an error
   that had sent five fits down the wrong path, and it is a string comparison.
5. **A typed spec and a generator**, pinned by a gate test against a trusted hand-written script.
6. **Skills**, written *after* you have made the mistakes, from the mistakes.
7. **The agent, last**, with the limits in place before the first unattended run.

Steps 1–4 deliver most of the value and involve no LLM at all. We consider this the single most
useful thing in this paper for another group to take away.

---

## 8. Deployment at a user facility

### 8.1 What changes for the user

Beamtime decisions ("should I keep counting? change contrast?") and post-experiment interpretation
are different problems with different tolerances. nr-workbench currently targets the second, with a
watch mode that analyses each measurement as it settles — which puts a first-pass answer in front of
the user *during* the beamtime, when the sample is still mounted and a follow-up measurement is still
possible. That is the change with the highest scientific value, and also the one with the highest
risk of over-trust.

### 8.2 Topologies

The code today supports a per-user install (`pip install`, `nrw init` in a directory) and a local
Flask UI. A facility deployment would plausibly want: a shared analysis host with per-user projects
on facility storage; integration with the data catalogue so `nrw init --ipts` populates itself; and a
web service for read-only sharing with collaborators who will never install anything. An exporter to
ORNL's ISAAC data pipeline already exists, deliberately written against a *file contract* rather than
against another package's internals.

### 8.3 Hard constraints

**LLM access.** The harness requires a commercial model API reachable from the analysis host. This is
the single largest deployment obstacle at a DOE facility: network egress, export-control review of
what leaves in a prompt (sample descriptions can be proprietary or pre-publication), and a
procurement path for per-token cost. An air-gapped deployment with a local open-weights model is
possible in principle and would need re-evaluation from scratch — none of our measurements transfer.

**Cost and who pays.** Per-analysis cost is bounded by the session turn budget but is not currently
metered per project. A facility needs this attributed before it can offer the service.

**Model version drift.** The environment capture records package versions, not the model that drove
the session. Two sessions months apart are not the same instrument. The transcript is retained, which
makes the session auditable, but *reproducible* is too strong a word for the agent's contribution.
We flag this as an open problem rather than a solved one; it is the sharpest reproducibility question
the whole approach raises.

**Retention.** Fit records are small; DREAM chains are not — one orphaned posterior in the reference
project was 298 MB. A facility retention policy must distinguish the record (keep indefinitely) from
the chain (keep for a defined window, regenerable from the record).

### 8.4 Trust and accountability

Who signs off on an agent-produced fit? Our answer is mechanical: the agent cannot promote a result,
by two independent mechanisms, so **promotion is a human act by construction**. For that signature to
mean something the record must contain what §5.1 lists, and the human must have read the technical
tier, not the summary.

The over-trust failure mode is real and is designed against: three-valued convergence, the
contradiction checker, the two-fitter rule, regression warnings that name the suspect edit, red-flag
sections written in the imperative, and the `is_blank` logic that refuses to let generated prose
masquerade as human thought. The general principle behind all of them — and the one we would give a
facility as its acceptance criterion — is:

> **The failure modes that matter are the ones that make a wrong answer look like a checked one.**

All three defects found in an autonomy survey of this codebase were of that kind, and none would have
been visible to an unattended daemon.

### 8.5 Staged rollout

1. **Provenance only, no agent.** Deploy `nrw init` / `fit run` / `ls` / `whence` / `check` to willing
   users. Success: results traceable, users prefer it. Risk: near zero.
2. **Assisted, interactive, expert users.** Agent on, human driving, promotion by hand. Success:
   time-to-first-model down; no incorrect promoted results. Collect the §6.5 data.
3. **Unattended overnight during beamtime**, declared fits only, escalations reviewed each morning.
4. **General user offering**, with training and an explicit statement of what the system does not do.

Metrics to collect from stage 1, because they are the follow-up paper: fits per project, fraction
with a stated reason, fraction promoted, time from last measurement to first report, corrections
after promotion, and user-reported trust.

### 8.6 Support

The unglamorous determinant of success. A facility must answer who fixes it at 03:00 on day two of a
beamtime. Our mitigation is that the offline checks, the generator, and the provenance layer all work
with no LLM at all — so a network outage degrades the system to a good conventional workbench rather
than breaking it.

---

## 9. Limitations and failure modes

1. **Single operator, single instrument, single technique.** Every result here comes from one analyst
   at one beamline, who wrote the software. This is the dominant threat to validity.
2. **No controlled comparison.** §6.2 is an existence proof; §6.5 is what would settle it.
3. **The agent's contribution is not reproducible** in the sense the fits are. Model version and
   sampling temperature are not captured; only the transcript is.
4. **Provenance covers fits, not reasoning.** The ledger records that a fit ran and why the analyst
   said it ran. It does not record the chain of inference that produced the sentence.
5. **No re-run from record.** The CLI has 51 leaf commands and none of them re-executes a fit
   directly from its own record; re-running requires regenerating from the spec. `nrw pack` produces
   a runnable bundle for a third party, which is the closest thing.
6. **Facility-specific instrument knowledge is hard-coded.** BL-4B conventions live in the source.
   Porting to another reflectometer is real work, deliberately.
7. **The skills encode one group's practice**, including its blind spots. A rationalisation nobody in
   this group makes is not in the list.
8. **Degeneracy is mitigated, not solved.** The system is better at *reporting* that a result sits on
   a ridge than at breaking the ridge. Breaking it needs contrast variation, which is an experimental
   decision.
9. **Over-trust remains the principal residual risk**, and no mechanism in §8.4 is a proof against a
   user who reads only the summary tier.
10. **[The system under test is deliberately NOT changed in response; see below.]
    Agent-authored specs can omit a premise the downstream interpretation cannot do without.**
    On the benchmark corpus of §6.6, **9 of 21 nr-workbench runs** initially could not be scored
    *at all* because no model spec in the session declared `probe.back_reflection`. Without it the
    stack orientation is unresolvable, and the extractor refused to guess rather than silently score
    a mirrored structure — the correct call, but the refusal lands only at extraction, after a full
    session. On `cu_film/Cu_0/201136` none of the **nine** specs the agent authored declared it.

    **The omission is not a property of any one model or harness.** It appeared across all three
    nr-workbench configurations — 2 of 7 runs under Claude Code with `claude-opus-5`, 3 of 7 under
    Claude Code with `gpt-5.4`, and 4 of 7 under opencode with `gpt-5.4` — and `201136` failed in
    every one of them. Two harnesses, two frontier models, one shared blind spot. That rules out the
    fluent-improvisation explanation we first reached for and locates the gap where the evidence
    below already pointed: in the spec-authoring path itself.

    It is **not** a briefing failure. The sample brief is byte-identical to the neighbouring case
    that succeeded apart from the run number, and states in prose: *"Neutrons are reflected from the
    substrate side (back reflection)."* `201144` declared the field in its first spec and every spec
    after; `201136` never did, and eight derived specs inherited the omission unchallenged.

    Three layers each permit the omission, and none is the agent being careless:
    `back_reflection` sits under `properties` in `$defs/Probe` of `nrw-model/1` with **no `required`
    entry**; the spec-authoring skill (`nrw-model-spec/SKILL.md`) never mentions the field; and the
    exemplar it instructs the agent to copy, `assets/example-corefine-tnr.yaml`, omits it — as do
    both `nrw-model` specs in the repository. The agent that failed copied the example it was
    pointed at. The agent that succeeded added the field *despite* it.

    **The system under test is still not fixed, and the reason is methodological rather than
    technical.** Every one of the available fixes — adding the field to the exemplar, naming it in
    the skill, running the extractor's existing two-signal agreement check at spec-write time,
    making the schema field `required` — would tune the system under test to a failure the benchmark
    surfaced.

    What *was* changed is the measuring instrument, which is a different object. The extractor
    required two independent signals for stack orientation, and took the first of them to be the
    agent's own declaration; it now takes the corpus's recorded measurement geometry instead, still
    cross-checked against the end materials and still refusing on disagreement. That is strictly
    stronger evidence — the geometry of the experiment does not depend on the system under test
    remembering to write a field — and it recovered all 9 runs without touching nr-workbench. The
    line we are holding is that a benchmark may fix its own scoring, and may not fix the thing it
    is scoring. That is moving the goalposts, and the tweak surface is unbounded: it grows with every
    harness and every model we put behind it, so there is no principled place to stop. A system
    repaired against its own benchmark measures the repairs. This is the same discipline §6.6's
    calibration and the pre-registered task text exist to enforce, and the same failure mode §10
    names in the field at large.

    One technical note worth keeping for whenever this *is* revisited: the obvious fix is the wrong
    one. Making the schema field `required` buys presence, not correctness, and it would convert
    today's loud failure into a possibly-silent one — an agent obliged to write something writes a
    plausible default, and a *coherent* misunderstanding (wrong flag **and** a stack authored in the
    matching wrong order, one mistake producing both) makes the extractor's two signals agree and
    passes a mirrored structure straight into the score. Absence is currently the safest state
    because it is unambiguous. It would also retroactively invalidate every existing spec against a
    hashed, immutable ledger (§5), requiring `nrw-model/2` and a migration.

    **The contrast, not the defect, is the result.** This is the cleanest available separation
    between the two architectures, and the reason to keep the failed run rather than re-roll it.
    AuRE constructs its model definition in code (`model_builder`), so the geometry is structural
    and this omission is not expressible; the cost is a workflow that can only produce the models
    its code can build. nr-workbench has the agent author the spec, which is what makes it strong at
    interactive hypothesis iteration — the nine specs here are a genuine, well-argued refinement
    sequence, not noise — and what lets one unstated premise invalidate the session. **The fluency
    and the failure are the same property, and the two designs are therefore not competing answers
    to one question but different trades: freer authoring buys exploration and costs guarantees;
    prescribed construction buys guarantees and costs reach. Neither dominates, and an either/or
    framing of the two is a mistake we should not make in this paper.**
    [AUTHOR: that last point sits in tension with §10's current framing, which reads as AuRE having
    got the principle backwards. Reconcile the two before submission — the honest claim is probably
    that AuRE's error was believing prompts alone were sufficient, not that prescription is wrong.]

    Status at time of writing: **2 of 3 nr-workbench cases scoreable** — far too small a denominator
    to quote. Settle it against the completed 51-case sweeps and report it as a rate with the AuRE
    arm alongside, as a measured property of the freer design rather than as a bug.
---

## 10. Conclusion

Reflectometry analysis resists automation for a specific and instructive reason: the quantity that an
automated loop would optimise ranks the candidate answers backwards, and the decisions that matter are
under-determined by anything on disk. In a curated week of expert analysis, 11 of 17 findings needed
judgement that no artefact could supply.

What can be automated is everything around that judgement — and it turns out to be most of the work.
nr-workbench supplies a general coding agent with a bounding box it cannot talk past, a method
encoded as skills that pre-refute the analyst's own rationalisations, and a ledger in which every fit
is immutable, hashed, and carries a mandatory statement of why it was run. The ledger is the
mechanism: it compresses a fit history by more than two orders of magnitude so the agent can afford
to know its own past, and it makes claims auditable months later. AuRE automates the same work
along a prescribed path, and gives up the ability to go anywhere its designer did not put a node —
which is precisely why it costs ten calls and always produces a record that can be read back.

Several groups built governed agents for scattering analysis while this one was being written, and
that convergence is the useful signal in it: the architecture is not the hard part. The hard part is
knowing whether any of them is right. On a published benchmark, the statistic these systems are
scored on certifies 78% of an autonomous system's answers where the physics certifies 22%, and not
one of its fitted profiles is physically admissible. A field that automates faster than it learns to
evaluate will produce a great deal of work that looks checked.

The design lesson we would most like to pass on is not that one of our two systems won. Measured
against the same curves, the targeted agent reached the same pass rate as the general one for a
hundredth of the cost and ten times fewer calls, and never produced a run that could not be
interpreted; the general agent reached the analyses nobody had specified, said what it could not
decide, and wrote the reports a collaborator reads. Each failed in the shape of its own strength —
one could not leave its rails, the other had none to leave. The lesson is to know which regime you
are in, and to stop expecting a single architecture to serve both. What does generalise across both
systems is narrower and harder:

> **An instruction is not a mechanism, and one mechanism is not two.**

Prompts express intent. Mechanisms survive a model that decides the rule does not apply. Build both,
and be clear at every point in the system about which one you have.

---

## Code and data availability

nr-workbench: `https://github.com/neutrons-ai/nr-workbench` [VERIFY — confirm public release, license
BSD 3-Clause, and archive a tagged release with a DOI]. AuRE: `https://github.com/neutrons-ai/aure`
[VERIFY]. The benchmark corpus of §6.6, the five sweep result sets, and the per-call LLM ledgers
behind §6.4 are available at [PLACEHOLDER — deposit and cite]. The two beamtime projects that the
scale and timing figures of §5.3 and §6.2 are measured on are available on request pending [PLACEHOLDER: IPTS data-release status; the aqueous IPTS is 36897,
the non-aqueous IPTS-34347].

## Author contributions

[PLACEHOLDER]

## Acknowledgements

[PLACEHOLDER — beamline staff; the analyst whose recorded findings form the reference corpus; SNS
beamtime allocations; funding.]

---

## References

> **Verification status.** Entries marked ✅ were checked against Crossref/OpenAlex/arXiv. Entries
> marked ⚠️ are unverified — check author list, venue, year and DOI before circulation. One entry in
> an earlier draft (Mantid, "Vats, C.") was **fabricated** and has been corrected; treat every ⚠️ as
> capable of the same failure until checked.
>
> **Self-citations to disclose:** M. Doucet is an author of Mantid [19] and SasView [21], and of the
> two studies the §6.6 corpus comes from. Journal policy requires these be declared.

**Governed agents for scattering analysis** — the §2.5.1 neighbours. All ⚠️: verify every one, and
add any that appeared after this draft.

1a. Xiao, Z., Zhang, L., Zhang, G. & Wang, X. (2026). NeuDiff Agent: a governed AI workflow for
    single-crystal neutron crystallography. *J. Appl. Cryst.* **59**, issue 4. arXiv:2602.16812. ⚠️
    *Verified to exist and to be published in this journal; confirm volume, pages and DOI.*
1b. Ding, L. & Do, C. (2025). SasAgent: multi-agent artificial intelligence system for small-angle
    scattering data analysis. *J. Appl. Cryst.* arXiv:2509.05363. ⚠️ *ORNL Neutron Scattering
    Division; verified to exist.*
1c. Do, C. (2026). EQSANS-CLI: a natural-language, agent-ready command-line tool for small-angle
    neutron scattering data reduction at EQ-SANS. arXiv:2605.00651. ⚠️
1d. Li, Q. *et al.* (2026). Rongzai agent: a large language model-based autonomous assistant for
    Rietveld refinement of neutron diffraction data. arXiv:2605.13911. ⚠️ **Unverified by us.**
1e. Deng, B. *et al.* (2026). Harnessing AtomisticSkills for agentic atomistic research.
    arXiv:2605.24002. ⚠️ **Unverified by us.**
1f. ColPackAgent: agent-skill-guided hard-particle Monte Carlo workflows for colloidal packing.
    arXiv:2605.15625. ⚠️ **Unverified by us; authors not captured.**
1g. Souza, R. *et al.* (2025). PROV-AGENT: unified provenance for tracking AI agent interactions in
    agentic workflows. *IEEE e-Science*. arXiv:2508.02866. ⚠️ **Unverified by us.**

1. Kienzle, P. A. *et al.* **refl1d** / **bumps**: depth-profile modelling and uncertainty analysis.
   [VERIFY — cite the software and its DOI]
2. Nelson, A. R. J. & Prescott, S. W. (2019). refnx: neutron and X-ray reflectometry analysis in
   Python. *J. Appl. Cryst.* **52**, 193–200. DOI 10.1107/S1600576718017296 ✅
3. Pospelov, G., Van Herck, W., Burle, J., Carmona Loaiza, J. M., Durniak, C., Fisher, J. M.,
   Ganeva, M., Yurov, D. & Wuttke, J. (2020). BornAgain: software for simulating and fitting
   grazing-incidence small-angle scattering. *J. Appl. Cryst.* **53**, 262–276.
   DOI 10.1107/S1600576719016789 ✅
4. Majkrzak, C. F. & Berk, N. F. Phase-sensitive neutron reflectometry / exact inversion. [VERIFY]
5. Greco, A., Hinderhofer, A., Schreiber, F. *et al.* Neural-network analysis of X-ray/neutron
   reflectivity; **mlreflect**. [VERIFY]
6. Boiko, D. A., MacKnight, R., Kline, B. & Gomes, G. (2023). Autonomous chemical research with
   large language models. *Nature* **624**, 570–578. DOI 10.1038/s41586-023-06792-0 ✅
7. M. Bran, A., Cox, S., Schilter, O., Baldassari, C., White, A. D. & Schwaller, P. (2024).
   Augmenting large language models with chemistry tools. *Nature Machine Intelligence* **6**,
   525–535. DOI 10.1038/s42256-024-00832-8 ✅ *"ChemCrow" is not in the published title; the
   surname is "M. Bran".*
8. Szymanski, N. J. *et al.* (2023). An autonomous laboratory for the accelerated synthesis of
   **inorganic** materials. *Nature* **624**, 86–91. DOI 10.1038/s41586-023-06734-w ✅
   *Cite with its Author Correction, Nature **650**, E1 (2026), DOI 10.1038/s41586-025-09992-y.*
8a. Leeman, J., Liu, Y., Stiles, J., Lee, S. B., Bhatt, P., Schoop, L. M. & Palgrave, R. G. (2024).
   Challenges in high-throughput inorganic materials prediction and autonomous synthesis.
   *PRX Energy* **3**, 011002. DOI 10.1103/PRXEnergy.3.011002 ✅
   *Cite this **supportively** in §8.4: it critiques A-Lab's automated phase identification, which is
   precisely the "wrong answer that looks like a checked one" failure mode this paper is about.*
9. Noack, M. M. *et al.* **gpCAM** / Gaussian-process-driven autonomous experimentation at scattering
   beamlines. [VERIFY]
10. Kalinin, S. V., Ziatdinov, M. *et al.* Autonomous and machine-learning-driven microscopy and
    spectroscopy. [VERIFY]
11. Yao, S. *et al.* **ReAct**: synergizing reasoning and acting in language models. ICLR 2023.
    [VERIFY]
12. Shinn, N. *et al.* **Reflexion**: language agents with verbal reinforcement learning. NeurIPS
    2023. [VERIFY]
13. Wang, G., Xie, Y., Jiang, Y., Mandlekar, A., Xiao, C., Zhu, Y., Fan, L. & Anandkumar, A.
    (2023). Voyager: an open-ended embodied agent with large language models. arXiv:2305.16291 ✅
    *Title corrected — "with a growing skill library" was a paraphrase. Cite arXiv; a TMLR record
    could not be confirmed.*
14. Jimenez, C. E. *et al.* **SWE-bench**: can language models resolve real-world GitHub issues?
    ICLR 2024. [VERIFY]
15. Moreau, L. & Missier, P. (eds.) (2013). PROV-DM: the PROV data model. W3C Recommendation. ✅
    *Editors, not "et al." authors.*
16. Soiland-Reyes, S., Sefton, P., Crosas, M., Castro, L. J., Coppens, F., Fernández, J. M.,
    Garijo, D., Grüning, B., La Rosa, M., Leo, S., Ó Carragáin, E., Portier, M. *et al.* (2022).
    Packaging research **artefacts** with RO-Crate. *Data Science* **5**, 97–138.
    DOI 10.3233/DS-210053 ✅
17. Mölder, F. *et al.* Sustainable data analysis with **Snakemake**. [VERIFY]
18. Di Tommaso, P., Chatzou, M., Floden, E. W., Prieto Barja, P., Palumbo, E. & Notredame, C.
    (2017). Nextflow enables reproducible computational workflows. *Nature Biotechnology* **35**,
    316–319. DOI 10.1038/nbt.3820 ✅
19. Arnold, O., Bilheux, J. C., Borreguero, J. M., Buts, A., Campbell, S. I., Chapon, L.,
    **Doucet, M.**, Draper, N., Ferraz Leal, R., Gigg, M. A. *et al.* (2014). Mantid — data analysis
    and visualization package for neutron scattering and μSR experiments. *Nucl. Instrum. Methods
    Phys. Res. A* **764**, 156–166. DOI 10.1016/j.nima.2014.07.029 ✅
    *An earlier draft listed a first author "Vats, C." who **does not exist on this paper** — a
    fabricated citation, corrected here. Self-citation: disclose.*
20. Toby, B. H. & Von Dreele, R. B. (2013). GSAS-II: the genesis of a modern open-source all
    purpose crystallography software package. *J. Appl. Cryst.* **46**, 544–549.
    DOI 10.1107/S0021889813003531 ✅
21. Doucet, M. *et al.* **SasView** small-angle scattering analysis. [VERIFY]

---

## Appendix A — Author's to-do before circulation

**Numbers to supply or verify**

- [ ] Total skill line count (§4.2 shows `4,5xx` — measure exactly).
- [ ] Regenerate the background-comparison ΔBIC figure noted as unverified in the synthesis §7.
- [ ] Re-run the convergence diagnostic on the stored THF chains (cheap; closes the §6.3 gap).
- [ ] Confirm "one week" for the original April 2025 analysis — is there a better-documented figure?
- [ ] Reference electrode and potential scale (aqueous); supporting electrolyte (THF). Both are
      flagged `[TO CONFIRM]` in the synthesis and are experimental facts the data files do not carry.
- [ ] Fill the blank beamtime label and IPTS in `jen-jun2026`'s project configuration.

**Decisions**

- [ ] Is §6.4's development-speed comparison (69 commits/9 days vs 112/5.5 months) in or out? It is
      striking and honest but invites a "you are marking your own homework" response. My
      recommendation: keep it, in §6.4 exactly as hedged, because a referee who finds it in the
      repository and not in the paper draws a worse conclusion.
- [ ] The operando Cu case study was cut in v2. Its two reversed findings are no longer claimed
      anywhere; confirm nothing downstream still relies on them, and decide whether the collaborators
      whose experiments they were should still be acknowledged.
- [ ] §5.3 and §6.2 still measure on `jen-jun2026` and `cu-thf-expt11`, which v2 no longer
      introduces. Either add one sentence of provenance where they first appear, or move those
      figures onto the benchmark corpus.
- [ ] **AuRE's role (§2.6 / §3).** Now framed as a *concurrent* sibling system cited by commit hash,
      not a superseded predecessor — its most recent commit postdates nr-workbench's. Confirm this
      framing is one AuRE's maintainers accept. If anyone else has contributed to AuRE they should
      see §3 before circulation. The alternative, if that conversation is awkward, is to cut §3
      entirely and keep only the general §2.6 argument — the paper survives it, losing the honest
      negative result but keeping the thesis. **Recommendation: keep §3.** A referee will find AuRE
      in the dependency list regardless (`aure_adapter.py`, `upstream.toml`), and an undiscussed
      predecessor reads far worse than a discussed one.
- [ ] Verify the pinned SHAs (`3021fee`, `7ae487a`) are the right ones to cite, and re-verify §3's
      claims against whichever is chosen. AuRE is actively developed; anything unpinned will rot.
- [ ] Whether to split §7 (transfer) into a companion paper. It is the section most likely to be cut
      by a *J. Appl. Cryst.* editor for length and the one most useful to other communities.

**Figures to make**

1. Architecture (§4, replacing the ASCII sketch).
2. The ridge, and why the product is the measurement — a 2D `(ρ,t)` posterior from the THF fit with
   constant-`Γ` contours over it, and the two "thickness" readings marked. **This is the paper's
   money figure**; it makes §6.2 visual in one image.
3. Context compression (§5.3) — the three views to scale.
4. The 1/5/11 finding split (§6.1) as a simple stacked bar with the three worked examples annotated.
