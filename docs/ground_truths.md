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

### 2026-08-05: bumps numbers its exports by position and ignores `Experiment.name`

`FitProblem.save` is `for i, f in enumerate(self.models): f.save(basename + "-%d" % (i + 1))`.
The name is used in plot titles but never in a filename, so
`cu-thf-218389-full-sequence-7-refl.dat` says nothing about which measurement
it holds. Anything reading those files afterwards has to know the build order.

Reconstructing that order by re-parsing the script is guesswork that fails
quietly -- a curve labelled `ocv2` showing `ocv1`'s data looks entirely
reasonable. So the ordering is now recorded at fit time, from the object that
was actually fitted: `describe_models(problem)` walks `problem.models` and
writes `info.models: [{index, name, n_points}]` into the manifest. The
generator additionally passes `name=<slot key>` (`ocv1#0`, `tnr#12`) to every
`Experiment`, which also makes bumps' own plots self-describing.

A hand-written script may name nothing. The UI then labels by position and
says so, rather than inventing spec slot names that were never declared.

Corollary for any `<basename>-<n>` glob: sort on the parsed integer. Lexical
order puts model 10 between 1 and 2.

### 2026-08-05: the tNR heatmap must not pick its own reference

`series_data` computes the fractional residual with `tnr.run.load` plus
`tnr.reference.fractional_residuals` -- the same functions `nrw tnr assess`
uses. Choosing a reference block independently in the web layer would let the
picture and the numbers drift apart, so that the map shows change against one
baseline while `a(t)` beneath it is normalised against another.

Unmeasured cells are `NaN`, not 0. On a diverging colour scale zero-fill and
"did not change" are the same colour, and only one of them is a measurement.

### 2026-08-05: analysis warnings are data, not chatter

"101 interval file(s) listed in JSON not found" is a fact about the run that
the scientist needs. It was going to stderr, which for a web request means a
server log nobody reads. `tnr.notify.collect()` now redirects the channel on a
thread-local, so `ProjectData` returns those messages in the response and the
page shows them beside the plot they qualify.

### 2026-08-05: refl1d's profile grid is an integration grid, not a display grid

`-profile.dat` is written at refl1d's `dz` -- 0.1 Å here, giving 7,108 points
per profile and 149k points for a 21-model fit. That is one to two orders of
magnitude finer than anything physical in these models: the thinnest oxide is
tens of angstroms and the smallest interfacial roughness a few.

The reader thins to a 0.5 Å spacing, well below the roughness, keeping the
final point regardless of where the stride lands so the profile still reaches
the substrate. Measured on a real fit: worst-case interpolation error is
1.65e-4 of the SLD span, and the payload drops from 2.44 MB to 614 KB. Pass
`max_spacing=0` for the unthinned read; a numerical consumer should.

### 2026-08-05: `json.dumps` does not escape `</script>`

Data embedded in a `<script>` block is not protected by Jinja's autoescaping,
and the HTML tokeniser ends the block at the first literal `</script>` -- even
inside a JSON string. A run label or a fit note containing that text would
truncate the page's JavaScript and spill the remainder as markup. The template
filter escapes `</` to `<\/`, which is inert in JSON and invisible to the
tokeniser.

### 2026-08-05: `sample.md`'s commented example must not reach the rendered page

The scaffolded `sample.md` carries a worked measurement table inside an HTML
comment, listing runs (230594, 230597, 230600) that were never measured.
`web.prose.render` strips comments before rendering, for the same reason
`scan._runs_mentioned` does: showing that example beside the real data would
be actively misleading. Raw HTML is not rendered either -- a `sample.md`
travels between beamtimes and collaborators, and prose in a side panel has no
reason to run script.

### 2026-08-05: AuRE returns one flat feature mapping, and confidence is load-bearing

`extract_all_features` does not nest. The keys are `critical_edges`,
`oscillation_periods`, `n_fringes`, `estimated_total_thickness` +
`thickness_uncertainty` + `thickness_confidence`, `estimated_roughness`,
`estimated_n_layers`, `q_min`/`q_max`/`n_points`/`has_error_bars`. Guessing at
nested keys like `kiessig_fringes` yields empty results with no error, which is
how the first version of `aure_adapter` was wrong.

On the real 218386 curve the thickness comes back as **444 +/- 555 A** with
confidence `medium`. The uncertainty exceeds the value, so it constrains
nothing -- but printed alone it looks like a number you could seed a model
with. `Estimate.usable` encodes that test, and `nrw data features` says
"uncertainty exceeds the value; not a constraint" rather than leaving the
reader to notice.

### 2026-08-05: segment overlap independently found the reference script's special case

`nrw data overlap Sample6` measures run 218386's 1.2 deg and 3.5 deg segments
as disagreeing by **+27.58%, 14.8 sigma**, while every other pair in the sample
sits within 3 sigma.

The hand-written reference script has, at line 243:

    # The third run has a different intensity, so we don't share that
    # parameter with the first two runs.

So the original author found the same thing by eye and worked around it with a
free per-segment intensity. The check turns that into a number with an error
bar, before a model is written rather than after one misbehaves.

Where two angle settings overlap they measure the same sample, so a significant
ratio is a normalisation error. The fit will otherwise absorb it into a layer
thickness or an SLD, which is invisible on a log-R plot.

### 2026-08-05: interpolate reflectivity log-log, or the overlap ratio is biased

Comparing two segments means interpolating one onto the other's Q grid.
`np.interp` on R against Q biases the ratio **0.22% low** on a realistic
overlap: Fresnel decay goes as Q^-4, and a chord between two points of a convex
curve sits above it, so every interpolated value is slightly high. The bias is
one-directional, so it does not average away, and it is a fifth of the 1% scale
this check reports at.

Logging R alone leaves 0.043% -- `log R` is straight in `log Q`, not in `Q`.
Log-log is exact for a power law: measured residual 2e-14 on a synthetic
Fresnel curve, and 0.13% on one with strong fringes, which is honest since
fringes are not a power law.

### 2026-08-06: which direct beam normalised a segment is recorded only in the template XML

The reduced ASCII does not say. `REF_L_<run>_auto_template.xml` does:

    run 218386 <- direct beam 218274
    run 218387 <- direct beam 218275
    run 218388 <- direct beam 218338      <- measured much later

That is the 3.5 deg segment `nrw data overlap` reports 27.6% out at 14.8 sigma.
Different angles legitimately use different direct beams, so this is where to
look first rather than a diagnosis -- but two segments normalised against
references measured far apart are exactly the ones that come out on different
scales, and `nrw data overlap` now prints the mapping whenever it flags a pair.

Templates are named after the *first* run of a measurement, so looking one up
by segment 218388's own number finds nothing; every template in the directory
has to be read. Only one template exists in the apr2025 data (for 218386), so
218393's normalisation history cannot be checked the same way.

### 2026-08-06: the BL-4B detector moved, twice

Sample-to-detector was 1830 mm until 2024-08-26, then 1355 mm, then back to
1830 mm on 2025-01-01. Source-to-detector went 15750 -> 15282 -> 15750 mm.
A run reduced with the wrong distance is wrong in a way that looks like a real
sample. `instrument/geometry.py` holds the dated table and `spans_a_change`
answers the question it exists for: may these runs be co-refined?

The apr2025 data this project is built around sits after the move back, so it
uses 1830/15750.

### 2026-08-06: nr-analyzer's theta-offset physics is untested upstream

`tests/test_theta_offset_json.py` writes an empty file and mocks
`compute_theta_offset` entirely. So the 613 lines of NeXus event loading, peak
fitting and gravity correction have no test anywhere.

Only the dated geometry table was ported here. Vendoring the rest would mean
shipping unverifiable physics into a package whose whole claim is that results
are checkable -- and there is no raw event data in a workbench project to test
it against, since `data/raw/` is gitignored by design.

### 2026-08-06: a default argument bound at import cannot be overridden

`sync_upstream.check(manifest_path: Path = MANIFEST)` looked fine and was
untestable: the default is evaluated once when the module is imported, so
monkeypatching `sync_upstream.MANIFEST` changed nothing and a caller passing an
override to `load_manifest` was silently ignored. Both now take `None` and
resolve inside the function. Caught by a test that asserted `main()` exits
non-zero on drift and got 0.

### 2026-08-06: upstream.toml entries without a commit were not actually tracked

Six of eight `[[adapted]]` entries recorded a repo and a path but no commit, so
there was nothing to compare against and the drift check could only skip them.
The manifest looked like it was doing its job. Commits are backfilled from the
checked-out source repos, and `test_every_manifest_entry_names_a_commit`
now fails if a new entry omits one.

The manifest also mixes key spellings -- one entry has several `paths` from a
single `repo`, another has parallel `repos`/`paths`/`commits` -- so anything
reading it has to handle both.

### 2026-08-06: a refl1d script is identified by its last line, not its first

`problem = FitProblem(...)` is the closing line by convention -- in the oct2025
reference it is line 344 of 344 -- and these scripts import via
`from refl1d.names import *`, so the name never appears near the top. The
importer's first version sniffed the leading 4 KB, found the imports, missed
the identifying token, and left every loose model script unclassified.

### 2026-08-06: `\b` finds no boundary between `r` and a digit

`re.search(r"\b(\d{6})\b", "r223995_eis_reduction.json")` matches nothing: `r`
and `2` are both word characters, so there is no boundary between them. The
pattern silently returned None and sent every tNR file into one flat directory
instead of one per run. Now `(?:^|[^0-9])r?(\d{6})(?![0-9])`.

### 2026-08-06: resolve both sides before asking whether one path contains another

`_link_target` resolved the source but compared it against an unresolved root.
On macOS `/tmp` resolves to `/private/tmp` and `/home` goes through autofs, so
a source genuinely inside the project was judged foreign and silently linked
with an absolute path. Any symlinked component on either side does this.

### 2026-08-06: old fit outputs must not be imported

`nrw import` recognises `.dat`, `.par`, `.err`, `.mc.gz` and `results/` and
deliberately leaves them behind -- 310 files in apr2025, 527 in june2026.

A `results/<fit_id>/` directory here means a manifest with the hash of every
input, the resolved environment, the git SHA and the exact command. A `.dat`
dumped by a bumps run in 2025 has none of that. Putting it where `nrw whence`
promises an answer would fake provenance nobody recorded. Re-running the script
through `nrw fit run` takes minutes and produces the real thing.

The distinction from "not recognised" is reported separately for the same
reason: "I know what this is and it should not come" is different information
from "I do not know what this is".

### 2026-08-06: two planned skills were not written, on purpose

The M6 skill list included `tnr-plotting` and `partial-data-assessment`.
Neither was written, and the reasons are worth keeping so nobody adds them
later to tick the box.

**`tnr-plotting`** was to be adapted from `experiments-2025/docs/tNR-plotting.md`
(422 lines). Read closely, that file is a *development plan* -- Phase 0 through
Phase 7, a "Files touched" section, a "Decisions taken" section about extending
`tnr_chi2.py`. Its one durable section, "Numerical pitfalls", lists guards that
are already implemented in `tnr/metrics/` and pinned by the characterization
tests: the R <= 0 guard in `fractional_residuals`, the floor on
`dR_i^2 - dR_ref^2`, the boxcar edge handling, `--variogram-min-pairs 3`. Those
are tested code, not guidance a reader acts on.

**`partial-data-assessment`** would have covered what you can conclude from
per-angle `_partial.txt` files: segment consistency and the Q-range limit on
what is resolvable. Both are already covered -- consistency in
`refl-bl4b-instrument` (with `nrw data overlap` behind it), resolvability in
`thin-layer-degeneracy` (the 2*pi/Q_max rule). A third telling would compete
with those rather than add to them.

Twelve skills ship and are seeded by `nrw init`. Four more exist in the bundle
for `nrw skills sync`. A skill nobody needed is worse than a missing one: the
retriever scores against every skill's tags, so filler dilutes the ones that
matter.

### 2026-08-06: the copper-oxide SLDs in circulation are wrong, and CuO hides in Cu

Three sources disagreed on the copper oxides. Computed from CRC bulk densities
and coherent scattering lengths (`periodictable`):

    Cu        6.55      CuO   6.46      Cu2O  5.36      Cu(OH)2  2.46

AuRE's skill table gives CuO 5.0 and Cu2O 4.0. The first draft of this repo's
`metal-oxide-interfaces` skill gave CuO 4.2-4.5 and Cu2O 4.9-5.2 -- both wrong
*and inverted relative to each other*. The same first-principles calculation
reproduces AuRE's TiO2 (2.63 vs 2.6) and SiO2 (3.47 vs 3.47) exactly, so the
method is sound and the copper entries specifically are the bad ones.

The physical consequence is the reason this matters: **stoichiometric CuO at
6.46 is nearly contrast-matched to copper at 6.55.** A dense fully-oxidised CuO
layer on a copper electrode is close to invisible to neutrons. Looking for one
and finding nothing is not evidence it is absent.

Cu2O is the visible one at 5.36, and a real native oxide is porous and hydrated
rather than bulk-dense -- Cu2O at 80% of bulk density is 4.29 -- so a fitted
CuOx in **4.2-5.5** is an ordinary cuprous oxide and the width of that range is
porosity.

Also: `aure_adapter.sld("Cu2O")` raises. AuRE's density table covers elements
and common compounds but not these oxides, so a density must be passed
explicitly. The skill's example says so now; the first version claimed it
worked and did not.

### 2026-08-06: the incident angle is in the file header, in radians

Every reduced steady-state REF_L file carries a `# Meta:` line holding one JSON
object with `theta` (radians), `norm_run`, `dq_over_q`, `sequence_number`,
`scaling_factors` and the timestamps. `nrw model new` was instead writing
`[0.45, 1.2, 3.5][:n]` -- the group's usual settings, truncated to the segment
count.

For a three-segment measurement at the usual settings that is right to 0.08%,
which is why it went unnoticed. It is badly wrong otherwise:

* Run 218389 has **one** segment in `data/steady` -- the summed tNR dataset --
  so it got `[0.45]`. The header says **0.5997**. That is 25% off, and theta
  enters both `wl = 4*pi*sin(theta)/q` and `dT = dq/q*tan(theta)`, so the
  wavelength axis and the resolution were both wrong by 25%.
* Any two-segment measurement got `[0.45, 1.2]` regardless of its real angles.

A wrong theta does not raise. It broadens or sharpens every fringe and the fit
absorbs it into roughness.

Measured values, for the record: 218386 is 0.4500 / 1.2010 / 3.5003 and 218393
is 0.4499 / 1.2009 / 3.5002. The fixed-width `TwoTheta(deg)` column agrees with
the JSON `theta` to seven digits, which is a free check on the radians
conversion.

**This is a parsing problem, not an inference problem.** An LLM call was
considered and rejected: the value is a field in a JSON object, so a parser is
exact, offline, deterministic and testable, while a model can return a
plausible number that silently corrupts the resolution. Where a model helps is
reading an *unfamiliar* header once and writing a parser for it -- a
code-generation task with a test, not a per-file inference.

### 2026-08-06: a tNR run is also reduced whole into data/steady

Time-resolved slices carry no header at all, and the angle appears in neither
the `*_eis_reduction.json` sidecar nor the tNR template XML. It looked like the
angle simply was not recorded.

It is: the same run is *also* reduced as a summed dataset into `data/steady`
under the same run number -- `REFL_218389_4_218389_partial.txt` -- and that file
has the full `# Meta:` block. `theta_for_run` reads it.

Two consequences beyond the angle:

* That summed file must **not** be co-refined alongside the series. It is the
  sum of the very slices the series contributes, so including both puts the
  same neutrons into the fit twice and roughly doubles that run's weight.
  `nrw model new` now drops the state and says why.
* `sequence_number: 4` in the header matches `scan_index: 4` in
  `reduction_options.json`, so the two records agree on which angle setting the
  tNR run used.

### 2026-08-06: filling the spec from notes, and the boundary that makes it safe

`nrw model new` fills in facts -- runs, files, measured angles. It cannot fill
in the stack, which is in the scientist's head and, if written down, in
`sample.md`. Two paths close that:

* `--from-notes` calls a configured endpoint.
* `--print-prompt` writes the skeleton and prints the same request for the
  coding assistant already open on the repository.

Both build the same prompt, in `spec/authoring.py`, so they cannot drift.

**A model may propose only `description`, `materials`, `stack`, `parameters`
and `constraints`.** Anything it returns for `states`, `series`, `thetas`,
`data_dir` or `run` is dropped in `merge_proposal`. The filter is structural,
not a request in the prompt, because those are exact measurements and a
plausible wrong angle is unrecoverable -- it broadens every fringe and the fit
absorbs it into roughness without raising.

Two things that only showed up on a real run:

* A proposal that replaces the stack leaves the scaffold's constraint pointing
  at `Film.thickness`, a layer that no longer exists, and the spec then fails
  its own `nrw model validate`. `_repair_constraints` keeps the paths that
  survive and drops the constraint when none do.
* The critical edge from `nrw data features` is worth putting in the prompt.
  A real run came back reasoning that "the measured topmost SLD near 4.3 [is]
  consistent with a porous/native cuprous oxide" -- which is the 4.2-5.5 range
  derived from first principles in `metal-oxide-interfaces`.

### 2026-08-06: a reduced-data scaffold that builds to infinity

`nrw model new` scaffolds a series with no `select:`, so it takes every slice.
14 of run 218389's 130 slices contain points with `dR = 0`, chi-squared divides
by `dR`, and the whole problem returns `inf`. refl1d does not warn.

The guard belongs in the generated `create_probe`, not in the scaffold: it
fixes every spec rather than only scaffolded ones, and it matches what the rest
of the package already does -- `fractional_residuals` has had the same check
since M2. Points with non-positive or non-finite `dR` are dropped at load.

Verified not to change anything it should not: the M3 numerical gate still
passes, and the guide's model builds to the same chi-squared 101.632 over the
same 5380 points, because its `select: {labels: ["*_eis_*"]}` never included a
bad slice.

### 2026-08-06: .env, and why the scaffold must gitignore it

Settings load shell > project `.env` > `~/.nrw` > `~/.aure`, mirroring AuRE.
The `~/.aure` fallback is deliberate -- a machine already configured for AuRE
works here unchanged -- and is reported by `nrw doctor` rather than being
silent, because configuration arriving from a file you did not know was read is
hard to debug.

AuRE's `load_env()` is called by AuRE's own CLI, so importing `aure.llm.config`
from here sees only the shell. Without loading it ourselves, a user with a
working `.env` would find `nrw` says "no endpoint" while `aure` works.

The scaffolded `.gitignore` did **not** cover `.env`. A beamtime directory gets
shared with collaborators, archived by the facility, and sometimes published
alongside a paper, so an API key in one is a real exposure. It is ignored now,
a `.env.example` ships, and a test asserts both.

Loading is lazy: `python-dotenv` costs ~40 ms and `nrw --help` is run
constantly. A test asserts `dotenv` is not in `sys.modules` after `--help`.

### 2026-08-06: the model explanation is derived, never authored

`nrw model generate` writes `<name>.md` beside `<name>.py`: what is being
fitted, what is held equal to what, what each instrument parameter absorbs, and
every assumption the fit makes silently.

Every sentence comes from the resolved `ParameterTable`, so it cannot describe
a model other than the one that will run. A hand-written explanation drifts the
first time someone edits one and not the other, and a stale explanation is
worse than none because it reads as authoritative. `nrw check` reports
`stale-explanation` and `missing-explanation` against the recorded spec hash.

It is suppressed when the *script* is already stale -- one message about the
spec having moved on is enough.

### 2026-08-06: theta_offset and sample_broadening are partials-only

Both describe the *incident angle*. A combined file is the reduction's stitch
of every angle setting, so it has no single angle to offset or broaden. refl1d
accepts the parameter and fits it to something meaningless, which is the worst
of the three possible behaviours -- so `nrw model validate` rejects them on a
`kind: combined` state. AuRE draws the same line via its `_NUISANCE_KEYS`.

Canonical ranges, from AuRE's `_TRIPLET_DEFAULTS`:

    theta_offset       [-0.02, 0.02]
    sample_broadening  [ 0.0,  0.05]
    background         [ 0.0,  1e-5]

Declaring `sample_broadening` at all changes which resolution path refl1d
takes: the guide's model builds to chi-squared 101.632 without it and 101.689
with it pinned at 0.0. So a spec carrying it at zero is not equivalent to one
without it -- add it when there is a reason, not by default.

`sample.md` gained a **Measurement conditions** section for describing these in
words ("sample bowed slightly after mounting"), and both authoring paths read
it: the prompt maps alignment/curvature/background language onto the matching
parameter.

### 2026-08-06: an f-string return value will eat a YAML example

`agent_instructions` returns an f-string, and the instruction text contains
`{path: probe.theta_offset, ...}` as an example. Python read that as a format
field and `--print-prompt` raised `NameError: path`. Ruff's F821 caught it
before a user did. Braces in an f-string template must be doubled -- worth
remembering for any function that returns example code.

### 2026-08-06: the assessment already chooses the constraint form; use it

`nrw tnr assess` ends with a verdict that names the form the trajectory calls
for -- "a(t) is monotonic, so a linear-in-time constraint fits; the template
oscillates in Q, so free a thickness" -- and the authoring prompt was ignoring
it entirely, leaving the model to guess from prose.

`_measured_facts` now quotes the verdict, the trajectory shape, the peak
significance and the template classification. Running `nrw tnr assess` before
`nrw model new --from-notes` is therefore the whole answer to "how do I ask for
a linear constraint": the data already said so.

### 2026-08-06: a proposal will describe a constraint instead of returning one

A real reply put this in its `notes` field:

    the existing linear_in_time constraint should be moved from
    Film.thickness to CuOx.thickness

and then omitted the `constraints` block. The leftover constraint referenced a
layer the new stack does not have, `_repair_constraints` dropped it, and the
series ended up with every slice refitting the whole structure independently --
never what was wanted, and invisible in the spec.

The paths are derivable, so the fallback does not need the model: a parameter
declared `per: state` across both endpoint states is by definition something
the experiment changed between them, so interpolating it across the series is
exactly what the form is for. `_rebuild_constraints` re-aims the constraint at
those, excluding `per: model` values (identical at both ends, so interpolation
is a no-op) and `probe.*` nuisances.

The prompt was also strengthened to say RETURN the block rather than describe
it, which fixed it on the next real run -- but the deterministic fallback stays,
because a prompt is a request and this is a correctness property.

### 2026-08-06: the explanation states the arithmetic, not a paraphrase

`<name>.md` now carries the formula the generated script actually evaluates:

    p_i  =  p[ocv1] + (p[ocv2] - p[ocv1]) * f_i
    f_i  =  (t_i - t_0) / (t_last - t_0)

plus the per-slice fraction table, the free parameters the form introduces
(`tau`, `t_half`/`width`, knots) or a statement that it introduces none, and
the arithmetic: "90 slice values (6 paths x 15 slices) computed from no new
free parameters".

It also quantifies time-vs-index for the actual data rather than asserting they
differ. On run 218389's full 130 slices the worst disagreement is slice 17 at
0.120 against 0.132 -- 1.2% along the trajectory. Small here, and the reader
can see it is small instead of taking it on trust.

### 2026-08-06: constraint endpoints can be fitted, not only anchored

`from`/`to` normally name the steady states either side of a series, and that
is why the interpolating forms cost nothing -- they borrow parameters the
steady-state data already constrains. Writing `free` for either fits that
endpoint instead: one extra parameter per path per free endpoint.

    from: ocv1  to: ocv2     21 free   both anchored
    from: ocv1  to: free     27 free   +6, one per path
    from: free  to: free     33 free   +12

Three situations call for it, all real: a series with no bracketing
measurement; a series where something happened between the steady measurement
and the run, so anchoring asserts a continuity that is not there; and a series
where where the sample finished *is* the result rather than an assumption.

The range is borrowed from the path's existing `parameters` declaration -- a Cu
thickness plausible for the steady states is plausible during the series, and
repeating it would be a second place for it to be wrong. `endpoint_range`
supplies one where no declaration exists. Missing both is an error, not a
default: an unbounded endpoint drags the whole trajectory with it.

The explanation had to learn about this. Left alone it rendered `p[free]` in
the formula and claimed "No new free parameters" while six had just been
created -- the plausible-looking wrong document that is worse than none.

### 2026-08-06: sample.yaml was written and never read

`nrw sample scan` wrote the register; nothing consumed it. `nrw model new`
re-scanned the disk instead, so curating the register -- deleting the alignment
scans, the aborted runs, the other conditions -- did nothing at all. A
write-only register is decoration.

`load_register` reads it back, and `nrw model new` prefers it. The drift
between register and disk is reported, not resolved: a stale register and a
deliberately curated one are indistinguishable on disk, and silently re-adding
a run would undo a choice.

Two traps in doing this:

* The scaffold ships `steady: []` and `series: []`. Treating an empty register
  as a curated empty set would make `nrw model new` report "no data found" for
  every sample whose owner copied files in before running `nrw sample scan`.
  An empty register is a stub; fall back to scanning.
* `yaml.YAMLError` derives from `Exception`, not `ValueError`, so
  `except (OSError, ValueError)` sails straight past a malformed register.

### 2026-08-06: theta_offset scope is a claim about remounting, not about change

The skill said to fit `theta_offset` and `sample_broadening` `per: state`,
reasoning that "a realignment between two states is exactly the kind of thing
that makes them differ". That is backwards as a default here.

Both describe *how the sample sits in the beam* -- its alignment and its
flatness -- so the question is not "did the sample change?" but "was it
remounted?". An in-situ electrochemical cell measured continuously (OCV, tNR,
OCV) is mounted once and never touched: one alignment for the whole experiment,
so `per: model`.

Fitting one per state on a sample that never moved is several free parameters
describing one physical quantity, and they will absorb the real structural
differences between the states -- the thing being measured.

`probe.intensity` is the exception and is nearly always `per: state`: each
reduction used its own direct beam, and those genuinely differ.

`sample.md` now asks the deciding question outright, because it is not
derivable from the data.

### 2026-08-06: an error message that names only the wrong fixes

The double-assignment error said "remove it from `parameters` or from the
constraint's `paths`". On the case that actually produces it, both are wrong:
removing the declaration loses the range -- which a `free` endpoint borrows --
and removing it from the constraint leaves the series unfitted.

The real cause is nearly always a structural parameter declared `per: state`
with no `in:`, which scopes it to *every* group including the series. The fix
is to scope it to the steady states, and the message now says exactly that,
with the state names filled in.

It also reports **every** collision rather than the first. The mistake is made
once, in one habit, and applies to every structural parameter in the spec -- on
the real five-layer model that was eight of them, so raising on the first
turned one edit into eight validate-fix cycles.

### 2026-08-06: the critical edge in back reflection needs the substrate added

AuRE reports a critical edge as the SLD it would imply for a beam arriving from
vacuum. In back reflection the beam arrives through the substrate, so the
contrast is `rho_medium - rho_substrate` and the medium above it sits at
`estimate + rho_substrate`.

On expt11 that is the difference between a nonsense answer and the right one:

    raw estimate      4.28      not any solvent
    + rho_Si (2.074)  6.354     d8-THF, tabulated 6.35

`sample.md` said only "THF", which would be 0.18. The corrected edge said
deuterated, and it was right -- the notes were wrong. Where the two disagree
about deuteration, the edge is the measurement.

Uncorrected, this was actively harmful: the prompt reported "implies a topmost
SLD near 4.28" and a proposal duly set the solvent range to [4.0, 4.7], which
is neither isotope. `_back_reflection_substrate` reads the geometry from the
notes -- it runs while building the prompt, before anything has been proposed
-- and reports both numbers.

### 2026-08-06: repairs the model will not reliably do itself

`nrw model new --from-notes` scored 8/11 on a real, detailed `sample.md`, and
the three misses were all instructions the prompt had just gained. Two turned
out to be a silently failed edit: the `probe` and `series_select` output keys
never reached the prompt, so the model was told to use them in the rules and
never told they were valid output. **String-replacement edits to a prompt need
an assertion**; a prompt that quietly loses an instruction looks exactly like a
model that ignored one.

Three more repairs moved into `merge_proposal`, because each is a correctness
property rather than a request:

* `per: state` with no `in:` covers the series and collides with the
  constraint. Asked for in the prompt, omitted about half the time.
* `per: model` on a path that is also constrained is a contradiction --
  "pinned everywhere" against "has a trajectory". The declaration wins.
* A constrained path with a `free` endpoint and no declared range cannot be
  bounded. Dropped and named, because inventing a range for an SLD is the kind
  of plausible guess this package exists to avoid.

With those, the same notes produce a spec that validates and builds first try.
The remaining prompt-only instructions are the ones where a wrong answer is
visible in the spec rather than fatal to it.

### 2026-08-06: bumps runs on one CPU unless told otherwise

`bumps.fitters.fit` takes `parallel: int = 1`, and nothing here was passing it,
so every fit ran on a single core regardless of the machine. `parallel=0` means
all cores; `nrw fit run` now defaults to that and exposes `--parallel N`.

Measured on the 21-experiment Cu/THF co-refinement, dream with 8000 samples on
a 20-core laptop: **169 s serial, 30 s parallel** at 879% CPU. Population
fitters evaluate their whole population per generation and scale; amoeba is
sequential and gains nothing.

`parallel` is in `FIT_SETTING_KEYS`, so it is part of the settings digest and
appears in the record -- it changes how the fit ran.

Two things to know about the pool:

* bumps starts a `multiprocessing.Manager` and spawns workers that re-import
  `__main__`. A benchmark run from a heredoc died on exactly this
  (`FileNotFoundError: .../<stdin>`); the real CLI is fine. Since the failure
  is environmental and the model is not at fault, a pool failure falls back to
  serial with a warning rather than losing the fit.
* The fallback is deliberately narrow: it triggers on multiprocessing-shaped
  errors only, so a genuinely broken model still fails once and fast instead of
  being retried pointlessly.

### 2026-08-06: the fit record was not freezing the spec

`freeze_script` copied `model.py` and nothing else, so a result directory held
generated Python and no statement of intent -- which layers are tied, what the
constraint asserts, which endpoints are anchored. It could be re-run but not
re-reasoned about, and anything reading the model back had to reconstruct it
from generated code. `spec.yaml` and `model.md` are frozen alongside now. A
hand-written script has neither, which is normal rather than an error.

### 2026-08-06: `index` in bumps' -err.json is the chain column already

`point.mc.gz` is `(n_samples, 1 + n_parameters)` with logp in column 0, and
each parameter's `index` in `-err.json` runs 1..n -- it is the column, not an
offset into the parameter block. Adding one shifts every parameter onto its
neighbour.

The result is the dangerous kind of wrong: nothing raises, no value looks
impossible in isolation, and every band is a real interval from a real
parameter. It showed up as a copper *roughness* of 5 A carrying a
[500, 530] band, because it had been handed the thickness column.

Caught by asserting each fitted value sits on the same scale as its own band.
That check needs a tolerance of one band width rather than zero: the reported
value is the maximum-likelihood point, which is not obliged to lie inside a
*central* 68% interval and routinely sits just outside it when a parameter
rails against a bound.

### 2026-08-06: a trajectory band needs paired posterior samples

A constrained slice value is a deterministic function of the fitted endpoints,
so the band is computed by evaluating the constraint once per posterior sample.
Propagating each endpoint's `std` independently would be wrong in a specific,
visible way: for an interpolating form the endpoints are strongly
anti-correlated, so the band should *narrow* towards the middle of the series
and independent propagation widens it there instead.

Values come from `-slabs.dat` rather than the same evaluation -- that is the
layer table the fit actually used for each slice, so it needs no recomputation
and works for an optimiser run with no posterior at all.

### 2026-08-06: an old fit does not need re-running to describe itself

The trajectory view needs the spec, and fits made before `spec.yaml` was frozen
into the record do not carry one. Re-running is not the answer: the generated
script records the digest of the spec it came from, so a spec still on disk
that hashes to it is provably the same file and using it is exact.

If the spec has since been edited, it describes a *different* model, and
plotting its trajectory against this fit's numbers would be worse than plotting
nothing. That case reports the reason and stops -- and `nrw check` is already
calling the fit stale independently.

Both branches are visible in the UI. The failure that prompted this was not a
wrong plot, it was **no plot and no explanation**: an empty page is
indistinguishable from a broken one.
