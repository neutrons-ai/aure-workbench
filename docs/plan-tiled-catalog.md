# Plan: the experiment catalog on Tiled

> **A design record, kept for its reasoning.** What the code does now is in
> the code, `docs/experiment-sources.md` and `docs/ground_truths.md`; where
> they disagree with this plan, they are right.

**Status:** in progress. Step 1 (the experiment's own context) was built and
reviewed on 2026-10-20; its review changed the marker format and the parquet
store's recovery (`docs/ground_truths.md`, same date). Steps 2 to 11 are not
started. This is the first part of the Tiled work, and the only part planned
here (see "Not in this plan").

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

**Only the metadata.** Another process creates each experiment's container on
Tiled and catalogues its reduced data there. nrw does not write that data,
and in this plan does not read it either: the data still comes from the data
mount (`[experiment.source] kind = "local"`), and new runs are still noticed
when their files appear there. Both sides know a run by the same thing, its
run number.

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
the Tiled part of this plan (steps 2 to 11) to about 3,650, of which about
1,500 are code.

## What the server gives us

Checked on 2026-10-20; the details and how each was found are in
`docs/ground_truths.md` under the same date.

- tiled.ornl.gov runs Tiled 0.2.18, `api_version` 0: no stable API. Every
  data route answers 401 without a login. That includes the example
  experiment, `projects/isaac/IPTS-37740/`, so its layout has not been seen
  from here yet.
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
written (`prose_for`), for every store. That leaves step 6 a choice for text
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

1. **The experiment's own context, in both stores** (nothing of Tiled yet).
   - `ExperimentContext` in `experiment/model.py`, with its rules, edited
     through `apply_changes` with a revision check.
   - The parquet store's third table, `experiment.parquet`.
   - The Experiment page edits the record above the samples.
   - `prose_for` writes it into each managed `sample.md`, marked as the
     experiment's, and `adopt` leaves the marked text out of a sample's
     fields.
   - Tests:
     - the record's rules, and a stale edit refused;
     - the parquet round trip, and a catalog written before this step read
       as one with an empty experiment record;
     - every managed `sample.md` carrying the text in the right sections,
       and due to be applied when it changes;
     - `adopt` not pulling it into a sample;
     - the page editing it, in the browser tests.
2. **Settings.**
   - `CATALOG_OPTIONS` offers `"tiled"`, in place of `"api"`.
   - `EXPERIMENT_KEYS` gains `experiment.catalog` (`kind`, `location`), and
     `experiment/config.py` resolves the location with the rules above.
   - `project/settings.py` gets a validated branch for each key.
   - `env.py` knows `TILED_URL` and `TILED_API_KEY`, the key secret.
   - Tests:
     - `nrw init --check` is clean after a save that sets the catalog;
     - a default edited in a comment is reported;
     - every location rule is enforced;
     - a change of `catalog.kind` or `catalog.location` is applied, not
       dropped;
     - the key is never printed.
3. **A Tiled client** (`nr_workbench/tiled.py`, with httpx imported inside
   functions so that `nrw --help` stays instant).
   - The calls the store needs and no more: read a node, list a container's
     children with their metadata, create a container, patch a node's
     metadata, delete a node, and say whose a credential is.
   - Every request has a deadline: connect and read timeouts, and one for a
     whole listing. Each error names the server, with the server's own
     message cut to one line before it is shown; the store raises it as a
     `CatalogError`.
   - `pyproject.toml` declares httpx, which aure already brings.
   - Tests use a small in-process stand-in for Tiled (`httpx.MockTransport`)
     that reproduces the quirks above: 409 for an existing key, a bare 500 and
     a dropped connection for a failed `test`, and `next` links without the
     query.
4. **Logging in** (`nrw tiled login`, `nrw tiled logout`).
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
5. **Records.**
   - Converting each of the three records to metadata and back, with its
     spec and version; the checks on reading; and the match between key and
     record.
   - Pure functions, tested without a server. They use the same field table
     as the parquet rows, so a record reads the same from either store.
6. **The store** (`TiledCatalogStore`).
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
7. **Wiring.**
   - `Workspace` and `render.load_catalog` open the configured store.
   - `nrw experiment status` prints the store's description instead of
     `experiment/`; the parquet line reads as it does today.
   - The page's version comes from the store.
   - The Settings page can choose the catalog, shows a location field for
     Tiled, and runs the check described above.
   - Tests: the web and command-line paths for both stores, and the Settings
     page in the browser tests.
8. **Contract tests against a real Tiled 0.2.18.**
   - The store tests of step 6 run again, against a server started with
     `uvx --from "tiled[server]==0.2.18" tiled serve ...` in an environment
     of its own, so nothing of Tiled's enters nrw's.
   - Renewing and ending a session run against it too, after a password
     login on the local server. The Globus part itself can only be tried
     live, in step 11.
   - One server runs Tiled's tag policy with two members of one experiment.
     What one writes in `context/`, the other can read and edit, and a node
     written without tags is invisible to the other. That is the trap the
     "never untagged" rule exists for.
   - Skipped, with the reason, where `uv` or the network is missing. These
     tests catch Tiled changing under us, which `api_version` 0 allows.
9. **Documentation.**
   - `docs/experiment-sources.md`: its metadata-service section becomes the
     Tiled store, covering the layout, the schema for other applications,
     and the write protocol and its limits.
   - `docs/experiment.md`: the experiment's own context; and putting a new
     experiment's catalog on Tiled, from `nrw tiled login` through the
     setting to the check.
   - `docs/ground_truths.md`: whatever implementation turns up.
10. **Review.** The design, security and test reviewers, with their findings
    fixed. As before, each deliberately reintroduced bug must fail a test.
11. **A live check against tiled.ornl.gov.** You run `nrw tiled login`,
    which is the first test of the Globus login itself. Then a read-only
    check (`--check`) of an experiment such as IPTS-37740. The first write
    happens only on your go-ahead.

## Size

Rough line counts, at this codebase's comment density:

| Step | Code | Tests |
|---|---|---|
| 1. The experiment's own context | 350 | 300 |
| 2. Settings | 200 | 200 |
| 3. Tiled client | 300 | 350, with the stand-in |
| 4. Logging in | 250 | 250 |
| 5. Records | 150 | 150 |
| 6. The store | 400 | 350 |
| 7. Wiring | 200 | 200 |
| 8. Contract tests | — | 300 |
| **Total** | **about 1,850** | **about 2,100** |

With about 350 lines of documentation, that is about 4,300 lines, half the
withdrawn shared catalog. It has two parts that could be split:

- **Step 1 stands on its own.** It is a catalog feature, useful with the
  parquet store alone (about 650 lines). Built first, it means the Tiled
  store is written for the whole catalog from the start, rather than
  growing a third kind of record later.
- **Steps 2 to 11 are Tiled** (about 3,650).

The login is needed, since Globus is how people sign in. Three other pieces
go beyond the bare minimum. Each could wait, at a cost:

- **Choosing the catalog on the Settings page** (about 150 lines). Until
  then it is set with `nrw experiment settings`, and the page shows it
  read-only.
- **The two-user contract test** (about 150). Until then, that colleagues can
  see the records is checked only by hand, on tiled.ornl.gov.
- **The check** (about 250 with its tests). Until then, a refused login or
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

**Not known:** tiled.ornl.gov's session lifetimes. At Tiled's defaults, a
session used at least weekly lasts a year; the server's operators may have
set them shorter. Nothing depends on it. The check reports when the session
must next be renewed, and the docs will point to that rather than promise a
number.

## Not in this plan

- **Copying an existing parquet catalog to Tiled.** New experiments only,
  for now.
- **Reduced data and run announcements from Tiled.** Others catalogue the
  data. Whether nrw should ever read it from there is a later decision, and
  needs a look at its layout.
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
