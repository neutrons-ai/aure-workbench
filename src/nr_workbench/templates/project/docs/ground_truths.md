# Ground Truths

Facts about the **tooling, the instrument and this project's conventions** that
are not derivable from the code — things that stay true no matter which sample
you are looking at. AI assistants do not remember previous conversations; this
file is how that kind of context survives between sessions.

**Findings about a sample do not go here.** If it names a run number or a
fit id, it belongs with that sample:

| A finding about… | Goes in |
|---|---|
| one fit | `samples/<id>/results/<fit_id>/NOTES.md` — `nrw note <fit_id> -m "..."` |
| how several fits relate | `samples/<id>/reports/*.md` — `nrw note --sample <id>` |

That is not filing for its own sake. Notes stored with the sample are shown
beside the fit they are about, and travel to a collaborator inside
`nrw pack`; a finding recorded here reaches neither.

So this file is for: an instrument quirk, a reduction convention, a package
version that broke something, a decision about how the project is organised.

Format:

```
### YYYY-MM-DD: Brief title

What was found, why it matters, and a link to the relevant file.
```

## Findings

<!-- Add entries below, newest last. -->
