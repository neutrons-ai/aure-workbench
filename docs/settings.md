# Settings: which file holds what

| File | Belongs to | Holds | Committed |
|---|---|---|---|
| `nrw.toml` | the project | the IPTS, the data folder, the watcher, the assistants, and how nrw fits (`[fit]`) | yes |
| `.env` in the project | you, on this machine | the language-model endpoint and its key; the Settings page's *Language model* writes it | no (gitignored) |
| `~/.nrw` | you | the same, for all your projects; and the ISAAC Portal and its key (`ISAAC_URL`, `ISAAC_KEY`) | — |
| `~/.aure` | you, and AuRE | the endpoint too, and AuRE's own fit defaults (`FIT_METHOD`, `FIT_STEPS`, `FIT_BURN`) | — |

[fitting.md](fitting.md) has what `[fit]` takes, and how AuRE's fits are set
up.

## The language-model endpoint

For the language-model endpoint, nrw takes each variable from the first of:

1. the shell's environment;
2. the project's `.env`;
3. `~/.nrw`;
4. `~/.aure`.

The ISAAC Portal's `ISAAC_URL` and `ISAAC_KEY` follow the same order.

`nrw doctor` lists the files it read and the settings it found, with keys
redacted. Keys and endpoints never go in `nrw.toml`, because it is committed
and shared. `.env.example` in the project lists the variables.

To use Claude through the Claude Code CLI, which needs no key, choose it under
*Language model* on the Settings page of `nrw serve`. That writes
`LLM_PROVIDER=claude_code` into the project's `.env`, which then wins over
`~/.aure`, and **Check** makes one call to prove it answers. See
[experiment.md](experiment.md#language-model).

## What is sent, and to which model

Four commands ask the endpoint for an answer — `nrw model new --from-notes`,
`nrw assess`, `nrw isaac export` and `nrw aure run` — and `nrw agent run`
drives a harness instead; while a harness is driving, those four stand down.
[llm-calls.svg](llm-calls.svg) maps them, and one diagram per command shows how
its prompt is assembled: [model new](model-new-llm.svg),
[assess](assess-llm.svg), [isaac export](isaac-export-llm.svg),
[aure run](aure-run-llm.svg) and [agent run](agent-run-llm.svg).
