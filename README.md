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
nrw init --beamtime june2026 --ipts IPTS-34567
nrw doctor
nrw sample new Sample4 --title "ionomer on copper"
```

Then copy reduced data into `samples/Sample4/data/steady/` and
`samples/Sample4/data/tnr/`, open the folder in VS Code, and work with Claude
Code. `nrw init` is idempotent and safe to run on top of an existing beamtime
folder — it never overwrites a file you have edited.

**[docs/getting-started.md](docs/getting-started.md) walks the whole thing
through on real data**: two OCV states either side of an EIS sequence,
co-refined with the 15 time-resolved slices measured during it, from an empty
directory to a promoted χ² = 1.83 result. Every command and number in it was
produced by running it.

Run `nrw --help` for the full command surface.

## Provenance

Run any refl1d script -- including one you wrote by hand years ago -- and it
comes out with a complete, queryable record:

```bash
nrw fit run samples/Cu/models/cu-d2o.py --method dream --samples 5000
nrw ls                        # every fit, newest first, with freshness
nrw whence figures/fig3.svg   # what produced this?
nrw promote <fit_id> --as final --reason "converged; SLD band excludes null"
nrw check                     # CI-able: fails if a result went stale
nrw pack <fit_id>             # a zip a collaborator runs with only refl1d
```

Each fit writes an immutable directory holding the frozen script, every input
file with its sha256, the exact package versions and git state (with a patch if
the tree was dirty), and the bumps output. `nrw whence` traces a figure back to
that record even after it has been copied out of the project, because figures
are stamped at write time.

Re-running an identical fit is refused by default, and a result whose data has
changed underneath it is reported as `STALE` everywhere it appears.

`nrw pack` closes the last gap. A result directory records the *hashes* of its
data, not the data, so it describes a fit nobody else can run. A bundle carries
the measurements themselves at the paths the frozen script expects, plus a
`verify.py` that applies the recorded best-fit parameters and checks
chi-squared against the value it should get. The recipient needs refl1d, bumps
and numpy.

## Running unattended

During a beamtime, data keeps arriving and nobody is watching. nr-workbench can
hand each settled measurement to a coding harness and let it work:

```bash
nrw agent watch --dry-run     # what each measurement is waiting for
nrw agent watch               # analyse each one as it settles
nrw agent run Sample4         # or one session, by hand
```

It contains no decision policy, deliberately. We measured against a week of
expert analysis with the findings written down as they happened: of 17
findings, 1 was reachable by arithmetic and 11 needed judgement, and
chi-squared ranks that corpus *backwards* — both promoted fits are worse in
chi-squared than the best in their arm. So the harness decides, and this
package supplies what has to exist around it: the offline checks it reads
first, limits it cannot talk past (`promote`, `--upload` and `--force` are
refused by a `PreToolUse` hook *and* by `nrw` itself under `NRW_AGENT=1`), a
bounded session, and a transcript.

A session refuses to start unless `## Fits to perform` in the sample's notes
says what you want. Deciding that is the one thing it must not do for itself.

**[docs/getting-started-with-agent.md](docs/getting-started-with-agent.md)
walks the setup through** — one sample first, then measurements that are still
arriving. [docs/agent.md](docs/agent.md) is the reference: what is enforced,
what is only asked for, and the evidence behind the split.

## Status

Working. The scaffold and skills, the provenance spine, the tNR assessment
tools, the model spec and generator, the web UI, and the data tools are all
implemented, along with `nrw pack`, `nrw note`, `nrw assess`, `nrw isaac
export` and the unattended agent above. See
[docs/project.md](docs/project.md) for the original requirement.

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
