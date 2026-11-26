# Fitting options

A project fits in two ways, and each is set up in its own place.

## nrw's fits

**nrw's fits** are the ones recorded in the fit index and shown under Fits. They
come from `nrw fit run`, or from **Fit…** on the Experiment page, which runs
`nrw fit run`. Each setting comes from the first of these that sets it:

1. the command line (`nrw fit run model.py --method amoeba --steps 500`), or
   the page's Fit form;
2. with `nrw fit run --from <fit_id>`, which runs a recorded fit again from
   its own problem file, the fitter, settings and seed that fit ran with;
3. the project's `nrw.toml`, under `[fit]`;
4. bumps' own default for that fitter.

When nothing names a fitter, it is **DREAM**: it samples the posterior, so its
answer comes with uncertainties. In `nrw.toml`, `[fit]` names the fitter and
holds what every fitter takes. Each fitter's own settings go in its own table,
and apply only when that fitter runs:

```toml
[fit]
method = "dream"    # dream | de | amoeba
seed = 12345        # a fixed seed makes a fit repeatable; none by default
parallel = 0        # CPUs to use: 0 means all of them

[fit.dream]
samples = 20000     # bumps' default: 10000
burn = 1000         # bumps' default: 100

[fit.de]
steps = 2000        # bumps' default: 1000

[fit.amoeba]
steps = 1000        # bumps' default: 1000
```

With these settings:

- `nrw fit run model.py` runs DREAM with 20000 samples and 1000 burn-in steps.
- `nrw fit run model.py --method amoeba` runs amoeba for 1000 steps.
- A setting a fitter does not take, such as `samples` for amoeba, is refused
  rather than ignored.

Each fit prints which settings it took from `nrw.toml`. Its record keeps every
setting it ran with, so it can be reproduced after `nrw.toml` changes. A fit an
unattended session starts can also be held to ceilings, `[agent.limits.<fitter>]`:
see [agent.md](agent.md#compute-during-a-beamtime), "Compute, during a beamtime".
`nrw init` writes these tables into a new project's `nrw.toml` commented out,
holding bumps' own defaults (with `pop` for DREAM and DE), so uncommenting one
changes nothing until you edit a value. For an older project, paste them in.

A model spec's `fit:` block is **not** read, and `nrw model validate` says so.
Put fit settings in `nrw.toml` or on the command line.

## AuRE's fits

**AuRE's fits** are reconnaissance: they happen inside `nrw aure run`, and in
the page's **Quick fit with AuRE**. AuRE sets them up itself, from these
sources:

- **`--budget quick`**, the default: nrw writes DE, 300 steps and one
  refinement into the run's `setup.yaml`, and that wins over everything else.
- **`--budget standard`**: nrw leaves the fit to AuRE. AuRE reads `FIT_METHOD`,
  `FIT_STEPS` and `FIT_BURN` from its environment, else uses its defaults
  (DREAM, 1000 steps, 1000 burn-in). For a run nrw starts, that environment is
  the first of: the shell, the project's `.env`, `~/.nrw`, `~/.aure`.
- **AuRE's physics knobs** (`MODE_ENUMERATION` and the others): nrw sets every
  one for each run, over the shell's too, and records them in `run-env.json`
  beside the setup. A value for one of them in `~/.aure` is not used by a run
  that nrw starts.
- **The fit you keep**: after a quick fit on the page, the spec AuRE proposed is
  fitted with `nrw fit run`, starting from AuRE's values, with the project's
  fitter: `[fit]` in `nrw.toml`, else DREAM, so it carries uncertainties. That
  fit is the one that appears under Fits.
- **Again**: **Quick fit again** on a model AuRE proposed asks AuRE again, for
  example after you have added to `sample.md`. What AuRE reads there includes
  the run's condition and notes. A spec you have edited is never replaced.

`nrw.toml` says nothing about AuRE's fits, and `~/.aure` says nothing about
nrw's. [settings.md](settings.md) lists which file holds what.
