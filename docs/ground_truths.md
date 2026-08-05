# Ground Truths

Key findings, decisions, and verified facts discovered during development. AI
assistants do not remember previous conversations; this file is how context
survives between sessions.

Record what is **non-derivable** from the code: API quirks, packaging traps,
why a design went one way. Do not record what the code or git history already
says.

## Findings

### 2026-08-05: Skills live at the repo root, never `.claude/skills/`

Inherited from `ai-project-template`'s 2026-07-13 decision, and it applies here
too: **GitHub Copilot cannot read `.claude/skills/`.** A repo-root `skills/`
folder is the only tool-neutral home, and both assistants reach it by `Read`.

Neither tool auto-discovers that folder, which is why every skill also gets a
**thin dispatcher agent** in both `.claude/agents/` and `.github/agents/`. The
pair is byte-identical except that the `.claude` copy carries
`tools: Read, Grep, Glob, Bash` — Copilot's agent schema flags those names as
unknown, so it is omitted there. `tests/test_skills.py` asserts exactly that
difference.

To change a standard, edit the `SKILL.md`. Never the dispatcher.

### 2026-08-05: setuptools package-data globs do not match dotfiles

`templates/**/*` follows glob semantics: `*` matches neither a leading dot nor
anything inside a dot-prefixed directory. With only that pattern, the wheel
shipped without `.gitignore`, `.github/copilot-instructions.md`,
`.vscode/settings.json` and `.vscode/extensions.json` — so an installed
nr-workbench would scaffold projects with **no Copilot instructions and no
editor schema wiring**, while an editable install looked perfectly fine.

Fixed with explicit dot patterns in `[tool.setuptools.package-data]`:
`templates/**/.*`, `templates/*/.*/*`, `templates/*/.*/**/*`.

`tests/test_packaging.py` builds a real wheel and compares its contents against
the source tree, so this cannot silently reopen. Note that the test copies the
repo to a temp directory **with `*.egg-info` stripped** first: a `SOURCES.txt`
left by an earlier build is reused by setuptools and re-includes files the
current configuration no longer packages. Without that strip the test passes
even with the fix reverted — measured, not theorised.

### 2026-08-05: AuRE ships no SKILL.md files in its wheel — FIXED, merged as `3021fee`

AuRE's `[tool.setuptools.package-data]` covered only `"aure.web"`, so none of
its eight `SKILL.md` files reached the wheel or sdist. Invisible under
`pip install -e .`, but `Dockerfile:8` installs non-editable — so
`ghcr.io/neutrons-ai/aure` ran with an empty skill registry, injecting no
domain knowledge into any prompt, with no error and no log line.
`SkillRegistry._scan` only warned when the *directory* was missing, which it
never was.

Fixed upstream (`neutrons-ai/aure` PR #20, merged 2026-08-05 as `3021fee`) and
verified from this side: a wheel built from aure main, installed non-editable
into a clean venv, exposes all eight `SKILL.md` files.

**Consequence for later milestones.** nr-workbench can now read AuRE's domain
skills from the installed package instead of vendoring copies. The three skills
shipped in M0 are unaffected — `nr-workbench-project` is new,
`tnr-change-assessment` comes from experiments-2025, and
`neutron-reflectometry` is a merge of both sources rewritten into the v2
anatomy — but the four planned pure-AuRE skills (`thin-layer-degeneracy`,
`solvent-contrast-matching`, `metal-oxide-interfaces`, `polymer-films`) should
be *read from upstream*, not copied. One fewer divergent copy to keep in sync.

### 2026-08-05: Enumerate aure's skills without importing aure

Confirmed working against the installed wheel:

```python
import importlib.util
from pathlib import Path

spec = importlib.util.find_spec("aure")          # does NOT run aure/__init__.py
skills_root = Path(spec.submodule_search_locations[0]) / "skills"
names = sorted(p.parent.name for p in skills_root.glob("*/SKILL.md"))
```

`find_spec` on a *top-level* package locates it without executing its
`__init__.py`, so this costs nothing and avoids the 1.5-3s LLM-stack import.
Do not use `find_spec("aure.skills")` — resolving a submodule imports the
parent, which defeats the whole point.

### 2026-08-05: Any `import aure` costs 1.5-3 seconds

`aure/__init__.py` eagerly imports `.state` and `.workflow`; the latter chains
through all eight node modules into `langchain_core`, `periodictable` and
`scipy`. There is **no lightweight entry point** — importing
`aure.tools.feature_tools` pays the full cost.

So every aure import in this package is function-local, confined to
`src/nr_workbench/aure_adapter.py`, and `tests/test_cli.py::
test_help_does_not_import_heavy_modules` runs `nrw --help` in a subprocess and
fails if `aure` (or refl1d, bumps, matplotlib, scipy, langchain_core) reaches
`sys.modules`.

To locate aure's package directory without executing its `__init__.py`, use
`importlib.util.find_spec("aure")`.

### 2026-08-05: Pin aure by commit SHA, never by tag or `@main`

Its `pyproject.toml` has said `version = "0.1.0"` across every release, while
tags reach `v0.1.3` — and `v0.1.0` and `v0.1.1` point at the *same* commit. pip
cannot tell two aure builds apart from their metadata, so a tag pin plus
resolver caching can silently give you the wrong one.

Consequence for provenance: the version string is worthless as an identifier.
`commands/doctor.py::_aure_commit` reads the real commit from pip's
`direct_url.json`, and that is what belongs in a fit manifest.

Also note aure declares `requires-python = ">=3.9"`, which is wrong:
`model_builder.py` uses `int | None` in an evaluated annotation with no
`from __future__ import annotations`, so `import aure` raises on 3.9. We
require >= 3.11 independently.

### 2026-08-05: `nrw init` must preserve project identity on re-run

`created` is stamped into `nrw.toml` and `README.md`. Regenerating it each run
made those two files differ every time, so `init` reported "upgrade 2 file(s)"
forever and was not idempotent — and the field came to mean "last init" rather
than "created", which is not what a provenance record wants.

`commands/init_cmd.py::_build_context` now reads the existing `nrw.toml` and
reuses `created`, `name`, `beamtime` and `ipts` unless overridden on the
command line. So a bare `nrw init` in an existing project is a true no-op and
does not blank fields the user set earlier.

### 2026-08-05: Observe a script's inputs; do not guess them from its source

Every real fit script in experiments-2025 builds its data path the same way:

```python
DATA = os.path.join(os.path.dirname(__file__), "..", "data", "steady", fname)
```

The full path never appears as a literal, and `fname` is often assembled from a
run number, so scanning the AST for string literals finds nothing. Measured: the
first implementation recorded 1 input (the script) for a script that read a data
file.

`fitting/runner.py::track_opened_files` installs a `sys.addaudithook` on the
`open` event and records every genuine file open during `runpy.run_path`. That
is exact rather than heuristic and works regardless of how the path was
assembled — through `numpy.loadtxt`, refl1d's loaders, or runtime string
building.

Two consequences worth knowing:

- **Audit hooks cannot be removed once installed.** One hook is installed lazily
  and gated on a module-level collector that the context manager sets and
  restores, so nesting works and nothing accumulates after the block.
- **The script is loaded once**, and the loaded problem is handed to `run_fit`.
  Loading it twice would double every side effect a script has.

`--dry-run` cannot observe anything, so it falls back to the literal scan plus a
unique-basename search of the project, and the two paths can disagree. That is
accepted: the recorded path is the exact one.

### 2026-08-05: bumps `fit(export=...)` still silently drops the uncertainty block

Inherited from nr-analyzer and **re-verified against bumps 1.0.4**, because a
workaround nobody rechecks becomes folklore. Measured on the same problem:

| call | files written | `-err.json` | `-chain.mc.gz` |
|---|---|---|---|
| `fit(problem, export=dir)` | 9 | no | no |
| `fit(problem)` then `export_fit(dir, problem, result)` | 19 | yes | yes |

`fit(export=...)` forwards `result.state` — a bare `MCMCDraw` — where
`export_fit` expects an `OptimizeResult`. Its
`getattr(fit, "fit_state", getattr(fit, "state", None))` resolves to `None` and
the entire uncertainty branch is skipped, with no error.

`tests/test_runner.py::test_bumps_export_kwarg_still_drops_the_uncertainty_block`
pins the bug deliberately: when bumps fixes it, that test fails and tells us the
workaround can go.

### 2026-08-05: fit_id collides for same-second replicates

`fit_id` is a second-resolution timestamp plus a content hash, so two forced
replicates of an identical run started in the same second produce the same id
and the second `mkdir` fails. `record.py::create_unique` appends `-2`, `-3`, …
Because `mkdir` is atomic, the same loop is what makes concurrent fits safe:
whichever process loses the race takes the next suffix.

### 2026-08-05: `ruff format` will reformat vendored files unless excluded

`_vendor/result_manifest.py` is a byte-identical copy of a contract shared
across analyzer_tools, data-assembler and nr_isaac_format. A plain
`ruff format src tests` reformatted it and broke that guarantee — caught by
`tests/test_vendor.py` on its first real run. `extend-exclude` in
`[tool.ruff]` now covers `src/nr_workbench/_vendor`.

### 2026-08-05: The tNR refactor is pinned by golden files, not by review

`tnr_chi2.py` was 1992 lines in one module. Splitting it was worth doing, but a
subtle change to a coadd weight or a variance sign would be invisible in review
and would quietly corrupt every future assessment.

So the split was done **mechanically**, extracting source by line range so the
numerics moved byte-for-byte, and the ASCII tables in `tests/data/tnr_golden/`
were captured from the **original** tool before any of it started. All six
tables still compare byte-for-byte identical.

Two consequences worth keeping:

- `tests/data/tnr_golden/` is only regenerated when a numerics change is
  deliberate, and the commit has to say so.
- `zip(strict=)` is *not* added in `tnr/ascii.py` or `tnr/plots.py`, because it
  changes behaviour — raising instead of truncating — in code nobody has
  re-derived. Ruff has a per-file ignore for B905 there.

Structural notes: `type_style`/`ordered_types` moved to `tnr/intervals.py`
because both the metrics and the plots need them and neither should drag
matplotlib into the other; `plots.py` forces the Agg backend at import;
`click.echo(..., err=True)` became `tnr/notify.py` so `metrics/` has no CLI
dependency.

### 2026-08-05: Three interpretation bugs the fixture caught

The synthetic run in `tests/tnr_fixture.py` injects a *known* sigmoidal
trajectory and a *known* oscillatory Q template, so the assessment can be
checked against ground truth rather than merely for plausibility. That caught
three wrong answers that all looked reasonable:

1. **Trajectory: chord-residual tests break on overshoot.** Comparing a(t)
   against a straight line through its endpoints classifies a textbook sigmoid
   as `non-monotonic` whenever the curve overshoots and settles back, because
   the chord then ends below the plateau. Slope *concentration* is the actual
   defining property of a sigmoid and is unaffected by where the endpoints land.
2. **Flat needs error bars.** Without `sigma_a` the classifier has no noise
   scale, so pure noise reliably reads as `non-monotonic` — it wanders. The
   test is now whether the first and last thirds differ by more than 3 combined
   standard errors.
3. **Variogram: a fixed threshold is wrong.** γ = 1 is the noise floor and the
   metric reports its own spread, so "is it rising?" is a significance question.
   Real run 223995 sits at γ = 1.16 with an error of 0.032 — a 5σ rise that the
   original threshold of γ > 1.3 called flat. That produced output which said
   "nothing is changing" directly above "max |a/σ| = 7.3".

Related: when the variogram and the amplitude disagree, `build_verdict` now
says `ambiguous` and names the likely cause (a reference block spanning a
period when the sample was already moving) rather than silently believing the
amplitude. The reading order says *if the variogram is flat, stop*, and the
verdict has to honour that.

### 2026-08-05: bumps CAN express logistic and exponential constraints

The plan flagged this as a spike that might force `exponential` and `logistic`
to be deferred. It does not. Verified against bumps 1.0.4:

- `bumps.parameter.exp` does **not** exist, which is what makes it look
  impossible at first glance. But `bumps.parameter.pmath` carries the full set
  (`exp`, `log`, `sqrt`, `sin`, `tanh`, …), and `np.exp(parameter)` also
  dispatches correctly and returns an `Expression`.
- A `FitProblem` whose stack contains such an expression **serializes and
  round-trips** through `bumps.serialize`.

So every constraint form ships. Do not conclude from a missing
`bumps.parameter.exp` that transcendental constraints are unavailable.

### 2026-08-05: the model gate, and the two things it caught

`tests/test_model_gate.py` drives the hand-written
`Cu-THF-218386-full-sequence.py` and the generated script from the *same*
parameter vector and compares chi-squared. It passes at 1.7e-16 — floating-point
noise — across 21 experiments and 40 free parameters, with a 62-line spec
standing in for 343 lines of Python.

Getting there required two corrections that no amount of reading would have
found:

**1. The apr2025 scripts used a different resolution convention.** The initial
generated script differed from the reference by ~1e-4 in chi-squared — small
enough to look like rounding, large enough to be real. The cause was `dL`: the
reference computes the SNS moderator emission-time polynomial, while
`angular_only` sets `dL = 0`.

**Resolved 2026-08-05: BL-4B standardises on angular-only (`dL = 0`), and the
moderator variant is not supported.** It computed
`dL = delta_wl_over_wl(wl) * q` — multiplied by q rather than by wl, which is
dimensionally wrong. Carrying both would mean two sets of results that cannot
be compared, and only one of them is right.

Consequences, all live:

- A spec asking for `resolution: moderator` is **rejected with an explanation**,
  not silently accepted. Failing loudly matters here because the alternative is
  a fit that runs fine and produces numbers nobody can compare with anything.
- **Fits made under the moderator convention must be re-run, not compared.**
  The same model on the same data gives χ² 101.983 under moderator and 101.994
  under angular-only.
- The vendored gate reference is normalised to `dL = 0` **on both sides**, so
  `tests/test_model_gate.py` measures the model structure — co-refinement,
  parameter sharing, constraints — rather than a convention that has been
  dropped. Its banner says so.

**2. Angle segments do not always share a normalisation.** The reference gives
ocv1's 3.5° segment its own intensity with a wider range, and says why:
*"The third run has a different intensity, so we don't share that parameter
with the first two runs."* The schema's original `per: model|state|measurement`
could not express "share within the state, except this one segment".

Fixed by letting `in:` name a single measurement as `group#index`, with a
specificity rule: a measurement-level declaration outranks a group-level one.
Unreferenced parameters are then pruned, so a fully-overridden group parameter
does not linger and inflate the count.

### 2026-08-05: `nrw check` polices generated scripts, not hand-written ones

The first version of the script-drift check flagged any `models/*.py` with no
spec as an `orphan-script`. It broke an M1 test, and the test was right: running
an existing hand-written script **unchanged, with no spec and no migration** is
the adoption path this package promises. Flagging every such file would make
`check` useless for exactly the case it is meant to support.

The rule is now: only files that *claim* to be generated are policed.

| condition | reported as |
|---|---|
| generated banner, self-hash mismatch | `hand-edited-script` |
| generated banner, spec changed since | `stale-script` |
| generated banner, spec gone | `missing-spec` |
| `HAND-OWNED SCRIPT` banner (a fork) | skipped -- hand-owned by design |
| no banner at all | skipped -- it is just a script |

A hand-written script is still fully tracked: `nrw fit run` records its hash,
its inputs and the environment exactly as for a generated one. Provenance does
not require the generator.

### 2026-08-05: a scaffold that does not validate teaches the pattern backwards

`nrw model new` initially emitted a spec that failed its own `nrw model
validate`: it declared a structural parameter `per: state` (which defaults to
*every* group, series included) while also constraining that path across the
series -- the double-assignment error. First contact with the schema would have
been an error message about a file the tool itself wrote.

`_scaffold_document` now scopes structural parameters to the states with an
explicit `in:` whenever a constraint owns the series, and omits the constraint
entirely when there is only one state to anchor it. The scaffold validates,
generates, and builds as written.

### 2026-08-05: A NUL byte is the binary test, not a failed decode

`_render_diff` originally detected binary content with `try: decode('utf-8')`.
That is insufficient: control bytes including NUL are valid UTF-8 code points,
so `b"\x00\x01\x02"` decodes cleanly and got fed into a text diff. Now
`_is_binary` checks for a NUL byte first, which is what git does.
