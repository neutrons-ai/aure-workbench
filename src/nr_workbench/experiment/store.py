"""Where the catalog is kept: parquet tables and a manifest, in ``experiment/``.

``runs.parquet`` holds one row per run and ``samples.parquet`` one row per
sample -- the split data-assembler's lakehouse uses, where a measurement row
carries a nullable sample id and samples are their own table.
``experiment.parquet`` holds the experiment's own context, one row, and is
written only once that has been set: a project that never sets it keeps the
two files it always had. ``catalog.json`` records the sha256 of every table,
is written *last* on every save, and is how an interrupted save is told apart
from a complete one.

Four failure modes decide how this is written, each of them silent if handled
the obvious way:

**A file that exists but will not parse is not an empty catalog.** It raises
:class:`CatalogCorruptError`, which is deliberately *not* a ``ValueError``:
pyarrow's ``ArrowInvalid`` is one, and the house pattern ``except (OSError,
ValueError)`` would turn a damaged catalog into an empty one -- whose next save
would then replace every decision the experimenter made with nothing.

**A binary merge conflict has no conflict markers.** Git leaves "ours" in the
working tree, which loads perfectly. Load and save are refused while git
reports ``experiment/`` unmerged.

**Two writers.** A browser tab and a terminal can edit at once. Every update is
a read-modify-write under a lock, and checks each edited record's revision, so
a concurrent edit to a *different* record is merged and one to the *same*
record is refused.

**An interrupted save.** The tables are replaced one after another; a crash
between them leaves a set that parses. The manifest catches that, and the
previous complete set is kept under ``.nrw/cache/experiment/`` to recover
from -- every table of it, so that recovery shows what the last load showed.

pyarrow is imported inside the functions that use it: it costs about a second,
and ``nrw --help`` must stay instant.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import threading
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple, Protocol

from nr_workbench.experiment.model import (
    EXPERIMENT_FIELDS,
    Catalog,
    ExperimentChange,
    ExperimentContext,
    RunChange,
    RunEntry,
    RunKey,
    SampleChange,
    SampleContext,
    apply_changes,
)
from nr_workbench.fsutil import advisory_lock, atomic_write_bytes
from nr_workbench.problems import Problem
from nr_workbench.provenance.hashing import sha256_file_or_none

#: Written into every table and the manifest.
SCHEMA_ID = "nrw-experiment/1"

#: Bumped when a change would make an older nrw misread the tables. An older
#: nrw refuses a newer catalog rather than dropping what it cannot read.
SCHEMA_VERSION = 1

RUNS_FILE = "runs.parquet"
SAMPLES_FILE = "samples.parquet"
EXPERIMENT_FILE = "experiment.parquet"
MANIFEST_FILE = "catalog.json"

#: Every table, by its name in the manifest. The runs and samples are always
#: written and listed; the experiment's context only once it has been set,
#: and an nrw from before it never lists it.
_TABLES = {"runs": RUNS_FILE, "samples": SAMPLES_FILE, "experiment": EXPERIMENT_FILE}
_OPTIONAL = frozenset({"experiment"})

#: Columns this version reads. Anything else is preserved, untouched.
_RUN_COLUMNS = (
    "run",
    "kind",
    "sample_id",
    "measurement",
    "condition",
    "include",
    "note",
    "title",
    "start_time",
    "rev",
    "updated_at",
)
_SAMPLE_COLUMNS = (
    "sample_id",
    "title",
    "description",
    "details",
    "mounting",
    "measurement_conditions",
    "fits_to_perform",
    "rev",
    "updated_at",
)
_EXPERIMENT_COLUMNS = (*EXPERIMENT_FIELDS, "rev", "updated_at")


class CatalogError(Exception):
    """The catalog cannot be used as it is. The message says what to do.

    Not a ``ValueError``, so that no ``except ValueError`` written for bad
    *requests* can mistake a damaged catalog for a client error or an empty
    one.
    """


class CatalogCorruptError(CatalogError):
    """A catalog file exists but cannot be read."""


class CatalogUnmergedError(CatalogError):
    """Git reports the catalog as unmerged."""


class CatalogVersionError(CatalogError):
    """The catalog was written by a newer nrw."""


class CatalogStore(Protocol):
    """Where the organization is kept.

    The parquet store below is the only one today. A facility service that
    serves the same tables is the one this shape is for: ``update`` sends edits
    rather than a whole catalog, so the service can merge concurrent edits by
    record exactly as the local store does.
    """

    def describe(self) -> dict[str, Any]:
        """What the page and ``nrw experiment status`` say about the store."""
        ...

    def load(self) -> Catalog:
        """The current catalog; empty when none has been written yet."""
        ...

    def load_report(self) -> tuple[Catalog, tuple[Problem, ...]]:
        """The catalog and what reading it recovered from, together.

        Returned together, not kept on the store: the web server shares one
        store across request threads, and a problem remembered from "the last
        load" would be another thread's load by the time it was asked for.
        """
        ...

    def update(
        self,
        *,
        runs: Iterable[RunChange] = (),
        samples: Iterable[SampleChange] = (),
        experiment: ExperimentChange | None = None,
        now: str | None = None,
    ) -> Catalog:
        """Apply edits against the latest catalog and store the result."""
        ...


class CatalogUnavailableError(CatalogError):
    """The configured kind of catalog store is not available."""


def open_store(root: Path, kind: str = "parquet") -> ParquetCatalogStore:
    """The catalog store ``[experiment.catalog] kind`` names.

    Raises:
        CatalogUnavailableError: For a planned or unknown kind. Deliberately not
            a fallback to the local parquet store: edits meant for a facility
            service would be written into the project instead, where nobody
            else would see them.
    """
    if kind == "parquet":
        return ParquetCatalogStore.for_project(root)
    if kind == "api":
        raise CatalogUnavailableError(
            '[experiment.catalog] kind = "api" is planned but not implemented '
            "yet; see docs/experiment-sources.md. The catalog is not editable "
            'until it is, or until kind = "parquet".'
        )
    raise CatalogUnavailableError(
        f'[experiment.catalog] kind = {kind!r} is not known. Known: "parquet".'
    )


#: One lock per catalog directory per process, for the web server's threads.
#: The file lock alone does not serialize them: flock is per open file
#: description, and it is a no-op where fcntl is missing.
_THREAD_LOCKS: dict[Path, threading.Lock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()


def _thread_lock(directory: Path) -> threading.Lock:
    with _THREAD_LOCKS_GUARD:
        return _THREAD_LOCKS.setdefault(directory.resolve(), threading.Lock())


class _Loaded(NamedTuple):
    """One read of the catalog, and what the save that follows must know."""

    catalog: Catalog
    problems: tuple[Problem, ...]
    #: An experiment table in ``experiment/`` that the read left alone because
    #: it came from an interrupted save.
    partial_experiment: bool


class ParquetCatalogStore:
    """The catalog as ``experiment/*.parquet`` in the project.

    Args:
        directory: The committed catalog directory, ``<root>/experiment``.
        cache_dir: Where temp files, the lock and the recovery copies go.
            Must be gitignored and on the same filesystem as ``directory``.
        git_root: The project root, for the unmerged check. ``None`` skips it.
    """

    def __init__(
        self, directory: Path, cache_dir: Path, *, git_root: Path | None = None
    ) -> None:
        self.directory = Path(directory)
        self.cache_dir = Path(cache_dir)
        self.git_root = git_root

    @classmethod
    def for_project(cls, root: Path) -> ParquetCatalogStore:
        """The store for a project, at its standard locations."""
        from nr_workbench.project.layout import ProjectLayout

        layout = ProjectLayout(root=Path(root))
        return cls(
            layout.experiment_dir, layout.experiment_cache_dir, git_root=layout.root
        )

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def describe(self) -> dict[str, Any]:
        """Where the catalog is and whether one has been written."""
        return {
            "kind": "parquet",
            "directory": self.directory.name,
            "exists": self.exists(),
        }

    def exists(self) -> bool:
        """Whether any catalog file has been written."""
        return any(
            (self.directory / name).exists()
            for name in (*_TABLES.values(), MANIFEST_FILE)
        )

    def load(self) -> Catalog:
        """Read the catalog.

        Returns:
            The catalog. Empty when nothing has been written yet -- and only
            then.

        Raises:
            CatalogCorruptError: A file exists but cannot be read, and no
                complete earlier version could be recovered.
            CatalogUnmergedError: Git reports ``experiment/`` unmerged.
            CatalogVersionError: The catalog was written by a newer nrw.
        """
        return self.load_report()[0]

    def load_report(self) -> tuple[Catalog, tuple[Problem, ...]]:
        """Read the catalog, and say what reading it recovered from.

        Taken under the same locks as a save. A save replaces its files one
        after another, and a read landing between two of them would see a
        manifest that does not match its tables -- and report an interrupted
        save that is merely in progress.

        Raises:
            CatalogError: As for :meth:`load`.
        """
        with self._reading():
            loaded = self._load_unlocked()
        return (loaded.catalog, loaded.problems)

    @contextmanager
    def _reading(self) -> Iterator[None]:
        """The save lock, for a read. Skipped where it cannot be taken.

        A catalog on a read-only mount -- a colleague's project, opened to look
        -- has nowhere to put the lock file. Refusing to read it would be
        worse than the race the lock guards against.
        """
        with _thread_lock(self.directory):
            try:
                context = advisory_lock(self.cache_dir / "catalog")
                context.__enter__()
            except OSError:
                yield
                return
            try:
                yield
            finally:
                context.__exit__(None, None, None)

    def _load_unlocked(self) -> _Loaded:
        problems: list[Problem] = []
        self._refuse_if_unmerged()
        if not self.exists():
            return _Loaded(Catalog(), (), partial_experiment=False)

        manifest = self._read_manifest(self.directory / MANIFEST_FILE)
        # Where the tables are read from, and the manifest that lists them: the
        # catalog's own, or the kept set when the last save was interrupted.
        source, listing = self.directory, manifest
        if manifest is not None and not _matches(manifest, self.directory):
            recovered = self._recover(manifest)
            if recovered is None:
                raise CatalogCorruptError(self._unrecoverable(manifest))
            source, listing = recovered
            problems.append(
                Problem(
                    "catalog",
                    "The last save of the catalog was interrupted. Showing the "
                    "last complete version; the next save replaces the partial "
                    "files.",
                )
            )
        elif manifest is None:
            problems.append(
                Problem(
                    "catalog",
                    f"{self.directory.name}/{MANIFEST_FILE} is missing, so an "
                    "interrupted save could not be detected. The tables are used "
                    "as they are; the next save writes the manifest.",
                )
            )

        experiment_path = source / EXPERIMENT_FILE
        if (
            listing is not None
            and "experiment" not in _tables(listing)
            and experiment_path.exists()
        ):
            problems.append(
                Problem(
                    "catalog",
                    f"{self.directory.name}/{EXPERIMENT_FILE}, the experiment's "
                    "context, is not in the manifest, so whether it belongs with "
                    "the other tables cannot be checked: an older nr-workbench "
                    "may have saved the catalog since, a merge may have kept one "
                    "side's catalog.json, or a save may have been interrupted. It "
                    "is used as it is, so check the experiment's context on the "
                    "Experiment page; the next save lists it again.",
                )
            )

        runs = _read_table(source / RUNS_FILE, "runs")
        samples = _read_table(source / SAMPLES_FILE, "samples")
        experiment = _read_table(experiment_path, "experiment")
        if len(experiment) > 1:
            raise CatalogCorruptError(
                f"{EXPERIMENT_FILE} holds {len(experiment)} rows; the experiment "
                "has one context. Restore it from git "
                f"(`git checkout -- {self.directory.name}/{EXPERIMENT_FILE}`)."
            )
        catalog = Catalog(
            runs={entry.key: entry for entry in map(_run_from_row, runs)},
            samples={
                entry.sample_id: entry for entry in map(_sample_from_row, samples)
            },
            experiment=(
                _experiment_from_row(experiment[0])
                if experiment
                else ExperimentContext()
            ),
        )
        # A table the recovered set does not hold is the interrupted save's:
        # never read, and removed by the next save, so that it cannot come back.
        partial = (
            source != self.directory
            and not experiment_path.exists()
            and (self.directory / EXPERIMENT_FILE).exists()
        )
        return _Loaded(catalog, tuple(problems), partial_experiment=partial)

    def _unrecoverable(self, manifest: dict[str, Any]) -> str:
        """What to do about a catalog that matches neither its manifest nor a kept set."""
        name = self.directory.name
        if (
            "experiment" in _tables(manifest)
            and not (self.directory / EXPERIMENT_FILE).exists()
        ):
            # The manifest is written last, so this is no interrupted save: the
            # file was saved and never reached here -- committed without it.
            return (
                f"{name}/{MANIFEST_FILE} lists {name}/{EXPERIMENT_FILE}, the "
                "experiment's context, but the file is not here. If it was never "
                f"committed, whoever saved it should run `git add {name}/"
                f"{EXPERIMENT_FILE}` and commit it; otherwise restore it from git. "
                "nrw will not save over the catalog until then."
            )
        return (
            f"{name}/ does not match its own manifest, and no complete earlier "
            "version is available. A save was probably interrupted, or a file was "
            f"replaced by hand. Restore it from git (`git checkout -- {name}/`) or "
            "from a backup; nrw will not save over it until then."
        )

    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------

    def update(
        self,
        *,
        runs: Iterable[RunChange] = (),
        samples: Iterable[SampleChange] = (),
        experiment: ExperimentChange | None = None,
        now: str | None = None,
    ) -> Catalog:
        """Apply edits to the latest catalog, under the lock, and store it.

        Args:
            runs: Edits to run records.
            samples: Edits to sample records.
            experiment: An edit to the experiment's own context.
            now: Timestamp for changed records; the current UTC time if omitted.

        Returns:
            The catalog as stored.

        Raises:
            CatalogError: The catalog cannot be used as it is.
            CatalogValidationError: An edit breaks a rule.
            RecordConflict: A record changed since the editor loaded it.
        """
        stamp = now or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        run_changes, sample_changes = list(runs), list(samples)

        with _thread_lock(self.directory), advisory_lock(self.cache_dir / "catalog"):
            loaded = self._load_unlocked()
            updated = apply_changes(
                loaded.catalog,
                runs=run_changes,
                samples=sample_changes,
                experiment=experiment,
                now=stamp,
            )
            if _same(loaded.catalog, updated):
                return loaded.catalog
            self._write(updated, stamp, partial_experiment=loaded.partial_experiment)
            return updated

    def _write(
        self, catalog: Catalog, stamp: str, *, partial_experiment: bool = False
    ) -> None:
        """Write the tables and then the manifest, keeping the last good set.

        Args:
            catalog: The catalog to store.
            stamp: When.
            partial_experiment: The load before this found an experiment table
                left by an interrupted save, and did not read it. Unless this
                save writes one, it is removed: listed by no manifest, it would
                otherwise be read back as an older nrw's.
        """
        runs_bytes = _table_bytes(
            "runs",
            _RUN_COLUMNS,
            [_run_row(entry) for key, entry in sorted(catalog.runs.items())],
        )
        samples_bytes = _table_bytes(
            "samples",
            _SAMPLE_COLUMNS,
            [_sample_row(catalog.samples[k]) for k in sorted(catalog.samples)],
        )
        tables: dict[str, Any] = {
            "runs": {
                "file": RUNS_FILE,
                "sha256": _sha(runs_bytes),
                "rows": len(catalog.runs),
            },
            "samples": {
                "file": SAMPLES_FILE,
                "sha256": _sha(samples_bytes),
                "rows": len(catalog.samples),
            },
        }
        writes = [(RUNS_FILE, runs_bytes), (SAMPLES_FILE, samples_bytes)]
        # Only once it has been set: a project that never describes its
        # experiment keeps exactly the files it always had.
        if catalog.experiment.rev:
            experiment_bytes = _table_bytes(
                "experiment",
                _EXPERIMENT_COLUMNS,
                [_experiment_row(catalog.experiment)],
            )
            tables["experiment"] = {
                "file": EXPERIMENT_FILE,
                "sha256": _sha(experiment_bytes),
                "rows": 1,
            }
            writes.append((EXPERIMENT_FILE, experiment_bytes))
        manifest = {
            "schema": SCHEMA_ID,
            "schema_version": SCHEMA_VERSION,
            "updated": stamp,
            "tables": tables,
        }

        self.directory.mkdir(parents=True, exist_ok=True)
        self._keep_previous()
        if partial_experiment and not catalog.experiment.rev:
            (self.directory / EXPERIMENT_FILE).unlink(missing_ok=True)
        for name, data in writes:
            _replace(self.directory / name, data, self.cache_dir)
        _replace(
            self.directory / MANIFEST_FILE,
            (json.dumps(manifest, indent=2) + "\n").encode("utf-8"),
            self.cache_dir,
        )

    def _keep_previous(self) -> None:
        """Copy the current complete set aside, for :meth:`_recover`.

        Only a set that matches its own manifest is worth keeping: copying a
        half-written one over the last good copy would destroy the one thing
        recovery needs. Every table is kept, listed or not -- an experiment
        table an older nrw no longer lists is still what a load reads -- so a
        recovered catalog is the one the last load showed.
        """
        manifest = self._read_manifest(self.directory / MANIFEST_FILE)
        if manifest is None or not _matches(manifest, self.directory):
            return
        previous = self.cache_dir / "previous"
        previous.mkdir(parents=True, exist_ok=True)
        for name in (*_TABLES.values(), MANIFEST_FILE):
            path = self.directory / name
            if path.exists():
                shutil.copy2(path, previous / name)
            else:
                (previous / name).unlink(missing_ok=True)

    def _recover(self, manifest: dict[str, Any]) -> tuple[Path, dict[str, Any]] | None:
        """The kept set, but only if it is exactly what *manifest* describes.

        Being internally consistent is not enough: the kept copy could be from
        an older save, and restoring that silently would undo edits nobody
        was told about. The manifest in ``experiment/`` is written last, so
        when it still names the kept set's hashes, that set is precisely the
        last complete catalog -- and when it does not, nobody can say which
        version is right, and a person has to.

        Returns:
            The directory holding the kept set, and its manifest; or ``None``
            when no kept set can be trusted.
        """
        previous = self.cache_dir / "previous"
        try:
            kept = self._read_manifest(previous / MANIFEST_FILE)
        except CatalogError:
            return None
        if kept is None or not _matches(kept, previous):
            return None
        if _hashes(kept) != _hashes(manifest):
            return None
        return (previous, kept)

    # ------------------------------------------------------------------
    # Checks
    # ------------------------------------------------------------------

    def _read_manifest(self, path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CatalogCorruptError(
                f"{path.name} cannot be read ({exc}). Restore it from git."
            ) from exc
        if not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA_ID:
            raise CatalogCorruptError(f"{path.name} is not an nrw experiment manifest.")
        version = manifest.get("schema_version")
        if isinstance(version, bool) or not isinstance(version, int):
            raise CatalogCorruptError(f"{path.name} has no usable schema_version.")
        if version > SCHEMA_VERSION:
            raise CatalogVersionError(
                f"The catalog was written by a newer nr-workbench (schema "
                f"version {version}; this one reads {SCHEMA_VERSION}). Upgrade "
                "before editing it -- an older version would drop what it "
                "cannot read."
            )
        return manifest

    def _refuse_if_unmerged(self) -> None:
        if self.git_root is None:
            return
        from nr_workbench.project.vcs import run_git

        try:
            relative = self.directory.resolve().relative_to(
                Path(self.git_root).resolve()
            )
        except ValueError:
            return
        output = run_git(
            Path(self.git_root), ["ls-files", "-u", "--", relative.as_posix()]
        )
        if output and output.strip():
            raise CatalogUnmergedError(
                f"Git reports {relative.as_posix()}/ as unmerged. Parquet files "
                "cannot be merged line by line, so git leaves one side in place "
                "without conflict markers -- it would load as if nothing had "
                "happened. Pick one side (`git checkout --ours` or `--theirs`), "
                "re-apply the other side's edits on the experiment page, and "
                "commit, before editing here."
            )


# ---------------------------------------------------------------------------
# Rows
# ---------------------------------------------------------------------------


def _same(before: Catalog, after: Catalog) -> bool:
    """Whether an update changed any record's revision (and so its content)."""
    return (
        {k: e.rev for k, e in before.runs.items()}
        == {k: e.rev for k, e in after.runs.items()}
        and {k: e.rev for k, e in before.samples.items()}
        == {k: e.rev for k, e in after.samples.items()}
        and before.experiment.rev == after.experiment.rev
    )


def _run_row(entry: RunEntry) -> dict[str, Any]:
    row = dict(entry.extra)
    row.update(
        {
            "run": entry.key.run,
            "kind": entry.key.kind,
            "sample_id": entry.sample_id,
            "measurement": entry.measurement,
            "condition": entry.condition,
            "include": entry.include,
            "note": entry.note,
            "title": entry.title,
            "start_time": entry.start_time,
            "rev": entry.rev,
            "updated_at": entry.updated_at,
        }
    )
    return row


def _sample_row(entry: SampleContext) -> dict[str, Any]:
    row = dict(entry.extra)
    row.update(
        {
            "sample_id": entry.sample_id,
            "title": entry.title,
            "description": entry.description,
            "details": entry.details,
            "mounting": entry.mounting,
            "measurement_conditions": entry.measurement_conditions,
            "fits_to_perform": entry.fits_to_perform,
            "rev": entry.rev,
            "updated_at": entry.updated_at,
        }
    )
    return row


def _experiment_row(entry: ExperimentContext) -> dict[str, Any]:
    row = dict(entry.extra)
    row.update({name: getattr(entry, name) for name in EXPERIMENT_FIELDS})
    row.update({"rev": entry.rev, "updated_at": entry.updated_at})
    return row


def _run_from_row(row: dict[str, Any]) -> RunEntry:
    try:
        return RunEntry(
            key=RunKey(_int(row, "run"), _text(row, "kind")),
            sample_id=row.get("sample_id") or None,
            measurement=_text(row, "measurement"),
            condition=_text(row, "condition"),
            include=_bool(row, "include"),
            note=_text(row, "note"),
            title=_text(row, "title"),
            start_time=_text(row, "start_time"),
            rev=_int(row, "rev"),
            updated_at=_text(row, "updated_at"),
            extra=_extra(row, _RUN_COLUMNS),
        )
    except (TypeError, ValueError) as exc:
        raise CatalogCorruptError(
            f"{RUNS_FILE}: a row cannot be read ({exc})."
        ) from exc


def _sample_from_row(row: dict[str, Any]) -> SampleContext:
    try:
        return SampleContext(
            sample_id=_text(row, "sample_id"),
            title=_text(row, "title"),
            description=_text(row, "description"),
            details=_text(row, "details"),
            mounting=_text(row, "mounting") or "unknown",
            measurement_conditions=_text(row, "measurement_conditions"),
            fits_to_perform=_text(row, "fits_to_perform"),
            rev=_int(row, "rev"),
            updated_at=_text(row, "updated_at"),
            extra=_extra(row, _SAMPLE_COLUMNS),
        )
    except (TypeError, ValueError) as exc:
        raise CatalogCorruptError(
            f"{SAMPLES_FILE}: a row cannot be read ({exc})."
        ) from exc


def _experiment_from_row(row: dict[str, Any]) -> ExperimentContext:
    try:
        return ExperimentContext(
            **{name: _text(row, name) for name in EXPERIMENT_FIELDS},
            rev=_int(row, "rev"),
            updated_at=_text(row, "updated_at"),
            extra=_extra(row, _EXPERIMENT_COLUMNS),
        )
    except (TypeError, ValueError) as exc:
        raise CatalogCorruptError(
            f"{EXPERIMENT_FILE}: its row cannot be read ({exc})."
        ) from exc


def _int(row: dict[str, Any], name: str) -> int:
    value = row.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} is {value!r}, not a whole number")
    return value


def _bool(row: dict[str, Any], name: str) -> bool:
    value = row.get(name)
    if not isinstance(value, bool):
        raise TypeError(f"{name} is {value!r}, not true or false")
    return value


def _text(row: dict[str, Any], name: str) -> str:
    value = row.get(name)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise TypeError(f"{name} is {value!r}, not text")
    return value


def _extra(row: dict[str, Any], known: tuple[str, ...]) -> dict[str, Any]:
    # Nulls kept: a column a newer nrw wrote, null in every row, must still be
    # a column after an older nrw saves.
    return {k: v for k, v in row.items() if k not in known}


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------


def _read_table(path: Path, name: str) -> list[dict[str, Any]]:
    """Read one table as rows, or refuse loudly."""
    if not path.exists():
        return []
    try:
        import pyarrow.parquet as pq

        table = pq.read_table(path)
    except Exception as exc:  # noqa: BLE001 - ArrowInvalid, OSError, and kin
        raise CatalogCorruptError(
            f"{path.name} exists but cannot be read ({type(exc).__name__}: {exc}). "
            "It is not being treated as empty, because the next save would then "
            "replace every decision recorded in it. Restore it from git "
            f"(`git checkout -- {path.parent.name}/{path.name}`)."
        ) from exc

    metadata = table.schema.metadata or {}
    if (
        metadata.get(b"nrw.schema") != SCHEMA_ID.encode()
        or metadata.get(b"nrw.table") != name.encode()
    ):
        raise CatalogCorruptError(f"{path.name} is not the nrw {name} table.")
    return table.to_pylist()


def _table_bytes(
    name: str, columns: tuple[str, ...], rows: list[dict[str, Any]]
) -> bytes:
    """Serialize rows to parquet, known columns first, extras preserved."""
    import io

    import pyarrow as pa
    import pyarrow.parquet as pq

    extras = sorted({k for row in rows for k in row} - set(columns))
    schema_fields = {
        "run": pa.int64(),
        "rev": pa.int64(),
        "include": pa.bool_(),
    }
    arrays = []
    names = []
    for column in (*columns, *extras):
        values = [row.get(column) for row in rows]
        kind = schema_fields.get(column, pa.string() if column in columns else None)
        arrays.append(pa.array(values, type=kind))
        names.append(column)
    table = pa.Table.from_arrays(arrays, names=names).replace_schema_metadata(
        {
            b"nrw.schema": SCHEMA_ID.encode(),
            b"nrw.table": name.encode(),
            b"nrw.schema_version": str(SCHEMA_VERSION).encode(),
        }
    )
    sink = io.BytesIO()
    pq.write_table(table, sink)
    return sink.getvalue()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _tables(manifest: dict[str, Any]) -> dict[str, Any]:
    tables = manifest.get("tables")
    return tables if isinstance(tables, dict) else {}


def _hashes(manifest: dict[str, Any]) -> tuple[Any, ...]:
    tables = _tables(manifest)
    return tuple(
        (tables.get(key) or {}).get("sha256")
        if isinstance(tables.get(key), dict)
        else None
        for key in _TABLES
    )


def _matches(manifest: dict[str, Any], directory: Path) -> bool:
    """Whether the tables in *directory* are exactly what *manifest* lists.

    The runs and samples tables must be listed. An optional table -- the
    experiment's -- is checked when it is listed, and only then.
    """
    tables = manifest.get("tables")
    if not isinstance(tables, dict):
        return False
    for key, name in _TABLES.items():
        if key in _OPTIONAL and key not in tables:
            continue
        entry = tables.get(key)
        if not isinstance(entry, dict) or entry.get("sha256") != sha256_file_or_none(
            directory / name
        ):
            return False
    return True


def _replace(target: Path, data: bytes, scratch: Path) -> None:
    """Write ``data`` to ``target`` atomically, with the temp file in *scratch*.

    Its own function so that a test can fail the second of a save's three
    replacements and check what a reader finds.
    """
    atomic_write_bytes(target, data, scratch=scratch)
