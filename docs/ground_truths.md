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

### 2026-08-05: A NUL byte is the binary test, not a failed decode

`_render_diff` originally detected binary content with `try: decode('utf-8')`.
That is insufficient: control bytes including NUL are valid UTF-8 code points,
so `b"\x00\x01\x02"` decodes cleanly and got fed into a text diff. Now
`_is_binary` checks for a NUL byte first, which is what git does.
