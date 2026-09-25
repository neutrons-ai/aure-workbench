# Experiment sources, feeds and stores: how the pieces plug in

The Experiment page (see [experiment.md](experiment.md)) answers three questions,
each behind its own small interface in `nr_workbench.experiment`, so each can be
replaced without touching the others or the page:

| Question | Interface | Built | Planned |
|---|---|---|---|
| Where does the reduced data come from? | `DataSource` (`experiment/sources/`) | a local folder | Tiled |
| How do we learn that a run exists? | `RunFeed` (`experiment/feeds/`) | files appearing in that folder | the SNS web monitor, Tiled |
| Where is the organization kept? | `CatalogStore` (`experiment/store.py`) | parquet in the project | a facility service |

They are configured independently in `nrw.toml` (`[experiment.source]`,
`[experiment.feed]`, `[experiment.catalog]`). A planned kind is refused by name,
never replaced by the local folder: falling back would quietly watch a path
nobody chose.

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
  version listed. It is called from a bounded worker pool with a timeout, so a
  slow source costs a request a timeout, not a thread.

**A Tiled source**:

- `inventory()` searches the experiment's container (by IPTS) for reduced
  runs, maps each to a `SourceRun`, and takes the version from Tiled's own
  revision or etag.
- `read_bytes()` fetches the file as it was written, not a re-serialized array.

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
  lists need an ORNL login, so a monitor feed needs credentials (in `.env`,
  never in `nrw.toml`).
- **Tiled** can announce runs whatever the data source is.

A feed returns every run it currently knows. `experiment/live.py` works out
what changed, so a feed never keeps state about what it has already said. If a
remote API only offers deltas, keep the accumulated set inside the feed.

**Acquisition is not reduction.** A later run that a feed merely *announces*
does not make an earlier run complete: that is when the next acquisition
started, and the earlier run's last segment may still be reducing. Only a later
run with *reduced files* counts (`status.judge`, `latest_reduced`). A monitor
feed should keep it that way.

## `CatalogStore`: the organization

```python
class CatalogStore(Protocol):
    def describe(self) -> dict: ...
    def load(self) -> Catalog: ...
    def problems(self) -> list[Problem]: ...
    def update(self, *, runs=(), samples=(), now=None) -> Catalog: ...
```

`update` takes **edits**, not a whole catalog. Each edit carries the revision of
the record it was based on:

- a concurrent edit to a *different* record is merged;
- an edit made against a *stale* record raises `RecordConflict`.

A facility service holding the same tables should keep those semantics.
Otherwise two people editing one experiment overwrite each other.

The parquet store's schema follows data-assembler's lakehouse split: run rows
carry a nullable `sample_id`, and samples are their own table. Columns a store
does not know must survive a save. A newer schema version must be refused, not
read partially.

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
