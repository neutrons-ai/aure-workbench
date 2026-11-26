# AuRE Workbench
A project workbench for neutron reflectometry analysis at the SNS Liquids
Reflectometer (REF_L / BL-4B).

This project is a companion to the AuRE - Automated Reflectivity Evaluator repository.

`nr-workbench init` scaffolds an analysis project: one directory layout for
every sample, a curated `skills/` folder that Claude Code, GitHub Copilot and
OpenCode all read, and a provenance ledger that keeps every result linked to the
script, data, and environment that produced it.

## Quick start: join an experiment

Five steps bring an experiment that is shared on Tiled to your own machine: its
runs, its samples and its fits.

1. **Install nrw.** On macOS and Linux:

   ```bash
   curl -fsSL https://raw.githubusercontent.com/neutrons-ai/aure-workbench/main/install.sh | sh
   ```

   On Windows, in PowerShell:

   ```powershell
   irm https://raw.githubusercontent.com/neutrons-ai/aure-workbench/main/install.ps1 | iex
   ```

   It needs `git`, and brings its own Python. Open a new terminal if `nrw` is
   not found afterwards. [docs/install.md](docs/install.md) explains what the
   script does, and its options.

2. **Log in to Tiled.** It prints an address and a code to enter in your
   browser:

   ```bash
   nrw tiled login
   ```

3. **Pull the experiment**, by its IPTS number:

   ```bash
   nrw tiled pull --ipts IPTS-34567
   ```

   That makes the project in a new folder, `IPTS-34567/`, holding the
   experiment's samples, its reduced data and its fits.

4. **Look at it in your browser:**

   ```bash
   cd IPTS-34567
   nrw serve
   ```

   Open <http://127.0.0.1:8765/experiment>. `Ctrl-C` in the terminal stops the
   server.

5. **Open the folder in VS Code** when you want to work on the analysis with
   Claude Code or GitHub Copilot: **File → Open Folder…**, or from the project
   folder:

   ```bash
   code .
   ```

To get what others have pushed since, run `nrw tiled pull` again inside the
project; it needs no `--ipts` there.

## Starting a project of your own

```bash
mkdir my-beamtime && cd my-beamtime
nrw init --beamtime june2026 --ipts IPTS-34567
nrw doctor
nrw sample new Sample4 --title "ionomer on copper"
```

Then copy reduced data into `samples/Sample4/data/steady/` and
`samples/Sample4/data/tnr/`, open the folder in VS Code, and work with Claude
Code. `nrw init` is idempotent and safe to run on top of an existing beamtime
folder — it never overwrites a file you have edited.

Run `nrw --help` for the full command surface.

## Why

The previous way of working was a directory per beamtime, each with its own
layout, and fitting scripts named `…-corefine-tNR-test.py`, `…-t0.py`,
`… copy.py`. It became impossible to say which script produced a published
figure. Writing a time-resolved co-refinement by hand made it worse: the
reference model is 341 lines, ~200 of them copy-pasted parameter tying, with
absolute paths belonging to a different user baked in.

nr-workbench replaces that with:

- **One layout.** `samples/<id>/` for everything; beamtime is metadata.
- **Immutable, attributed results.** Every fit writes a new directory recording
  the spec, the script, every input hash, and the environment.
- **A declarative model spec.** Steady-state co-refinement *and* time-resolved
  series with functional constraints in one schema, generating a readable,
  standalone refl1d script.
- **Skills the agent actually reads**, in the tool-neutral repo-root layout.

## Documentation

| Guide | What it covers |
|---|---|
| [Install](docs/install.md) | What the installer does, its options, upgrading, uninstalling, and installing by hand |
| [Getting started](docs/getting-started.md) | One analysis end to end on real data, from an empty folder to a promoted fit |
| [The Experiment page](docs/experiment.md) | Organizing a beamtime's runs into samples, then fitting and curating fits in `nrw serve` |
| [Sharing through Tiled](docs/experiment-sources.md#tiled-pushed-and-pulled-by-a-person) | `nrw tiled push`, `status` and `pull` |
| [Your first fit](docs/first-fit.md) | A first model from AuRE, when you have data and no layer stack yet |
| [Fitting options](docs/fitting.md) | Choosing a fitter and its settings, for nrw's fits and for AuRE's |
| [Settings](docs/settings.md) | Which file holds which setting, and what is sent to a language model |
| [Provenance](docs/provenance.md) | What each fit records, tracing a figure to its fit, and packing a fit for a collaborator |
| [Running unattended](docs/getting-started-with-agent.md) | Letting an assistant analyse measurements as they arrive |
| [The unattended agent, in full](docs/agent.md) | Its limits, what it reads, and [choosing an assistant](docs/agent.md#choosing-an-assistant) |
| [Contributing](CONTRIBUTING.md) | The development setup |

## Status

Working. The scaffold and skills, the provenance spine, the tNR assessment
tools, the model spec and generator, the web UI — its Experiment page, fit
curation and ISAAC publishing included — and the data tools are all
implemented, along with `nrw pack`, `nrw note`, `nrw assess`, `nrw isaac
export` and `push`, `nrw tiled`, and the unattended agent above.

## Development

```bash
pytest                          # tests
ruff check src tests            # lint (ruff lints and formats; no black)
ruff format --check src tests   # format
pre-commit run --all-files      # exactly what CI runs
```

`pre-commit` only checks files git tracks, so stage a new file before running
it.

The workflow, code standards, and review process are in
[.github/copilot-instructions.md](.github/copilot-instructions.md), imported by
[CLAUDE.md](CLAUDE.md). Shared standards live once in [skills/](skills/).

## License

BSD 3-Clause. See [LICENSE](LICENSE).
