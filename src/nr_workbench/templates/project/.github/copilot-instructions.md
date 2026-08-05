# AI Assistant Instructions

This is an **nr-workbench** project: neutron reflectometry analysis for SNS
REF_L (BL-4B). These instructions are shared by GitHub Copilot and Claude Code
(which imports this file from `CLAUDE.md`).

## Core principles

1. **Assess before acting** — read the sample context and existing models before
   proposing anything.
2. **Give an itemized plan** — numbered steps, each independently checkable.
3. **Work incrementally** — one step at a time, say which one you are on.
4. **Verify** — run the check, show the output, fix failures before moving on.
5. **Record ground truths** — append findings to `docs/ground_truths.md`.

## Standard workflow

**assess → plan → implement → verify → record.**

For an analysis task that means: read `sample.md` and the measurement register
→ assess the data (`nrw tnr assess` for a time series) → propose a model → write
the spec → generate, preview, fit → check the result → write it down.

## Read the skills

`skills/reflectometry/` holds the domain knowledge for this beamline. Three are
effectively always relevant:

- **`nr-workbench-project`** — the project map and the provenance rules. Read
  this before touching files.
- **`neutron-reflectometry`** — SLD values and bounds, probe construction, χ²
  and BIC interpretation, roughness rules, the refl1d API traps.
- **`tnr-change-assessment`** — the reading order for time-resolved data.

Each has a dispatcher agent of the same name in `.github/agents/`, so you can
invoke it directly. To change a standard, edit its `SKILL.md`.

## Provenance rules

The reason this project exists is that a previous folder of ad-hoc scripts made
it impossible to say which one produced a published figure. So:

- **Never hand-edit a generated `models/*.py`.** Edit the `.yaml` spec and
  regenerate, or `nrw model fork` to take ownership with provenance intact.
- **Never overwrite a result.** Fits write new immutable directories.
- **No absolute paths** in anything committed.
- **No boolean toggles** inside a fit script — a variant is a separate spec.
- Run **`nrw check`** before calling a result final.

## Data conventions

- Reduced files are 4 columns: `Q, R, dR, dQ`. **`dQ` is FWHM, not sigma.**
- `REFL_{run}_combined_data_auto.txt` merges all angle segments; the per-segment
  angle is not recoverable from it.
- `REFL_{run}_{seg}_{subrun}_partial.txt` is one segment. Standard thetas are
  `[0.45, 1.2, 3.5]` degrees; tNR is usually a single angle at 0.6°.
- tNR slices are `r{run}_t{seconds:06d}.txt` with a sibling reduction JSON that
  carries the interval structure.

## Code style

Type hints on parameters and returns, Google-style docstrings on public
functions, specific exceptions with useful messages, and comments that explain
*why*. Analysis scripts must resolve paths from the project root, never from an
absolute location.

## Educational approach

The audience includes scientists who are not software engineers. Explain *why*,
not just *what*, in plain language.
