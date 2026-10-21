# Plan: the experiment's data and catalog on Tiled

> **A design record, kept for its reasoning.** What the code does now is in
> the code, `docs/experiment-sources.md` and `docs/ground_truths.md`; where
> they disagree with this plan, they are right.

**Status:** in progress. Step 1 (the experiment's own context) was built and
reviewed on 2026-10-20; its review changed the marker format and the parquet
store's recovery (`docs/ground_truths.md`, same date). On 2026-10-21 the plan
gained reading the reduced data from Tiled (step 6) and a sign on the page
that there is new data to sync (step 2). Step 2 was built and reviewed the
same day; its review made the sign share apply's refusals instead of copying
two of them, and made the page survive any one sample it cannot look at.
Steps 3 to 13 are not started.

## What this adds, and what it leaves alone

The experiment catalog gets a second home. That is the record of which sample
each run belongs to, under what condition, whether it is used, the notes on
it, and each sample's context. With

```toml
[experiment.catalog]
kind = "tiled"
```

a project keeps it on tiled.ornl.gov, in the experiment's `context/`
container. There everyone on the experiment, and other applications, read
and edit the same records, live, from their own projects and machines.
People log in with Globus, through `nrw tiled login`.

The catalog also gains **the experiment's own context**: what applies to the
whole experiment rather than to one sample. Today that can only be written
into each `sample.md`, once per sample. It is a catalog feature, so it
arrives in both stores.

**The file-based catalog stays, and stays the default.** `kind = "parquet"`
keeps working exactly as it does: the same `experiment/*.parquet` files,
committed with the project and shared through git, with the same locks,
recovery and checks. It gains one table, for the experiment's context.
Nothing moves unless a project chooses Tiled. The rule for every step below:
**the parquet store's existing tests pass unchanged.**

**And the data, from beside it.** Another process creates each experiment's
container on Tiled and catalogues its reduced data there, in `autoreduction/`,
and that data is never written by nrw. With

```toml
[experiment.source]
kind = "tiled"
```

it reads it, instead of the data mount: the same reduced files, byte for
byte, so a project can be worked on from any machine that reaches
tiled.ornl.gov. Apply copies them into `samples/<id>/data/` exactly as it
copies from the folder. `kind = "local"`, the folder, stays the default. Both
sides know a run by the same thing, its run number.

**A sign that there is new data.** Whichever the source, the page says when
runs assigned to a sample have files that apply has not copied yet, or that
were reduced again since, and when new runs belong to no sample yet. Copying
stays a person's decision: the sign leads to the Review, and apply copies
what was reviewed.

## Why

Today the catalog is shared the way the project is, through git. Sharing it
between the projects of everyone on an experiment, and with other programs
that record samples, is a metadata service's job. That decision is recorded
on 2026-09-28, after a shared set of parquet files on a network mount was
built and withdrawn.

`CatalogStore` was shaped for this: `update` sends edits, not a whole
catalog, so a service can merge concurrent edits record by record.
`docs/experiment-sources.md` already lists what a store for a service must
do: refuse an edit made against an older record, check records others wrote
before they are used, answer within a deadline, and never fall back to the
project's files. Tiled is that service, and `context/` is where on it the
experiment's context is kept.

### What Tiled takes off our hands

This is the shared catalog again: the one withdrawn on 2026-09-28 for
turning files on a network mount into a database. Tiled is the database, so
most of what that attempt had to build is now the server's:

| What the withdrawn attempt had to build | Now |
|---|---|
| saves that check nobody wrote in between | Tiled refuses a key that exists, and the whole-record `test` refuses a stale write, but for a race of milliseconds |
| a history of its own, since git no longer kept one | Tiled's revisions |
| group permissions for several accounts | Tiled's access tags |
| locks, and lock timeouts for NFS | the server orders its own writes; nrw keeps one lock, inside its own process |
| deadlines for a mount that stops answering | an HTTP timeout |
| detecting writes by other programs, and accepting or restoring them | other programs write through the same API, under the same protocol, so a stale write of theirs is noticed like anyone's |
| moving existing projects over | still not built: new experiments only |

What stays nrw's is what Tiled cannot know: that a record must meet
`sample.md`'s rules before it is used, that every node needs tags because
Tiled's default is private to its writer, the Globus login, and telling the
page that someone else has edited. That attempt came to about 8,500 lines;
the catalog on Tiled in this plan, with its share of the client and the
login, to about 3,000 (see "Size").

## What the server gives us

Checked on 2026-10-20 and 2026-10-21; the details and how each was found are
in `docs/ground_truths.md` under those dates.

- tiled.ornl.gov runs Tiled 0.2.18, `api_version` 0: no stable API. Every
  data route answers 401 without a login.
- An experiment, such as `projects/isaac/IPTS-37740/`, holds `nexus/` and
  `autoreduction/`, tagged `isaac` like the experiment itself.
  `autoreduction/` is one `bytes` node per reduced file, keyed by the file's
  own name and served from the data mount's `new_reduction` folder: the bytes
  nrw's local source reads. The nodes carry no metadata. The listing gives
  each file's size, and each download an `ETag` and a `Last-Modified` taken
  from the file on the mount.
- It accepts metadata writes, and keeps every version a write replaces,
  numbered.
- Access is by tag. A node created without one is visible only to its
  creator, a child does not inherit its parent's tags, and applying a tag
  takes owning it.
- Creating a key that already exists is a clean 409. A JSON Patch `test` is
  checked and then written in two separate steps, so it is not a true
  compare-and-swap. A failed one comes back as a bare 500, and the server
  drops the connection.
- Deleting a node deletes its children and their revision history.
- Logging in with Globus is Tiled's own device-code flow. The server gives
  a link and a code, the person approves them in any browser, and the
  program that asked receives a session. A session is a short-lived access
  token (15 minutes by default) and a refresh token (7 days). Renewing hands
  out new ones without using the old one up, until the session itself ends
  (365 days). Those are Tiled's defaults; the server's operators can change
  them.
- The `tiled` client library requires `pydantic-settings<2.12`, older than
  this environment has. httpx is already installed.

## Decisions (2026-10-20)

| Question | Decision |
|---|---|
| Where | each experiment's `context/` container: `https://tiled.ornl.gov/projects/isaac/{ipts}/context` |
| Shared with | experiment members, who create and edit records there, and other applications. nrw is the first, so the layout below is the schema |
| Who creates what | another process creates `IPTS-<n>/` and its data, and never rebuilds it. nrw creates `context/` when it is missing, and `runs/`, `samples/` and its records inside it |
| Who can see it | every node nrw creates carries its parent's access tags, never none |
| The experiment's own context | a third kind of record, in both stores, written into every managed `sample.md` |
| The data | `[experiment.source] kind = "tiled"`, read from the experiment's `autoreduction/`; the folder stays the default (2026-10-21) |
| Syncing it | a sign on the page, for whatever source, of what apply would copy and of runs no sample holds; apply stays a person's decision (2026-10-21) |
| Existing projects | new experiments only: a project whose `experiment/` holds catalog records is not switched |
| Sign-in | a Globus login, `nrw tiled login`, kept as a session that nrw renews. People probably cannot mint API keys in production; a key set by hand still works |
| Client | a few HTTP routes through httpx, not the `tiled` library |

## Design

### The experiment's own context

A sample's context has five sections in `sample.md`. Some of what people
write there is true of every sample in the experiment: the cell, the
electrolyte, how the instrument was set up, how samples were aligned. The
catalog gains one record per experiment for that:

| Field | What it holds |
|---|---|
| `title` | the experiment's name, one line |
| `description` | what the experiment is, and what it is for |
| `details` | what every sample shares: composition, environment, cell |
| `measurement_conditions` | what every measurement shares: alignment, instrument, normalisation |
| `rev`, `updated_at` | as for every record |

Those four are all it holds for now. A proposal's text, if it is added
later, is one more field of this record.

- **The rules are a sample's.** The title is one line, and the prose has no
  headings, comment markers or run-note lines. Edits go through
  `apply_changes` with the same revision check, and the Experiment page
  edits the record above the samples.
- **In the parquet store** it is a third table, `experiment.parquet`, with
  one row, listed in the manifest. An older nrw neither reads it nor removes
  it.
- **On Tiled** it is the metadata of `context/` itself, the node that is the
  experiment's context (below).
- **Into `sample.md`.** Each managed sample's file carries the experiment's
  text at the head of the section it belongs in: the description in
  Description, the details in Details, the measurement conditions in
  Measurement conditions. It is set apart and marked as the experiment's.
  Every reader therefore sees it without being changed: AuRE's setup reads
  Description and Details, and `nrw model new --from-notes` and the agent
  read Measurement conditions. `adopt` tells the marked text from the
  sample's own, as it already does the mounting sentence and the run notes,
  and never pulls it into a sample's fields. The title is not written into
  `sample.md`.
- **A change reaches every sample.** Editing the experiment's context makes
  every managed `sample.md` due to be applied, as editing a sample does for
  that sample today.

### New data, and the sign to sync

The page polls the source already, and every copy apply has made is recorded
in the sample's `data/sources.json`, each file with the source's version of it.
Comparing the two needs no file read, so every poll can say what an apply
would do:

- **ready to copy**: runs that apply would copy now, by its own rules (assigned,
  included, complete or confirmed), with a file `sources.json` does not hold;
- **reduced again**: a copied file whose version at the source has changed
  since (the Review then reads it, to tell a new reduction from the same bytes);
- **in no sample**: complete runs no sample holds, which nothing copies until
  someone assigns them.

The totals sit in the page's top line, beside when the source was last
checked: the Apply panel is below the runs and the quick look, off a laptop's
screen. One opens the Review, the other selects the runs in no sample, ready to
assign. Each sample's count is beside its name in the list, and the number
ready to copy is in the tab's title, for a page left open behind others. The
Review shows exactly what would be copied, and apply copies it. Nothing is
copied by itself: apply writes into folders people and agents are working in.

The rules are apply's own, so the sign never promises what apply will not do
(as built, 2026-10-21):

- **Planning is split where it would read.** Each file's fate, as far as no
  read decides it, comes from one set of steps; the Review then reads what it
  must, and the sign stops there. It takes only the steps for the runs a
  sample uses, so it never walks the sample's other files.
- **The refusals are shared, not copied.** What stops apply for one sample
  (an id nrw would not write, a folder differing only in case, a `sample.md`
  the catalog cannot write, a copy record that cannot be read) or for all of
  them (a damaged scaffold lock) is asked in one place by both. The sign
  counts nothing there, and names it in the page's notes. The first version
  copied two of the four, and promised copies that apply skipped.
- **It reads no data file, and never waits.** It follows no link in a
  sample's data (`nrw import` points them into the facility's tree), and opens
  `sources.json` without blocking, taking only a regular file of at most
  8 MiB.
- **One sample cannot take the page down.** A sample it cannot look at is
  that sample's note. Anything else is logged, and the sign is empty with one
  note; the page, its poll and the answer to a saved edit go on.
- **A run in no sample is one nobody decided about.** Excluding it (a direct
  beam, an alignment scan) stops it being counted. Assigning is not applying,
  so a damaged lock leaves this count alone.
- **What a person did is not new data.** A run included again, whose copy
  comes back from `data/excluded/`, and one excluded, whose copy moves out,
  are not counted. Either could be signed later ("N runs to move out"), from
  the same steps.

It is the same for the folder and for Tiled, since both list versions.

### The reduced data from Tiled

```toml
[experiment.source]
kind = "tiled"
location = "https://tiled.ornl.gov/projects/isaac/{ipts}/autoreduction"   # its default
```

`TiledDataSource` (`experiment/sources/tiled.py`) keeps every rule
`docs/experiment-sources.md` sets for a source:

- **Listing.** `autoreduction/` is listed a page at a time, with each node's
  data source: one node, one file. Its key is the file's name, held to the
  rule the folder's names meet (`instrument/reduced.canonical_name`). A name
  that is not exactly canonical is refused by name, never renamed, and files
  group into runs as the folder's do.
- **Versions.** A file's version is its asset's id and size, from the listing,
  so it changes when the file is registered again or rewritten at another
  size. A rewrite that keeps the size does not show in the listing. The
  Review therefore asks again for each copied file with its `ETag`, which
  costs a 304 when it is unchanged, so an apply never copies past one. (Open
  question: the cataloguing process could record each file's time in its
  node's metadata, which would make the listing exact.)
- **Headers and times.** The first time a version is listed, its bytes are
  read once (about 10 kB a file), for the title, angle and start time the page
  shows. The `Last-Modified` of that read is the file's time on the server's
  clock, and the server's `Date` is its now, so settling needs no clock of
  nrw's. Both are cached by version, as the folder source caches headers. The
  first look at a full experiment reads each file once: 336 files, about
  3.4 MB, for IPTS-37740.
- **Bytes.** `read_bytes` is `GET /asset/bytes/<path>?id=<asset id>`: the
  original bytes, under the original name. It raises `SourceChangedError` if
  the size, or the `ETag` the header read saw, is not the one listed.
- **The check.** `probe` lists once and reads the newest few runs, for the
  Settings page.
- **Run announcements** need nothing new. The folder feed is built on the
  source's own listing, so with `kind = "tiled"` a run is noticed when it
  appears on Tiled.

It shares the client, the login, the host binding and the deadlines with the
catalog (steps 4 and 5), and never writes.

### Configuration

```toml
[experiment.catalog]
kind = "tiled"
location = "https://tiled.ornl.gov/projects/isaac/{ipts}/context"   # the default
```

- `"tiled"` replaces the planned `"api"` in `CATALOG_OPTIONS`. `"api"` was
  always refused, so no project can depend on it.
- `[experiment.catalog]` joins `EXPERIMENT_KEYS`. That is what makes
  `nrw init` render it back from what the file says. Without it, a catalog
  set by hand would leave `nrw.toml.nrw-new` on every later `init` (the
  2026-09-25 entry). It also puts the table, commented out, into new
  projects' `nrw.toml`, and makes `catalog.kind` and `catalog.location`
  settings of the Settings page and `nrw experiment settings`.
- `project/settings.py` gets a branch for each, with validation. A key in
  `CHANGE_KEYS` with no branch passes the "is it a setting" check and is
  then dropped without a word.
- `location` must be `https://`, or plain `http://` on this machine's
  loopback, for tests. It may hold no user name, query or fragment, and its
  path is segments of letters, digits, `.`, `_` and `-`. `{ipts}` is filled
  in as it is for the data location. It is read only when `kind = "tiled"`.
- On Tiled, changing the IPTS changes which catalog the project reads. The
  confirmation the Settings page already asks for an IPTS change must say so.

### Logging in

`nrw tiled login` signs a person in with Globus, from any terminal, including
one on an analysis node with no browser. People are not expected to be able
to mint API keys on the production server, so nrw keeps the Globus session
itself, as Tiled's own client does:

1. **It starts the login.** It finds the Globus login on `TILED_URL`'s about
   page, prints the link and the code, and opens a browser only where there
   is a display. The link works from any machine. It is Globus's own login
   page, printed with its host, so that a link anywhere else would stand
   out.
2. **It waits** at the interval the server asks for, until the person has
   logged in and entered the code, or until the code expires (15 minutes by
   default). nrw sends nothing to any host but `TILED_URL`'s: it builds the
   address it polls itself, as it builds every other, rather than taking
   the one the server names.
3. **It keeps the session in a file of its own**, `~/.nrw-tiled`, readable
   by its owner only and never printed: the refresh token, and the current
   access token with its expiry. The file is replaced whole, never edited
   in place. It is not `~/.nrw`, which holds a person's settings and is
   written only when they change one.
4. **Every nrw process renews it as needed.** An access token lasts minutes,
   and nrw renews it from the refresh token when it has run out, or once
   after a 401. Renewing does not use a refresh token up, so `nrw serve` and
   a command in another terminal can both renew at once without logging
   each other out. nrw keeps the newest one, so a session used at least
   once a week lasts as long as the server allows.

`nrw tiled logout` ends the session on the server and deletes the file.

A session acts with all of the person's permissions: an API key can be
limited, a session cannot. That makes `~/.nrw-tiled` the secret here that
needs the most care, and it is noted for the security review. A key set by
hand as `TILED_API_KEY`, for a service account or wherever the server lets a
person mint one, is used instead whenever it is set.

Under the unattended agent, both commands are refused, by the guard and by
the commands themselves: a login is the person's to make.

### Credentials

- `TILED_URL` (default `https://tiled.ornl.gov`) and `TILED_API_KEY`
  (optional, secret) become known settings, read the way `ISAAC_KEY` is:
  from `~/.nrw` or the shell, never from the project's `.env`, which anyone
  who can commit to the project can write. `nrw doctor` lists them, the key
  redacted.
- **Credentials go to one server: the person's `TILED_URL`.** `nrw.toml` is
  committed too, so a `location` on any other host is refused, and the
  refusal names both. This is the lesson of the ISAAC key (2026-09-30).
- A credential travels only in the `Authorization` header (`Bearer` for a
  session, `Apikey` for a key): never in a URL, a message, a log or a page.
  Redirects are not followed. Paging uses `page[offset]`, never a link the
  server hands back.
- A key that is not alphanumeric is refused before anything is sent: Tiled
  cannot have issued it.

### Layout and records

```
projects/isaac/IPTS-<n>/            created by another process
├── autoreduction/                  the reduced data, catalogued by others
└── context/                        spec context-experiment, version 1: the experiment's record
    ├── runs/
    │   └── 218386-steady/          spec context-run, version 1: one run
    ├── samples/
    │   └── S1/                     spec context-sample, version 1: one sample
    └── ...                         other applications' nodes, left alone
```

- **`context/` is the experiment's, not nrw's.** Its own metadata is the
  experiment's record. Inside it nrw keeps to two containers, `runs/` and
  `samples/`, and reads, writes and deletes nothing else there.
- **Each record is a node with no data.** Its metadata is the record, with
  the same fields and types as a row of the parquet tables, `rev` and
  `updated_at` included. Fields nrw does not know are kept through every
  write, as unknown columns are today.
- **Each record carries a spec**, `context-experiment`, `context-run` or
  `context-sample`, at version 1. That is how any application recognises a
  record, and knows which version of the fields it holds. A record of a
  newer version is refused, as a newer parquet catalog is. A node in `runs/`
  or `samples/` with no spec is not a record: nrw names it among the
  catalog's problems and leaves it alone.
- **Keys:** `<run>-<kind>` for a run, and the sample id for a sample
  (`[A-Za-z0-9][A-Za-z0-9_-]{0,63}`, already safe). Tiled 0.2.18 checks keys
  hardly at all, so nrw checks what it creates and what it reads.

| Record | Fields |
|---|---|
| experiment | `title`, `description`, `details`, `measurement_conditions`, `rev` (integer), `updated_at` |
| run | `run` (integer), `kind` (`"steady"`), `sample_id` (text or null), `measurement`, `condition`, `include` (true or false), `note`, `title`, `start_time`, `rev` (integer), `updated_at` |
| sample | `sample_id`, `title`, `description`, `details`, `mounting` (`unknown`, `once` or `remounted`), `measurement_conditions`, `fits_to_perform`, `rev` (integer), `updated_at` |

A run's `title` and `start_time` are copied from its reduced file's header,
as today. Every other field is a person's decision.

**This layout and the table are the schema for every application that writes
in `context/`.** nrw is the first. Each one must:

- give a new record its spec, and its parent's tags;
- test the whole record as it read it, write it whole, and add one to `rev`;
- keep fields it does not know;
- hold prose and one-line values to the rules below, because nrw writes them
  into `sample.md`.

### Reading

1. **Read `context/`**, whose metadata is the experiment's record. If it
   does not exist, the catalog is empty, and the first edit creates it. If
   `IPTS-<n>/` does not exist either, the experiment is not on Tiled yet,
   and the message says which location was looked for, so that a mistyped
   one is not mistaken for an empty catalog.
2. **List `runs/` and `samples/`** with their metadata, a page at a time. A
   missing one is empty.
3. **Check every record** by the rules the page's own edits meet
   (`experiment/model.py`):
   - types (`include` must be `true` or `false`, not `1`);
   - safe sample ids, and a key that matches the record;
   - one-line values;
   - prose without headings or comment markers.

   Records from other applications arrive unchecked, and these values are
   written into `sample.md`, which people and the unattended agent read as
   the sample's truth. A record that fails is refused by name, and the
   catalog with it, as an unreadable parquet row is today: it is shown
   read-only and never saved over.

The whole catalog is refused, not just the one record, because a catalog read
with a record missing is worse than none. `sample.md` would be rendered
without it, and a run would drop out of its measurement table, and out of
the next fit, without anyone deciding so.

Since step 1, the rules for text are also enforced where `sample.md` is
written (`prose_for`), for every store. That leaves step 8 a choice for text
that breaks a rule but is otherwise a sound record: refuse the catalog on
reading, as above, or read it and let `prose_for` refuse the files, which
keeps the catalog editable so the text can be fixed on the page. A record of
the wrong shape or types is refused on reading either way.

A read is three requests, plus one per extra page of 300 records. The page
already loads the catalog on every poll, so a watched experiment costs Tiled
three requests per viewer every 30 seconds by default.

### Writing

`update` keeps the semantics the page relies on: an edit to a record that
changed since it was loaded is refused (`RecordConflict`), an edit to another
record is merged, and an unchanged save writes nothing.

1. **Read the catalog afresh, and apply the edits** with `apply_changes`, as
   the parquet store does. Every value is validated, and every base revision
   checked, before anything is written.
2. **Create what is missing.** That is `context/`, only if `IPTS-<n>/`
   exists, and then `runs/` and `samples/`. Each new node gets its parent's
   tags. A 409 means another application made it first, which is fine. nrw
   never creates `IPTS-<n>/`.
3. **Write only the records whose revision changed.** The experiment's record
   goes first, then samples are created, then runs are written, then samples
   are deleted, so that every state in between is one the page can show.
   - **A new record** is created with its key. A 409 means someone created
     it first: `RecordConflict`.
   - **A changed record**, the experiment's included, is one `PATCH`: a JSON
     Patch `test` of the whole record as it was read, then a `replace` of the
     whole record. If anyone changed any of it in the meantime, the test
     fails. A failure arrives as a bare 500, so the record is read again. If
     it changed, that is a `RecordConflict`; if not, the server failed, and
     the message says so. The next request opens a new connection.
   - **A deleted sample** is read again just before the delete, and the
     delete is refused if the sample changed. The delete itself has no test.
4. **Never untagged.** If a parent's tags cannot be read, or the server
   refuses them, the edit fails with the server's reason. Writing without
   tags would succeed, and make the records invisible to everyone but the
   writer.

Within one process, writes to one catalog take a lock, as the parquet store's
writes do.

**Where this is weaker than the parquet store.** The documentation will say
so too:

- **Two saves of one record within milliseconds can both land.** Tiled tests
  and then writes in separate steps. The version overwritten is kept in the
  record's revisions.
- **Another application can skip the protocol.** One that writes without the
  test can overwrite an edit. The revisions keep what it replaced.
- **An edit of several records can stop partway.** A conflict on the third
  of five leaves the first two written. The message names the record that
  conflicted, and reloading shows the rest.
- **The history is Tiled's, not git's.** `experiment/` stays empty, and
  committing the project carries no catalog.
- **The `test` compares with Python's `==`.** `1`, `1.0` and `true` count as
  equal, so a change of type alone passes it. The type checks on reading
  cover that.

### Noticing other people's edits

The page reloads the catalog when its version changes. Today the version is a
digest of the parquet files' sizes and modification times (`_catalog_version`
in `web/experiment.py`). It becomes the store's to say:

- the parquet store keeps exactly that digest, with the experiment's table
  added: the code is moved, not changed;
- the Tiled store digests the records it has just read, so the poll that
  already loads the catalog asks the server nothing more. An edit made by
  another application shows up the same way as a colleague's.

### Failures, and what each one says

| What happened | What the person sees |
|---|---|
| not logged in, and no key | to run `nrw tiled login` |
| the session has expired or was ended, or the key is refused | that, with the server's name, and to run `nrw tiled login` again; never a token or the key |
| not allowed (403), or a tag the writer does not own | the server's reason |
| `IPTS-<n>/` is not on Tiled | that the experiment is not on Tiled yet, at which location, so the catalog cannot be edited |
| `context/`, `runs/` or `samples/` does not exist yet | an empty catalog, and that the first edit creates it |
| the server is down, or does not answer within the deadline | which server, and that the catalog is read-only until it answers |
| a record fails its checks, or has a newer version | which record and why; read-only until it is fixed on Tiled |
| a node in `runs/` or `samples/` without a spec | its name, as a problem; it is left alone |
| `location` is on another host than `TILED_URL` | both hosts, and how to trust the other one (`TILED_URL` in `~/.nrw`) |
| the project's `experiment/` holds records | that switching would leave them behind, and that copying is not built yet |

None of these falls back to the parquet files. An edit meant for everyone
must not land where nobody else looks.

### Checking before using

`nrw experiment settings --check` and the Settings page's check test the
catalog as they test the data folder, by reading only:

- who the session or key belongs to (`/api/v1/auth/whoami`), and when the
  session must next be renewed by;
- that `IPTS-<n>/` exists and can be read, whether `context/` does, and
  which tags nrw would give what it creates;
- whether `runs/` and `samples/` exist yet, and how many records they hold.

It cannot prove that the tags will be accepted for a write without writing,
and it says so. Reporting a configuration is not testing a connection (the
2026-08-17 entry).

### What stays the same

Apply, adopt, the rendering of `sample.md` and `nrw sample new` all go
through `CatalogStore` already, so a Tiled catalog asks nothing new of them.
Step 1 changes what they render and read, in both stores alike. The agent
guard already refuses every command that writes the catalog (`assign`,
`release`, and `apply`, `adopt` or `settings` with `--write`); its one new
rule is the login's. Reads stay allowed under the agent: they send the
credential only to `TILED_URL`.

## Steps

Each step is one commit, with its tests, and leaves the whole suite green.

1. **The experiment's own context, in both stores.** Done, e06203c
   (2026-10-20).
2. **The sign of new data** (for any source, nothing of Tiled yet). Done
   (2026-10-21).
   - On every poll, and with no file read: per sample, the runs apply would
     copy and the copied files reduced again; overall, the complete runs no
     sample holds. Apply's own rules decide what it would copy, so the sign
     and the Review cannot disagree.
   - The page shows the totals beside **Apply**, each sample's count in the
     list, and the Review as the way on.
   - Tests:
     - a new run of a sample counted, then not, once applied;
     - a re-reduced file counted as reduced again;
     - an excluded or unconfirmed run not counted as ready;
     - a run in no sample counted apart;
     - the sign agreeing with the Review's plan on the same snapshot;
     - the page showing it, in the browser tests.
3. **Settings.**
   - `SOURCE_OPTIONS` and `CATALOG_OPTIONS` offer `"tiled"`, the catalog's in
     place of `"api"`.
   - `EXPERIMENT_KEYS` gains `experiment.catalog` (`kind`, `location`); a
     source's `location` is a URL when its kind is `tiled`, held to the rules
     above, and its default is the experiment's `autoreduction/`.
   - `project/settings.py` gets a validated branch for each key.
   - `env.py` knows `TILED_URL` and `TILED_API_KEY`, the key secret.
   - Tests:
     - `nrw init --check` is clean after a save that sets either;
     - a default edited in a comment is reported;
     - every location rule is enforced;
     - a change of a kind or a location is applied, not dropped;
     - the key is never printed.
4. **A Tiled client** (`nr_workbench/tiled.py`, with httpx imported inside
   functions so that `nrw --help` stays instant).
   - The calls nrw needs and no more: read a node, list a container's
     children with their metadata and data sources, create a container,
     patch a node's metadata, delete a node, read an asset's bytes (with an
     `ETag` to ask "still this?"), and say whose a credential is.
   - Every request has a deadline: connect and read timeouts, and one for a
     whole listing. Each error names the server, with the server's own
     message cut to one line before it is shown.
   - `pyproject.toml` declares httpx, which aure already brings.
   - Tests use a small in-process stand-in for Tiled (`httpx.MockTransport`)
     that reproduces the quirks above: 409 for an existing key, a bare 500 and
     a dropped connection for a failed `test`, `next` links without the query,
     and the asset id that bytes need.
5. **Logging in** (`nrw tiled login`, `nrw tiled logout`).
   - The device-code login, the session file, and renewing the session in
     every process, as described above.
   - The guard, and the commands themselves, refuse both under the agent.
   - Tests, against the stand-in:
     - a login that is approved, one that expires, and one refused;
     - `~/.nrw-tiled` readable by its owner only, replaced whole, and a link
       at it refused;
     - an expired access token renewed, and a 401 renewed once and retried
       once;
     - two processes renewing at once, both still logged in;
     - logout ending the session on the server;
     - no token or key ever printed, and nothing sent to a host other than
       `TILED_URL`'s, whatever the server's answers name.
6. **The data from Tiled** (`TiledDataSource`).
   - `inventory`, `read_bytes` and `probe`, as described above, opened by
     `open_source` for `kind = "tiled"`.
   - The Review asks again for each copied file by its `ETag`, so a rewrite
     at the same size is caught before an apply.
   - Tests:
     - the apply tests run against both sources: copied whole or not at all,
       never overwriting, a re-reduction noticed, a hand-edited copy noticed;
     - names refused unless exactly canonical, and never renamed;
     - a file changed between listing and reading refused;
     - headers read once per version, and settling from the server's clock;
     - a source that cannot be reached is an inventory with the reason, never
       an exception;
     - the folder feed announcing runs as they appear on Tiled.
7. **Records.**
   - Converting each of the three records to metadata and back, with its
     spec and version; the checks on reading; and the match between key and
     record. The parquet store's row helpers move into the same module
     (`experiment/records.py`), so a record reads the same from either store.
   - Pure functions, tested without a server.
8. **The store** (`TiledCatalogStore`).
   - `load`, `load_report`, `update`, `describe`, `exists` and `version`, as
     described above. It refuses a project with local records, a location
     on another host, and untagged writes. It leaves alone every node that
     is not one of its records.
   - `open_store` takes the catalog's configuration. Called as it is today,
     it opens the parquet store as it does today.
   - Tests:
     - the semantic tests in `tests/test_experiment_store.py` run against both
       stores: round trip, unknown fields surviving, a stale edit refused
       with nothing written, another record's edit merged, an unchanged save
       writing nothing, and many threads losing nothing;
     - the parquet-only tests (manifest, crash recovery, git unmerged) stay
       as they are;
     - Tiled's own cases: creating `context/` and its children with their
       parents' tags, the create race, telling the 500 apart, an edit that
       stops partway, another application's record edited and kept whole,
       and a bad record or a spec-less node left untouched.
9. **Wiring.**
   - `Workspace` and `render.load_catalog` open the configured store, and
     the configured source as they do now.
   - `nrw experiment status` prints the store's description instead of
     `experiment/`; the parquet line reads as it does today.
   - The page's version comes from the store.
   - The Settings page can choose the source and the catalog, shows a
     location for Tiled, and runs the checks described above.
   - Tests: the web and command-line paths for every kind, and the Settings
     page in the browser tests.
10. **Contract tests against a real Tiled 0.2.18.**
    - The store and source tests run again, against a server started with
      `uvx --from "tiled[server]==0.2.18" tiled serve ...` in an environment
      of its own, so nothing of Tiled's enters nrw's. For the source, a folder
      of reduced files is registered with it as external assets, as on
      tiled.ornl.gov.
    - Renewing and ending a session run against it too, after a password
      login on the local server. The Globus part itself can only be tried
      live, in step 13.
    - One server runs Tiled's tag policy with two members of one experiment.
      What one writes in `context/`, the other can read and edit, and a node
      written without tags is invisible to the other. That is the trap the
      "never untagged" rule exists for.
    - Skipped, with the reason, where `uv` or the network is missing. These
      tests catch Tiled changing under us, which `api_version` 0 allows.
11. **Documentation.**
    - `docs/experiment-sources.md`: the Tiled source, with what it lists and
      how its versions work; and its metadata-service section becomes the
      Tiled store, covering the layout, the schema for other applications,
      and the write protocol and its limits.
    - `docs/experiment.md`: the sign of new data; and putting a new
      experiment's data and catalog on Tiled, from `nrw tiled login` through
      the settings to the checks.
    - `docs/ground_truths.md`: whatever implementation turns up.
12. **Review.** The design, security and test reviewers, with their findings
    fixed. As before, each deliberately reintroduced bug must fail a test.
13. **A live check against tiled.ornl.gov.** You run `nrw tiled login`,
    which is the first test of the Globus login itself. Then a read-only
    check (`--check`) of IPTS-37740, and its data synced into a scratch
    project from Tiled, which writes nothing there. The first write to Tiled,
    the catalog's, happens only on your go-ahead.

## Size

Rough line counts, at this codebase's comment density. Steps 1 and 2 are
what they came to. Both came to about three times their estimates (350 and
300; 250 and 300), most of it from what their reviews found. The rest are
estimates raised by about a third, which those two say is too little: expect
them to grow the same way.

| Step | Code | Tests |
|---|---|---|
| 1. The experiment's own context (built) | about 1,050 | about 1,150 |
| 2. The sign of new data (built) | about 650 | about 950 |
| 3. Settings | 250 | 250 |
| 4. Tiled client | 350 | 400, with the stand-in |
| 5. Logging in | 250 | 250 |
| 6. The data from Tiled | 450 | 550 |
| 7. Records | 150 | 150 |
| 8. The store | 400 | 350 |
| 9. Wiring | 250 | 250 |
| 10. Contract tests | — | 450 |
| **Steps 3 to 10** | **about 2,300** | **about 2,700** |

With about 450 lines of documentation, steps 3 to 13 come to about 5,500
lines by these estimates. With steps 1 and 2, the whole plan is about the
size of the shared catalog withdrawn on 2026-09-28. It is three pieces, each
of use on its own, and each a place to stop:

- **The sign of new data** (step 2, built: about 1,600 lines with its tests).
  Useful today, with the folder.
- **The data from Tiled** (steps 3 to 6, and their parts of 9 to 13, about
  3,300). The usual case: work from any machine, with what the facility put
  on Tiled. This is the one the login is for.
- **The catalog on Tiled** (steps 7 and 8, and the rest, about 2,200). Shared
  between everyone on the experiment, and other applications.

Three pieces go beyond the bare minimum. Each could wait, at a cost:

- **Choosing the source and the catalog on the Settings page** (about 200
  lines). Until then they are set with `nrw experiment settings`, and the page
  shows them read-only.
- **The two-user contract test** (about 150). Until then, that colleagues can
  see the records is checked only by hand, on tiled.ornl.gov.
- **The checks** (about 300 with their tests). Until then, a refused login or
  tag shows up only at the first edit.

The last two guard against failures that would otherwise give no sign at all.

## Questions

**Answered on 2026-10-20:**

- nrw creates `context/` when it does not exist.
- Members can create records there.
- `IPTS-<n>/` is never rebuilt.
- People probably cannot mint their own API keys in production, so nrw keeps
  the Globus session itself.
- No application writes in `context/` yet. nrw is the first, and the layout
  and tables above are the schema.
- The experiment's record holds the four fields above and no others. A
  proposal's text, if it comes, is a field of that record.

**Answered on 2026-10-21:**

- Another process populates each experiment's container with its data. nrw
  reads it from there, and copies it where the analysis reads it, rather than
  anyone copying it by hand.
- The page says when there is new data, counting the runs ready to copy and
  the runs no sample holds yet. Syncing is a person's apply.
- What the container holds was looked at, read-only, after a Globus login:
  `docs/ground_truths.md`, 2026-10-21.

**Not known:**

- tiled.ornl.gov's session lifetimes. At Tiled's defaults, a session used at
  least weekly lasts a year; the server's operators may have set them
  shorter. Nothing depends on it. The check reports when the session must
  next be renewed, and the docs will point to that rather than promise a
  number.
- Whether the process populating `autoreduction/` registers a file again
  when it is reduced again. If not, a rewrite at the same size shows only
  when the Review asks for the file by its `ETag` (step 6). That process
  could record each file's time in its node's metadata, which would make the
  listing exact.
- Whether experiment members own the `isaac` tag, which every node nrw creates
  in `context/` must carry. The live check finds out; if not, nrw needs to be
  told which tag to use.

## Not in this plan

- **Copying an existing parquet catalog to Tiled.** New experiments only,
  for now.
- **Syncing by itself.** The sign says what an apply would copy; nothing is
  copied until a person applies.
- **The NeXus files** in `nexus/`. nrw reads reduced data only.
- **Fit results on Tiled.** A plan of its own: which fits, what each record
  holds, and how it relates to the ISAAC push.
- **Logging in from the Settings page.** In this plan the login is a
  command. When there is no session, or it has expired, the page says to run
  it.
- **Who made each change.** Tiled's revisions record when, not who. With
  other applications writing, which one wrote a record may matter as much as
  who did; an `updated_by` field is worth agreeing on with them.
- **Moving back.** Switching a project from Tiled to parquet copies nothing
  back.
