# nr-workbench

A project workbench for neutron reflectometry analysis at the SNS Liquids
Reflectometer (REF_L / BL-4B).

`nr-workbench init` scaffolds an analysis project: one directory layout for
every sample, a curated `skills/` folder that Claude Code and GitHub Copilot
read, and a provenance ledger that keeps every result linked to the script,
data, and environment that produced it.

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

## Install

Install from git. nr-workbench depends on
[AuRE](https://github.com/neutrons-ai/aure), which is not published to PyPI, so
neither is this package.

```bash
python -m venv venv && source venv/bin/activate
pip install git+https://github.com/neutrons-ai/nr-workbench.git
```

For development:

```bash
pip install -e ".[dev]"
pre-commit install
```

## Use

```bash
mkdir my-beamtime && cd my-beamtime
nrw init --beamtime jen-june2026 --ipts IPTS-34567
nrw doctor
nrw sample new Sample4 --title "ionomer on copper"
```

Then copy reduced data into `samples/Sample4/data/steady/` and
`samples/Sample4/data/tnr/`, open the folder in VS Code, and work with Claude
Code. `nrw init` is idempotent and safe to run on top of an existing beamtime
folder — it never overwrites a file you have edited.

Run `nrw --help` for the full command surface.

## Status

Early. Milestone 0 (scaffold and skills) is implemented; the provenance ledger,
the model spec and generator, the tNR assessment tools, and the web UI are in
progress. See [docs/project.md](docs/project.md).

## Development

```bash
pytest                       # tests
ruff check . && ruff format . # lint and format (ruff does both; no black)
pre-commit run --all-files   # exactly what CI runs
```

The workflow, code standards, and review process are in
[.github/copilot-instructions.md](.github/copilot-instructions.md), imported by
[CLAUDE.md](CLAUDE.md). Shared standards live once in [skills/](skills/).

## License

BSD 3-Clause. See [LICENSE](LICENSE).
