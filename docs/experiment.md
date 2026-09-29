# The experiment page: organizing a beamtime's runs

During a beamtime, reduced runs land in a folder on the data mount one after
another. The **Experiment page** shows all of them in one table, lets you say
which sample each belongs to and how it was measured, and writes that into the
project: the data into `samples/<id>/data/`, and the context into
`samples/<id>/sample.md`, where the rest of the workflow already reads it.

Nothing downstream changes. `nrw model new`, `nrw data reconcile`,
`nrw agent watch` and the rest see a sample the page organized exactly as they
see one you set up by hand.

## Start it

From an empty folder:

```bash
nrw init
nrw serve
```

```
  http://127.0.0.1:8765/experiment      the experiment's runs
  http://127.0.0.1:8765/settings        its IPTS, data folder and watcher

  Data folder  /SNS/REF_L/{ipts}/shared/autoreduce/new_reduction  (nrw's default)
  ! The data location /SNS/REF_L/{ipts}/shared/autoreduce/new_reduction needs the
    project's IPTS, and nrw.toml has none. …

  This experiment is not set up yet: nrw needs its IPTS, or the
  folder its reduced data is in, before it can watch anything.

  To edit the experiment, open this link in your browser:
    http://127.0.0.1:8765/auth/9f0c…
  It works once, for one browser, and is kept out of the request log.
  Without it the pages are view-only.
  It opens Settings.
```

On a project that is not set up yet the link opens **Settings** (see
[Settings](#settings) below); once it is, the link opens the Experiment page.

The `/auth/…` link lets *this browser* make changes. Without it the page is
view-only, which is on purpose: an analysis node is shared, and anyone logged in
to it can reach `127.0.0.1`. The link stops working once it has been used, so a
copy left in scrollback or pasted into a chat opens nothing. To edit from a
second browser, restart `nrw serve` for a new link.

The same organization is available from the command line, for scripts and for
anyone who prefers it:

```bash
nrw experiment settings --ipts 34347 --write   # or the Settings page
nrw experiment status              # every run, its state and its sample
nrw experiment assign 234277 234280 --sample Sample6 --type steady --condition OCV
nrw experiment apply               # shows what would be written; changes nothing
nrw experiment apply --write       # writes it
```

## Settings

The Settings page sets what the Experiment page watches. Everything it saves
goes into `nrw.toml`, which you can also edit by hand.

- **The experiment.** Its IPTS and beamtime label. When the project's own path
  names an IPTS (`/SNS/REF_L/IPTS-34347/shared/…`), the page offers it.
- **Where the data is.** Either nrw's default location (see below), shown
  resolved as you type the IPTS, or another folder. **Check folder** says what
  a folder holds before you choose it: how many runs and their range, the
  newest runs, files it does not recognise, and the IPTS their headers name.
  A run whose headers name an experiment other than the IPTS on the page is
  flagged, run by run.
- **How new runs are noticed.** Files appearing in the data folder. The SNS web
  monitor and Tiled are listed as coming and cannot be chosen yet.
- **Where samples and assignments are kept.** Parquet files in this project's
  `experiment/`, committed with it. A metadata service, through which they
  would be shared with everyone on the experiment, is listed as coming; see
  [experiment-sources.md](experiment-sources.md).
- **Advanced**: the settle time and the poll interval.

A save changes only the lines of `nrw.toml` that hold these settings, keeps
every comment and hand edit, and keeps the previous file under
`.nrw/backups/`. It takes effect in the running server straight away, with no
restart. If `nrw.toml` was edited by hand since the page loaded it, the save is
refused and the page reloads, so nothing is written over. If the file writes a
setting in a shape nrw does not edit (an inline table, say), the page shows the
lines to change by hand instead. Changing the IPTS of an experiment whose
catalog already holds runs asks first.

`nrw experiment settings` does the same from the command line. With no options
it shows each setting and whether it follows nrw's default. With options it
shows the change as a diff, and `--write` saves it. `--check` looks at the
folder first, as **Check folder** does, with the same 15-second deadline.
`--json` prints one object, whichever options are given: `{"settings",
"change", "check"}`, where `change` and `check` are `null` unless asked for.

## Where the runs come from

By default the page watches the folder REF_L's `new_reduction` pipeline writes:

```
/SNS/REF_L/{ipts}/shared/autoreduce/new_reduction
```

with `{ipts}` taken from `[beamtime] ipts` in `nrw.toml`. **That location is
provisional** and expected to move. A project that keeps the default follows it
when nrw's default moves. To point somewhere else, use the Settings page or
`nrw experiment settings --location PATH`. By hand, edit the
`[experiment.source]` table `nrw.toml` already has:

```toml
[experiment.source]
location = "/SNS/REF_L/{ipts}/shared/autoreduce/new_reduction"
```

Uncomment it if it is still commented out, but do not add a second one: TOML
allows each table once, and a file that declares it twice stops every `nrw`
command until one is removed.

`nrw experiment status` says whether the folder can be reached, and names any
key in `[experiment]` it does not recognise rather than ignoring it.

The folder is re-read every 30 seconds while the page is open, and not at all
once nobody has looked for ten minutes. If the data mount stops answering, the
page keeps showing what it last saw and says so, rather than hanging.

## What each state means

| State | Meaning | Applied? |
|---|---|---|
| **complete** | Its files have stopped changing, *and* there is evidence the measurement ended. | Yes |
| **unconfirmed** | Its files have stopped changing, but nothing shows the measurement ended. | Only if you confirm it |
| **arriving** / **settling** | A file changed recently. | Not yet |
| **awaiting** | A run feed announced it, but no reduced files exist yet. | Not yet |
| **quarantined** | Its files do not look like one measurement. The page says why. | No |
| **clock skew** | Its files are dated in the future by the file server's clock. | No |

**Why "stopped changing" is not enough.** A run's angle segments are reduced as
each angle finishes measuring. In the reference experiment, run 218386's three
segments landed 15 and then 52 minutes apart. Five quiet minutes after the
first segment, it looks finished, and a copy made then is a third of a
measurement. It fits perfectly well, which is the problem.

So *complete* needs evidence the run ended, and one kind counts: **a later run
has been reduced.** Once the instrument has moved on and the next run's files
exist, this one is not getting more segments.

- **The planned segment count can only say "not yet".** When the
  `new_reduction` header says three segments were planned and two are here,
  the run is not complete, however much later data has arrived. When all three
  are here, that alone does not make it complete either: nobody has yet checked
  on a real file that the *first* segment's header already carries the whole
  plan. If it does not, a header read after segment 1 says "1 of 1" and would
  call a third of a measurement finished.
- **A run the feed announces does not count as the later run.** The
  announcement means the next acquisition started. This run's last segment may
  still be reducing.

A settled run without that evidence is *unconfirmed*. The last run of a
beamtime always is, because nothing comes after it. If the measurement is really
over, or was stopped early, tick "use run N as it is" when you review apply
(`--confirm N` on the command line).

## Organizing

Select runs in the table, choose a sample (or type a new id), optionally a
**type** and a **condition** (`OCV`, `-0.5 mA/cm2`, …), and **Assign**.
**Exclude** keeps a run in the sample but out of its data.

The type is **steady** or **tNR**, or one you add with **Add a type…**; a type
added once is offered from then on. A run whose type was never set is steady.
In the bar above the table the type starts at *unchanged*, so assigning runs
leaves the type each one already has.

Click a sample to fill in its context. Each box becomes the section of
`sample.md` it is named after:

- **Description** and **Details**: what the sample is.
- **Was the sample moved between measurements?** This decides whether alignment
  is fitted once or once per state, and it cannot be read from the data.
- **Measurement conditions**: what holds for every measurement.
- **Measurements**: one line per run, with its type, its condition, a
  **good** switch, and notes on that measurement alone: realigned, bowed, a
  segment that looks high. Type and condition go into the measurement table.
  Each run's notes go under *Measurement conditions*, with the run named first
  (`- Run 218386: realigned after mounting`), so nobody has to guess later
  which measurement a note is about.
- **Fits to perform**: an unattended agent session will not start without it.

A bad run is slid to **bad: not used**, which excludes it: the next apply
leaves it out of the table and moves its copy out of `data/steady/`. Say why in
its notes. They stay in the catalog and on the page, but are not written into
`sample.md`, whose readers would otherwise look for the run's data.

The preview shows the `sample.md` that will be written.

Some text is refused, with the reason, because a reader of `sample.md` would
misread it. A run's notes are held to the same rules, since they are written
there too:

- **A heading line inside a box.** A `## Fits to perform` typed into the
  Description would become the agent's task.
- **`<!--` or `-->`.** These hide text from readers that strip comments.
- **A `|` in a condition.** It splits the table cell.
- **An unclosed code block.**
- **A second table with a Run column.**

## Applying

**Review** shows exactly what apply would do:

- **+ copy**: new files into `samples/<id>/data/steady/`. These are the files a
  fit reads: each run's angle segments, which fits co-refine. A combined curve
  beside them stays at the source. A run that has only a combined curve is
  copied as that.
- **− move-out**: copies of runs you have since excluded, or moved to another
  sample, go to `samples/<id>/data/excluded/<run>/`. They are moved back if you
  include the run again. They are never deleted.
- **sample.md**: created, updated, or left alone, with the reason.

Nothing is ever overwritten. Each case below is shown and left alone for you to
decide:

- **source-changed**: the facility's file was re-reduced after it was copied.
- **local-edited**: the copy in the sample was edited.
- **conflict**: a different file with the same name was already there.
- **sample.md edited by hand**: kept as it is. The catalog's version goes
  beside it as `sample.md.nrw-new`.

`samples/<id>/data/sources.json` records what apply copied and from which
version of the source file; it is how those cases are told apart.

Only runs that are *complete*, or that you confirmed, are copied. A run is
copied whole or not at all.

## Models

Once a sample has data, the **Models** panel below Apply writes a model spec
for it: give the model a name and click **New model**. The spec is exactly what
`nrw model new <sample> --name <name>` writes from the data in
`samples/<id>/data/`: one state per run, each segment's angle read from its own
file, and a placeholder stack to replace with the real layers. The panel lists
the sample's specs, and marks those a script has been generated from.

A spec that already exists is never overwritten: choose another name, or edit
that one. A name is a plain name (letters, digits, `.`, `-`, `_`), because it
becomes the spec's filename. As with every change on the page, only a browser
that opened the link `nrw serve` printed can write one.

## A sample that already exists

A sample written by hand before the page existed can be taken into the catalog:

```bash
nrw experiment adopt Sample6                    # shows what would be recorded
nrw experiment adopt Sample6 --write            # records it; sample.md untouched
nrw experiment adopt Sample6 --write --rewrite  # and lets the catalog write sample.md
```

Its sections become the sample's context and its measurement table becomes the
run assignments. Runs you assigned to the sample on the page that its table
leaves out are kept as they are: the file never listed them, so leaving one out
is not removing it. Anything with no place in the catalog, such as your own
`## Notes` section or a comment you wrote, is listed as a **leftover**. A
leftover blocks `--rewrite`, because the rewrite would lose it.

The same step pulls hand edits back. If you edit a catalog-written `sample.md`
in your editor, the page shows **edited by hand** and offers **Pull hand edits**
to bring your changes into the catalog.

An entry `- Run 218386: …` under *Measurement conditions* becomes that run's
notes, continuing on the lines indented under it. A pull changes a run's notes
only where the file has an entry for the run; an entry left empty
(`- Run 218386:`) clears them. A run with no entry keeps its notes, because a
file written by hand, or before nrw wrote notes, never had any.

`--rewrite` backs the old file up under `.nrw/backups/`. That directory is
gitignored, so the backup exists on this machine only; commit first if the file
matters.

To stop the catalog writing a sample's `sample.md` altogether:

```bash
nrw experiment release Sample6
```

## Where it is stored

| File | What | Committed |
|---|---|---|
| `experiment/runs.parquet` | One row per run: its sample, type, condition, good or not, its notes | Yes |
| `experiment/samples.parquet` | One row per sample: its context | Yes |
| `experiment/catalog.json` | Digests of both, written last, so an interrupted save is noticed | Yes |
| `samples/<id>/data/sources.json` | What apply copied | Yes |
| `nrw.toml` (`[beamtime]`, `[experiment.*]`) | The IPTS, where the data is, and how new runs are noticed | Yes |

The tables are parquet: the same shape the facility's data lakehouse uses,
so that a later version can read the catalog from a facility service instead.
Parquet is binary, which has two consequences:

- **git cannot merge it.** If two people organize the same experiment on
  different branches, the merge leaves one side in place with no conflict
  markers. nrw refuses to load or save the catalog while git reports it
  unmerged, and says how to resolve it.
- **A diff does not show what changed.** `nrw experiment status --json` does.

A catalog file that exists but cannot be read is never treated as empty. The
page shows it read-only, and nothing is saved over it until it is restored.

## Unattended agents

An assistant can read the experiment, with `nrw experiment status --json` and
the page's API, and can preview `apply` and `settings`. It cannot assign,
apply, adopt, release or change the settings. Which sample a run belongs to is a claim about the experiment, like
promoting a fit, so those commands are refused under `NRW_AGENT` and the agent
is told to write its proposal in `ESCALATIONS.md`.

## Not yet

- **Sliced time-resolved (tNR) series.** The unsliced tNR run arrives in the same
  folder as an ordinary reduced run and is treated as one. Series support comes
  later: slices plus their reduction sidecar, tied to the unsliced run, which
  gives the angle and must never be co-refined with its own slices.
- **Other sources of runs.** The SNS web monitor, or Tiled. See
  [experiment-sources.md](experiment-sources.md).
