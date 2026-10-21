# Experiment sources, feeds and stores: how the pieces plug in

The Experiment page (see [experiment.md](experiment.md)) answers three questions,
each behind its own small interface in `nr_workbench.experiment`, so each can be
replaced without touching the others or the page:

| Question | Interface | Built | Planned |
|---|---|---|---|
| Where does the reduced data come from? | `DataSource` (`experiment/sources/`) | a local folder | Tiled |
| How do we learn that a run exists? | `RunFeed` (`experiment/feeds/`) | files appearing in that folder | the SNS web monitor, Tiled |
| Where is the organization kept? | `CatalogStore` (`experiment/store.py`) | parquet in the project | Tiled |

They are configured independently in `nrw.toml` (`[experiment.source]`,
`[experiment.feed]`, `[experiment.catalog]`), on the Settings page of
`nrw serve`, or with `nrw experiment settings`. A planned kind is refused by
name, never replaced by the local folder: falling back would quietly watch a
path nobody chose.

**How a new kind reaches the page.** The Settings page and `nrw experiment
settings` list every kind from one registry, `SOURCE_OPTIONS`, `FEED_OPTIONS`
and `CATALOG_OPTIONS` in `project/experiment_schema.py`: the built ones to
choose from, the planned ones shown as coming. Adding a kind means building
it, registering it in `open_source`, `open_feed` or `open_store`, and setting
`available=True` on its `Option`. The kind lists the registries check, and
every surface, follow from that one flag.

## A measurement, and what a source lists

A **measurement** is what the catalog calls a run: it is keyed by its first run
number, and it is made of N angle segments, each reduced from its own subrun,
plus other **artifacts**. Today the one other artifact nrw reads is the
combined curve, every segment stitched into one file.

A source lists every artifact of a measurement it can see (`SourceRun.files`,
each with its `role`). It does not decide which ones matter. That is one rule,
in `instrument/reduced.py` beside the file-name grammar: `fitting_names` gives
the segments, or the combined curve when there are no segments. This is the
rule `nrw model new` fits by. Apply copies exactly those files
(`SourceRun.fitting_files`), so a sample holds what a fit of it reads, and the
quick look plots the same ones. A new artifact kind, such as tNR slices or a
reduction sidecar, gets a role there first.

## `DataSource`: files and their bytes, nothing else

```python
class DataSource(Protocol):
    kind: str
    def describe(self) -> dict: ...
    def inventory(self) -> Inventory: ...        # every run it can see, now
    def read_bytes(self, file: SourceFile, *, max_bytes: int) -> bytes: ...
```

A source never decides anything. Two decisions are made once, outside the
sources, so that every source is held to the same rules:

- **Whether a run is complete**: `experiment/status.py`.
- **How a copy is made safely**: `experiment/apply.py` (staged, whole-run,
  never overwriting).

Rules a source must keep:

- **Hand back the original bytes under the original name.** The header carries
  the incident angle (in radians) and the dQ convention (FWHM or sigma, a 2.355×
  difference). The `new_reduction` dialect records the segment number *only* in
  the filename. A source that re-encodes the data, or renames it, changes fits.
- **Name files exactly as the reduction does.** Apply accepts only names that
  `instrument/reduced.canonical_name` reproduces byte for byte.
- **`SourceFile.version` must change when the bytes do.** It is how "the facility
  re-reduced this after we copied it" is noticed. It may change when the bytes
  do not; apply then compares digests.
- **Never raise for missing data.** An unreachable source is an `Inventory` with
  `reachable=False` and a problem saying why.
- **`read_bytes` raises `SourceChangedError`** if the file is no longer the
  version listed. It is called under a deadline (`nr_workbench.bounded.Bounded`,
  with `SOURCE_TIMEOUT` from `experiment/sources`), so a slow source costs a
  request a timeout, not a thread.
- **`probe()` is optional, and cheap.** It backs the Settings page's **Check
  folder** and `nrw experiment settings --check`: a look at a source *before*
  it is chosen. It lists once and reads only the newest few runs, never the full
  inventory, and returns a `Probe`. A source without it is refused as "cannot be
  checked yet". The check builds the source exactly as the watcher would
  (`experiment.workspace.check_source`), so it reads the place that would be
  watched and no other.

**Where Tiled keeps an experiment.** On the facility server an experiment is a
container at `projects/isaac/IPTS-<n>/`, created by another process. Its
reduced files are in `autoreduction/`, one node per file, keyed by the file's
own name (see `docs/ground_truths.md`, 2026-10-21). It can be browsed at
`https://tiled.ornl.gov/ui/browse/projects/isaac/IPTS-<n>/`; the API, under
`/api/v1/`, needs a login. A source on Tiled reads `url`, not the folder's
`location`; this is its default:

```toml
[experiment.source]
kind = "tiled"                        # planned: refused by name today
url = "https://tiled.ornl.gov/projects/isaac/{ipts}/autoreduction"
```

Each key is read only for its own kind, and one set for the other kind is
reported as changing nothing. One key with two meanings would need a default
that followed the kind, and every placeholder `nrw.toml` shows would go stale
the moment the kind was changed by hand.

**An address on Tiled** (the source's, or the catalog's) is held to these rules
when it is saved and when `nrw.toml` is read (`nr_workbench/tiled/address.py`):

- printable ASCII, checked whole before it is parsed: a parser drops a tab or a
  line break, so the address checked would not be the one stored;
- `https://`, or plain `http://` only to this machine, for a test server;
- a host name or an IP address, matched as written, and a port other than 0;
- no user name, query or fragment -- and never printed whole in a message, on
  a page or in a listing: anyone on the machine can read the pages;
- a path of segments of letters, digits, `.`, `_` and `-`;
- **its experiment named as `{ipts}`**, a segment of its own, and no IPTS
  written in. On the person's server, with their permissions, a committed
  address naming another proposal, or a container above every proposal, would
  read it or write to it; and a written-in IPTS would stay behind when
  `[beamtime] ipts` changed;
- **on the person's own Tiled server**, `TILED_URL` (default
  `https://tiled.ornl.gov`), from `~/.nrw` or the shell and never from the
  project's `.env`. `nrw.toml` is committed with the project, so an address on
  any other host is refused, naming both. That is the one server a login, or
  `TILED_API_KEY`, is ever sent to.

**`TILED_URL` itself** is a setting only the person makes
(`env.person_setting`). It is refused, and with it every address on Tiled:

- while the project's `.env`, `.env.local`, `.flaskenv` or `.envrc` sets
  `TILED_URL`, `TILED_API_KEY` or `HOME`, whatever value is in effect. A
  program that loads those files (direnv, pipenv, an editor) puts them in the
  environment, where they would look like the person's own. The files are read
  as the loosest loader would, so `export`, quoting, `KEY: value` and a
  byte-order mark all count;
- from a file that someone else owns or can write, or that sits in a folder
  someone else can write (a sticky one, as `/tmp` is, excepted), as ssh refuses
  its configuration;
- for an agent's command (`NRW_AGENT`), when it is not what the person's files
  say, however it came to be set.

`nrw doctor` shows the server in use and where it was set.

**A Tiled source**:

- `inventory()` searches the experiment's container (by IPTS) for reduced
  runs, maps each to a `SourceRun`, and takes the version from Tiled's own
  revision or etag.
- `read_bytes()` fetches the file as it was written, not a re-serialized array.
- `probe()` looks at the container's newest few runs, for **Check folder**.

The in-memory source in `tests/experiment_fixtures.py` is the second
implementation that keeps this interface from quietly becoming folder-shaped.
Add the Tiled one beside it, and run the apply tests against both.

## `RunFeed`: "run N exists"

```python
class RunFeed(Protocol):
    kind: str
    def describe(self) -> dict: ...
    def poll(self, inventory: Inventory) -> FeedUpdate: ...   # a snapshot, not a delta
```

The feed is separate from the source because other feeds know different things,
and know them sooner:

- **The folder feed** (`feeds/directory.py`) learns of a run when its files
  appear. It is built on the source's own listing, so the folder is read once
  per poll.
- **The SNS web monitor** (`monitor.sns.gov`) reports each run as it is
  *acquired*, before any reduction exists. The page shows such a run as
  **awaiting reduction**, a state the folder can never show. The monitor's run
  lists need an ORNL login, so a monitor feed needs credentials. They belong
  where ISAAC's key does, in `~/.nrw` or the shell: never in `nrw.toml`, and
  not in the project's `.env`, which anyone who can write the project can
  write.
- **Tiled** can announce runs whatever the data source is.

A feed returns every run it currently knows. `experiment/live.py` works out
what changed, so a feed never keeps state about what it has already said. If a
remote API only offers deltas, keep the accumulated set inside the feed.

**Acquisition is not reduction.** Each angle's `.dat` file is written once
*that angle* has been measured, so a run's segments arrive one by one, and a
run whose files have settled may still have angles to come. A later run that a
feed merely *announces* does not make an earlier run complete: that is when the
next acquisition started, and the earlier run's last segment may still be
reducing. Today only a later run with *reduced files* counts (`status.judge`,
`latest_reduced`).

**What the monitor will add.** The web monitor reports the run being measured
*now*. "The instrument is measuring run M" proves that every segment of an
earlier run with a subrun below M has been *acquired*. Once each of those has
been reduced and its files have settled, that run is complete, without waiting
for the next run's reduction. That is also what will let the last run of a
beamtime complete without a person: the monitor then reports that nothing is
being measured. A monitor feed should announce the current run with
`state="acquiring"` and leave the judgement to `status.judge`, which will need
that one rule added.

## `CatalogStore`: the organization

```python
class CatalogStore(Protocol):
    def describe(self) -> dict: ...
    def load(self) -> Catalog: ...
    def load_report(self) -> tuple[Catalog, tuple[Problem, ...]]: ...  # and what it recovered from
    def update(self, *, runs=(), samples=(), experiment=None, now=None) -> Catalog: ...
```

The catalog holds three kinds of record: each run, each sample's context, and
the experiment's own context, the one record shared by every sample.

`update` takes **edits**, not a whole catalog. Each edit carries the revision of
the record it was based on:

- a concurrent edit to a *different* record is merged;
- an edit made against a *stale* record raises `RecordConflict`.

A facility service holding the same tables should keep those semantics.
Otherwise two people editing one experiment overwrite each other.

### Planned: the catalog on Tiled

The catalog is kept in the project, and committed with it. It is shared the
way the project is: through git. Sharing it more widely -- between the projects
of everyone on an experiment, and with other programs that record samples --
is Tiled's job, in the experiment's own container
([plan-tiled-catalog.md](plan-tiled-catalog.md)):

```toml
[experiment.catalog]
kind = "tiled"                        # planned: refused by name today
url = "https://tiled.ornl.gov/projects/isaac/{ipts}/context"   # the default
```

That kind is refused by name today, and listed as coming on the Settings page
and in `nrw experiment settings`. The address may already be set
(`nrw experiment settings --catalog-url`), held to the rules above; it is read
only once the kind is `"tiled"`, and until then it is reported as changing
nothing.

A store for it implements `CatalogStore` above, and must:

- **Keep revisions.** Every record carries one, and an edit made against an
  older one is refused with `RecordConflict`, never applied over the newer.
- **Check what it is given.** Records written by other programs arrive
  unchecked. Before a value is used, it must meet the rules `experiment/model.py`
  applies to the page's own edits: sample ids that are safe as folder names,
  one-line values without `|` or line breaks, and prose without headings or
  comment markers. Every one of these is written into `sample.md`, which people
  and the unattended agent read as the sample's truth.
- **Answer within a deadline.** A page or command that waits for a service that
  stopped answering must fail after a timeout, and say which service.
- **Never fall back to the project's files.** An edit meant for everyone must not
  land where nobody else looks.

Sharing one set of parquet files between projects was built and withdrawn:
files on a network mount do not make a database. See the 2026-09-28 entry in
`docs/ground_truths.md`, and the branch `shared-catalog-attempt`.

The parquet store's schema follows data-assembler's lakehouse split: run rows
carry a nullable `sample_id`, and samples are their own table. The
experiment's context is a third table of one row, written only once it has
been set, so a project that never sets it keeps the two files it always had.
Columns a store does not know must survive a save. A newer schema version must
be refused, not read partially.

## tNR, later

Sliced time-resolved series are not handled yet. The unsliced tNR run arrives
in the same folder as an ordinary reduced file and is treated as one. Adding
series means:

- `RunKey.kind = "series"`, reserved already.
- The source listing slices (`r<run>_*.txt`) and their `*_eis_reduction.json`
  sidecar. The sidecar's interval labels define the slice names; validate them
  as strictly as segment names.
- Apply copying a series into `samples/<id>/data/tnr/<run>/` as one staged
  directory.
- Tying a series to its unsliced run, which is where `theta_for_run` reads the
  angle. A sample must never include both, because co-refining them counts the
  same neutrons twice.

## Not planned

**ONCat**, the facility's data catalog, is deliberately not used. The reduced
files and the run feed already carry what the page needs, and adding it would
mean a dependency and an account for no new information.
