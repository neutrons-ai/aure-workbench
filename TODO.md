# TODO

## Per-state stacks in nrw-model/1: adopt AuRE's `states[].layers`

**Problem.** A spec has one stack for every state; `per:` scopes a parameter's
*value*, never a layer's *presence*. So a layer present in some states and
absent in others cannot be written: a native oxide reduced away under
potential, an SEI that forms only after cycling, a swollen layer with no dry
counterpart. Nor can a parameter be tied to a different path (`CuOx.rho` to
`Cu.rho`), which would be the other way to express it.

Found on copper-oxide/sample1 (2026-11-18): the co-refinement of 230539 (OCV)
and 230546 (under potential), testing "the oxide is gone under potential",
had to fake the absent layer by fixing the 230546 CuOx SLD at the Cu value.
That is numerically equivalent to removing the layer (to within the Cu SLD
box), but it is a workaround every reader has to be told about.

**AuRE already solves this** (README, "When the states differ in structure";
present in the AuRE nrw pins, 487a31dc): a state may declare its own complete
`layers:`, states without one inherit the shared stack, and a tie naming a
layer a state does not have simply does not apply there.

**Work:**

1. [ ] Schema: an optional per-state stack in nrw-model/1 (`states[].stack`,
   mirroring the top-level `stack:`); a state without one inherits the
   top-level stack. Same beam-enters-last ordering rule.
2. [ ] Resolution: a `per: model`/`per: state` declaration whose layer a state
   lacks skips that state, as in AuRE. A declaration whose `in:` *names* a
   state lacking the layer is an error, not a skip.
3. [ ] Codegen: one refl1d `Stack` per distinct state stack; per-state stacks
   are part of the spec hash.
4. [ ] `nrw model validate`: run the stack-order / critical-edge check per
   state stack; report which states inherit and which declare their own.
5. [ ] `nrw aure import`: `aure_import.py` reads one `layers` list. Check what
   happens today to an AuRE run whose states declare their own `layers` --
   if they are silently dropped, that is a bug independent of this feature --
   and carry them into the spec once (1) exists.
6. [ ] Bump the AuRE pin. nrw's environment runs 487a31dc (2026-09-21); AuRE
   HEAD 4f15783 has 30 more commits, including the warning when ties are set
   and no state declares `layers` (286bf81), held parameters (f67fce9), and
   the posterior evidence estimators (5169e05, 2026-10-21:
   `aure.tools.posterior_tools`, Laplace with the sampled covariance and
   bridge sampling from the DREAM chains, reported beside the BIC estimate).
   The copper-oxide/sample1 final pass needs the last one -- see below.
7. [ ] Tests: a two-state spec with an oxide in one state only builds, shares
   what it should, and matches the faked-copper equivalent to numerical
   precision.
8. [ ] Docs: the `nrw-model-spec` and `steady-state-corefinement` skills;
   say that the faked-copper workaround is no longer needed.

Lower priority, separate: path-to-path ties (`same_as:`), for the cases a
per-state stack does not cover.

## Model-selection evidence: BIC vs AuRE's posterior evidence vs nested sampling

`nrw assess` reports BIC only. AuRE's posterior evidence (Laplace + bridge
sampling from the DREAM chains, item 6 above) should be cheap to apply to an
nrw fit, since nrw already keeps the chain (`fit/<model>-chain.mc.gz`) and the
prior box. Before deciding whether nrw reports it, compare it against BIC and
against dynesty on real fits:

1. [ ] Apply `posterior_tools` to recorded nrw DREAM fits (needs item 6).
2. [ ] Compare on copper-oxide/sample1, which has dynesty evidence for both a
   decisive and a marginal comparison (sample report "To do: per-state
   stacks, then refit the final models", item 5).
3. [ ] If it tracks dynesty where BIC does not, report it in `nrw assess`
   beside BIC, with its Laplace-bridge gap as the warning that the posterior
   is not Gaussian.

## `nrw report-figure` loses outputs that did not change

Found 2026-11-18 on copper-oxide/sample1. `run_report_figure`
(`commands/report.py`) records as outputs only the files under `reports/` whose
digest changed during the run, and rewrites the manifest each time. So a re-run
that produces byte-identical figures drops them from the manifest (`nrw whence`
then says "Nothing recorded"), and a file edited by hand in `reports/` while a
long script runs is recorded as the script's output.

1. [ ] Keep outputs from the previous manifest that still exist with the digest
   recorded there, or record "written this run" by mtime rather than by digest.
2. [ ] Exclude files the script cannot have written: changed before the script
   started, or a report `.md` that a person edits.
3. [ ] Test: run twice with identical output; `whence` still resolves.

## Left over from the per-state-stacks build (branch `per-state-stacks`)

Noticed by the builder, outside that plan, and not done (2026-11-19):

1. [ ] `contradictions._roughness_coherence` still checks only the top-level
   stack -- extend it to every state stack.
2. [ ] The summary that `aure_cmd._report_result` prints after an AuRE run
   shows the template stack, not each state's own.
3. [ ] AuRE nuisance blocks marked `fixed: true` (background, theta_offset,
   sample_broadening) are imported free over their window -- honour `fixed`.
4. [ ] `spec/explain.py`'s stack table looks up `layer.material`, not
   `material_key`, so a layer with no explicit material shows "None | ?"
   (predates the branch).
5. [ ] The authoring prompts (`nrw model new --print-prompt` / `--from-notes`)
   do not mention per-state stacks.
6. [ ] `codegen/generator.py` keeps a comment explaining the code by an earlier
   version ("It used to write a comment here"), against the describe-as-is rule.
7. [ ] `aure_import.run_of` is now used only by tests -- remove or reuse.
8. [ ] A layer named after a Python keyword (`class`, `None`) still generates a
   SyntaxError in the fit script: `_unique_idents` should also avoid keywords.
9. [ ] `_fitted_intensities`' docstring says AuRE state names hold no spaces;
   they can (`ocv 2`), and the code now handles it.

## Left over from the settled-is-complete build (branch `settled-is-complete`)

Settled data is always used and later data just adds to the sample (the
person's decision, 2026-11-19). Noticed by the builder, outside that plan:

1. [ ] `spec/resolve.py` still refuses `segments: auto` when a header plans
   more segments than are on disk -- `apply` copies a partial run that specs
   then cannot use. Only `_autoreduction.dat` headers carry a plan. Removing
   the refusal is consistent with the new rule, and less code.
2. [ ] `SourceRun.last_subrun` (`experiment/inventory.py`) is no longer used.
3. [ ] Known, by choice: a fit made before its run gained a segment is not
   flagged (`nrw check` compares only the files the fit read). A
   `run-gained-data` check was built and dropped for simplicity; it is ~55
   lines in `commands/provenance_cmd.py` if it is ever wanted.

Security review of that branch (2026-11-19), logged rather than fixed by the
person's choice:

4. [ ] HIGH (real use, not a vulnerability): `nrw agent watch` can fit a run's
   first segment alone and never refit the full run. It judges a run ready on
   settle alone (`agent/watch.py:51-53`) and marks it fitted by run number
   (`_runs_already_fitted`, `watch.py:344`, `:427`); `apply` now copies segment 1
   as soon as it settles. `docs/agent.md:278-284` still promises this cannot
   happen. Fix if the watcher stays: a run counts as fitted only when all its
   current `data/steady` files were inputs of the fit. Moot if the agent is
   retired (next section).
5. [ ] LOW: a run quarantined at the source after part of it was copied is not
   an attention item (`experiment/apply.py:571` DEFERRED, `:129`
   `ATTENTION_ACTIONS`): its clean earlier segments stay in the sample and
   `apply` exits 0. Add an attention action naming the reason when a
   quarantined or clock-skew run already has recorded copies.
6. [ ] INFO: `arrival.py:9-11` and `:136-139` still say a settled run needs
   evidence about the sequence of runs to count as complete.

## Retire `nrw agent` in favour of nr-watcher

The person's call (2026-11-19): the nr-watcher project now does what `nrw
agent` does -- watch for settled measurements and run unattended sessions -- so
nrw should stop carrying its own.

1. [ ] Inventory what `nrw agent` is: `run`, `watch`, `status`, `stop`
   (`agent/session.py`, `running.py`, `watch.py`, `harness/`), the
   `[agent.limits]` table in `nrw.toml`, `--task-file`, ESCALATIONS.md and the
   unattended-session text in the scaffolded `AGENTS.md`, `docs/agent.md`, and
   the `analyst-handoff` skill's reading of an unattended session.
2. [ ] Decide what stays in nrw. `nrw agent guard` is not unattended-only: it
   is the PreToolUse hook that refuses the person's commands to *interactive*
   assistants too (and has its own open item above), so it likely stays,
   perhaps renamed out of `agent`. `nrw handoff` reads what a session left.
3. [ ] Check what nr-watcher needs from nrw (commands it calls, files it reads)
   so retiring `nrw agent` does not break it.
4. [ ] Remove the rest, with its tests and docs. Simplifies: the watcher's
   partial-run problem (item 4 above), the agent limits, and the
   unattended-session paths through `apply` and the guard.

## The agent guard refuses commands that only mention a guarded command

Found 2026-11-19: `nrw agent guard` (the PreToolUse hook) refused a shell
command that appended Markdown to this file with a heredoc, because the text
contained the words of a person-only AuRE command. Nothing was being run. The
match is on the command line's text, so a heredoc, an `echo`, or a `-m "..."`
note that quotes the command is refused too.

1. [ ] Match the command words that will execute (shell-split the line; skip
   heredoc bodies and quoted arguments to other programs), not substrings.
2. [ ] Test: a `nrw note -m` whose message names the guarded command is allowed.
