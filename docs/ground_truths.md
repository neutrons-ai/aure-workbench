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

### 2026-08-05: AuRE ships no SKILL.md files in its wheel (fixed upstream)

AuRE's `[tool.setuptools.package-data]` covered only `"aure.web"`, so none of
its eight `SKILL.md` files reached the wheel or sdist. Invisible under
`pip install -e .`, but `Dockerfile:8` installs non-editable — so
`ghcr.io/neutrons-ai/aure` ran with an empty skill registry, injecting no
domain knowledge into any prompt, with no error and no log line.
`SkillRegistry._scan` only warned when the *directory* was missing, which it
never was.

Fixed in `neutrons-ai/aure` branch `fix/package-skills-data`. **Until that
merges, nr-workbench cannot read skills from an installed aure** and ships its
own adapted copies instead.

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

### 2026-08-05: A NUL byte is the binary test, not a failed decode

`_render_diff` originally detected binary content with `try: decode('utf-8')`.
That is insufficient: control bytes including NUL are valid UTF-8 code points,
so `b"\x00\x01\x02"` decodes cleanly and got fed into a text diff. Now
`_is_binary` checks for a NUL byte first, which is what git does.
