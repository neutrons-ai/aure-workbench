"""Everything the Experiment page knows, with no Flask anywhere.

The same split as :mod:`nr_workbench.web.project`: this class is testable with
nothing but a directory, and :mod:`nr_workbench.web.experiment_api` mirrors it
one-to-one, so an agent can ``curl`` exactly what the page renders.

Two rules shape it:

**No request waits on the data mount.** Listing the source is the background
poller's job (:class:`~nr_workbench.experiment.live.LiveInventory`); requests
read its last snapshot. The two things that must read the source -- a quick
look at a run's curves, and the fresh look apply takes before writing -- go
through a two-thread pool with a timeout, so a dead mount costs those requests
a timeout rather than a thread each, forever.

**Writes are the catalog's and apply's.** This class decides nothing: the
catalog validates edits, apply decides what may be copied. It refuses every
write when the server was started read-only, which the request gate in
:mod:`nr_workbench.web.security` also enforces -- two mechanisms, as with the
agent guard, so that forgetting one is not a hole.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from nr_workbench.experiment.model import (
    Catalog,
    RunChange,
    RunKey,
    SampleChange,
    validate_sample_id,
)
from nr_workbench.experiment.status import STATES
from nr_workbench.problems import Problem

#: Seconds a request may wait for the data source.
SOURCE_TIMEOUT = 15.0

#: Most runs a quick look will plot at once.
MAX_QUICK_LOOK_RUNS = 8


class WritesDisabledError(PermissionError):
    """The server was started without write access. The message says why."""


class SourceTimeoutError(TimeoutError):
    """The data source did not answer in time -- usually a dead mount."""


class ExperimentData:
    """The experiment of one project, shaped for the page.

    Args:
        root: Project root.
        writable: Whether writes are allowed at all. ``nrw serve`` sets this
            from where it is bound and who is running it.
        why_read_only: The reason, when not writable, for the page to show.
        clock: Wall clock, for tests.
        autostart: Start background polling on first use.
    """

    def __init__(
        self,
        root: Path,
        *,
        writable: bool = True,
        why_read_only: str = "",
        clock: Callable[[], float] = time.time,
        autostart: bool = True,
    ) -> None:
        self.root = Path(root).resolve()
        self.writable = writable
        self.why_read_only = why_read_only
        self._clock = clock
        self._autostart = autostart
        self._workspace: Any = None
        self._live: Any = None
        self._lock = threading.Lock()
        self._pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="nrw-source-read"
        )

    # ------------------------------------------------------------------
    # Wiring, lazily: `nrw serve` must not touch the data mount at start-up
    # ------------------------------------------------------------------

    @property
    def workspace(self) -> Any:
        """The project's experiment workspace, built on first use."""
        with self._lock:
            if self._workspace is None:
                from nr_workbench.experiment.workspace import Workspace

                self._workspace = Workspace(self.root)
            return self._workspace

    @property
    def live(self) -> Any:
        """The background poller, built on first use."""
        workspace = self.workspace
        with self._lock:
            if self._live is None:
                self._live = workspace.live(clock=self._clock, autostart=self._autostart)
            return self._live

    def stop(self) -> None:
        """Stop polling. For tests and shutdown."""
        if self._live is not None:
            self._live.stop()
        self._pool.shutdown(wait=False, cancel_futures=True)

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def overview(self) -> dict[str, Any]:
        """Everything the page shows on load.

        Returns:
            The source, feed and catalog, every run (from the source, the feed
            or the catalog), every sample, and the cursor for :meth:`changes`.
        """
        workspace = self.workspace
        changes = self.live.changes(None)
        catalog, catalog_problems, readable = self._catalog()
        rows = {view.key: self._row(view.key, view, catalog) for view in changes.runs}
        for key in catalog.runs:
            if key not in rows:
                rows[key] = self._row(key, None, catalog)
        return {
            "schema": "nrw-experiment-page/1",
            "ipts": workspace.config.ipts,
            "source": workspace.source.describe(),
            "feed": workspace.feed.describe(),
            "catalog": {
                **workspace.store.describe(),
                "version": self._catalog_version(),
                "readable": readable,
            },
            # A catalog that cannot be read must not be edited: saving over it
            # would replace every decision in it with the page's empty view.
            "writable": self.writable and readable,
            "read_only_reason": self.why_read_only,
            "states": list(STATES),
            "cursor": changes.cursor,
            "scan": _scan(changes),
            "runs": [rows[k] for k in sorted(rows)],
            "samples": self._samples(catalog),
            "problems": [
                p.as_dict()
                for p in (*workspace.problems, *catalog_problems, *changes.problems)
            ],
        }

    def changes(self, since: str | None) -> dict[str, Any]:
        """What changed since the page's cursor.

        Returns:
            Changed runs (all of them after a server restart), removed runs,
            the new cursor, and the catalog's version -- when that differs from
            what the page holds, someone else edited the catalog and the page
            should reload it.
        """
        changes = self.live.changes(since)
        catalog, catalog_problems, _ = self._catalog()
        return {
            "cursor": changes.cursor,
            "resync": changes.resync,
            "runs": [self._row(v.key, v, catalog) for v in changes.runs],
            "removed": [k.slug() for k in changes.removed],
            "catalog_version": self._catalog_version(),
            "scan": _scan(changes),
            "problems": [p.as_dict() for p in (*catalog_problems, *changes.problems)],
        }

    def curves(self, run: int) -> dict[str, Any]:
        """A run's reflectivity, straight from the data source, for a quick look.

        Raises:
            FileNotFoundError: If the source does not list the run.
            SourceTimeoutError: If the source does not answer in time.
        """
        from nr_workbench.experiment.apply import MAX_FILE_BYTES
        from nr_workbench.web.readers import read_reduced_bytes

        key = RunKey.parse(run)
        view = self.live.snapshot().runs.get(key)
        if view is None or view.source is None:
            raise FileNotFoundError(f"The data source does not list run {key.run}.")
        curves = []
        problems = []
        source = self.workspace.source
        for index, source_file in enumerate(view.source.files):
            label = (
                f"{key.run}#{source_file.segment}"
                if source_file.segment is not None
                else f"{key.run} combined"
            )
            try:
                data = self._bounded(source.read_bytes, source_file, max_bytes=MAX_FILE_BYTES)
                curve = read_reduced_bytes(data, label=label, name=source_file.name)
            except SourceTimeoutError:
                raise
            except Exception as exc:  # noqa: BLE001 - one unreadable segment is a finding
                problems.append(Problem(f"curve:{label}", str(exc)).as_dict())
                continue
            payload = curve.as_dict()
            payload["run"] = key.run
            if index < len(view.source.thetas) and view.source.thetas[index] is not None:
                payload["theta"] = view.source.thetas[index]
            curves.append(payload)
        return {"run": key.run, "curves": curves, "problems": problems}

    def sample_preview(self, sample_id: str) -> dict[str, Any]:
        """The sample.md the catalog would write, and where the file stands.

        Raises:
            ValueError: If the sample id is not usable.
        """
        from nr_workbench.experiment.render import (
            SampleRenderError,
            plan_sample,
            sample_md_relpath,
        )
        from nr_workbench.project.scaffold import classify, load_lock, render_diff
        from nr_workbench.web.prose import render

        validate_sample_id(sample_id)
        catalog, problems, _ = self._catalog()
        relpath = sample_md_relpath(sample_id)
        path = self.root / relpath
        try:
            planned = next(
                p
                for p in plan_sample(
                    self.root, self.workspace.render_context(), sample_id, catalog=catalog
                )
                if p.relpath == relpath
            )
        except SampleRenderError as exc:
            return {"sample": sample_id, "problems": [Problem("sample", str(exc)).as_dict()]}
        lock = load_lock(self.root / ".nrw" / "scaffold.lock.json")
        outcome = classify(planned, path, lock.get(relpath))
        text = planned.content.decode("utf-8")
        return {
            "sample": sample_id,
            "managed": catalog.manages(sample_id),
            "exists": path.is_file(),
            "state": str(outcome),
            "markdown": text,
            "html": render(text),
            "diff": render_diff(planned, path) if path.is_file() else "",
            "problems": [p.as_dict() for p in problems],
        }

    def apply_plan(
        self, samples: list[str] | None = None, confirmed: list[Any] | None = None
    ) -> dict[str, Any]:
        """What applying would do, from the latest snapshot. Writes nothing."""
        from nr_workbench.experiment.apply import plan_apply

        catalog = self._catalog_or_raise()
        runs, statuses = self._observed(self.live.snapshot())
        plan = plan_apply(
            self.root,
            catalog,
            runs,
            statuses,
            self.workspace.source,
            self.workspace.render_context(),
            samples=_sample_list(samples),
            confirmed=_run_keys(confirmed),
        )
        return plan.as_dict()

    def adopt_plan(self, sample_id: str) -> dict[str, Any]:
        """What adopting (or pulling) a sample's sample.md would do."""
        from nr_workbench.experiment.adopt import plan_adopt

        validate_sample_id(sample_id)
        plan = plan_adopt(
            self.root, self._catalog_or_raise(), sample_id, self.workspace.render_context()
        )
        return plan.as_dict()

    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------

    def update_runs(self, changes: Any) -> dict[str, Any]:
        """Record edits to run records.

        Args:
            changes: A list of ``{"run", "base_rev", "fields"}``. The run's
                title and start time are filled in from the source here, not
                taken from the request.

        Raises:
            ValueError: A malformed request or a rule broken (400).
            RecordConflict: A record changed since the page loaded it (409).
        """
        self._require_writable()
        if not isinstance(changes, list) or not changes:
            raise ValueError("expected a non-empty list of run changes")
        snapshot = self.live.snapshot()
        run_changes = []
        for item in changes:
            if not isinstance(item, dict):
                raise ValueError("each change must be an object")
            key = RunKey.parse(_required(item, "run"))
            fields = item.get("fields")
            if not isinstance(fields, dict) or not fields:
                raise ValueError(f"run {key.run}: 'fields' must be a non-empty object")
            if {"title", "start_time"} & set(fields):
                raise ValueError("title and start_time come from the data source, not the page")
            view = snapshot.runs.get(key)
            if view is not None and view.source is not None:
                fields = {
                    "title": view.source.title,
                    "start_time": view.source.start_time,
                    **fields,
                }
            run_changes.append(RunChange(key, _rev(item.get("base_rev", 0)), fields))
        catalog = self.workspace.store.update(runs=run_changes)
        return {
            "runs": [self._row(c.key, snapshot.runs.get(c.key), catalog) for c in run_changes],
            "samples": self._samples(catalog),
            "catalog_version": self._catalog_version(),
        }

    def update_sample(self, sample_id: str, payload: Any) -> dict[str, Any]:
        """Record edits to a sample's context, or remove it.

        Raises:
            ValueError: A malformed request or a rule broken (400).
            RecordConflict: The record changed since the page loaded it (409).
        """
        self._require_writable()
        validate_sample_id(sample_id)
        if not isinstance(payload, dict):
            raise ValueError("expected an object")
        delete = payload.get("delete", False)
        if not isinstance(delete, bool):
            raise ValueError("delete must be true or false")
        if delete and (self.root / "samples" / sample_id).exists():
            raise ValueError(
                f"samples/{sample_id}/ exists, so the catalog keeps managing it. "
                f"Use `nrw experiment release {sample_id}` to make sample.md "
                "yours instead."
            )
        fields = payload.get("fields", {})
        if not isinstance(fields, dict):
            raise ValueError("'fields' must be an object")
        catalog = self.workspace.store.update(
            samples=[
                SampleChange(
                    sample_id, _rev(payload.get("base_rev", 0)), fields, delete=delete
                )
            ]
        )
        return {"samples": self._samples(catalog), "catalog_version": self._catalog_version()}

    def apply(
        self,
        plan_id: Any,
        samples: list[str] | None = None,
        confirmed: list[Any] | None = None,
    ) -> dict[str, Any]:
        """Carry out a reviewed plan, after a fresh look at the source.

        Raises:
            ValueError: A malformed request.
            ApplyError: The plan changed, another apply is running, or
                something must be fixed first (409).
            SourceTimeoutError: The source did not answer in time.
        """
        from nr_workbench.experiment.apply import apply

        self._require_writable()
        if not isinstance(plan_id, str) or not plan_id:
            raise ValueError("plan_id is required: review the plan before applying")
        catalog = self._catalog_or_raise()
        # A fresh poll, not the last snapshot: completeness is judged now.
        snapshot = self._bounded(self.live.scan_once)
        runs, statuses = self._observed(snapshot)
        report = apply(
            self.root,
            catalog,
            runs,
            statuses,
            self.workspace.source,
            self.workspace.render_context(),
            expected_plan_id=plan_id,
            samples=_sample_list(samples),
            confirmed=_run_keys(confirmed),
        )
        return report.as_dict()

    def adopt(self, sample_id: str, rewrite: Any) -> dict[str, Any]:
        """Adopt (or pull) a sample's sample.md into the catalog."""
        from nr_workbench.experiment.adopt import adopt, plan_adopt

        self._require_writable()
        validate_sample_id(sample_id)
        if not isinstance(rewrite, bool):
            raise ValueError("rewrite must be true or false")
        context = self.workspace.render_context()
        plan = plan_adopt(self.root, self._catalog_or_raise(), sample_id, context)
        report = adopt(self.root, self.workspace.store, plan, context, rewrite=rewrite)
        return {
            "catalog_changes": report.catalog_changes,
            "rewritten": report.rewritten,
            "backup": report.backup,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _require_writable(self) -> None:
        if not self.writable:
            raise WritesDisabledError(
                self.why_read_only or "This server was started read-only."
            )

    def _catalog(self) -> tuple[Catalog, list[Problem], bool]:
        """The catalog, its problems, and whether it could be read at all.

        An unreadable catalog comes back empty *for display only*, with the
        third element False; every write path loads it again and refuses.
        """
        from nr_workbench.experiment.store import CatalogError

        store = self.workspace.store
        try:
            return (store.load(), store.problems(), True)
        except CatalogError as exc:
            return (Catalog(), [Problem("catalog", str(exc))], False)

    def _catalog_or_raise(self) -> Catalog:
        return self.workspace.store.load()

    def _catalog_version(self) -> str:
        from nr_workbench.experiment.store import MANIFEST_FILE, RUNS_FILE, SAMPLES_FILE

        digest = hashlib.sha256()
        for name in (MANIFEST_FILE, RUNS_FILE, SAMPLES_FILE):
            path = self.workspace.store.directory / name
            try:
                stat = path.stat()
            except OSError:
                digest.update(f"{name}:-".encode())
                continue
            digest.update(f"{name}:{stat.st_size}:{stat.st_mtime_ns}".encode())
        return digest.hexdigest()[:16]

    def _observed(self, snapshot: Any) -> tuple[dict, dict]:
        runs = {k: v.source for k, v in snapshot.runs.items() if v.source is not None}
        return (runs, {k: v.status for k, v in snapshot.runs.items()})

    def _bounded(self, function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        future = self._pool.submit(function, *args, **kwargs)
        try:
            return future.result(timeout=SOURCE_TIMEOUT)
        except concurrent.futures.TimeoutError as exc:
            raise SourceTimeoutError(
                f"The data source did not answer within {SOURCE_TIMEOUT:.0f}s; "
                "the data mount may be unavailable."
            ) from exc

    def _row(self, key: RunKey, view: Any, catalog: Catalog) -> dict[str, Any]:
        entry = catalog.runs.get(key)
        source = view.source if view is not None else None
        status = view.status if view is not None else None
        return {
            "key": key.slug(),
            "run": key.run,
            "kind": key.kind,
            "state": status.state if status else "not listed",
            "reason": status.reason if status else "the data source does not list it",
            "complete": bool(status and status.complete),
            "title": (source.title if source else "") or (entry.title if entry else ""),
            "start_time": (source.start_time if source else "")
            or (entry.start_time if entry else ""),
            "segments": list(source.segments) if source else [],
            "n_segments": source.n_segments if source else None,
            "thetas": [round(t, 3) if t is not None else None for t in source.thetas]
            if source
            else [],
            "announced": view is not None and view.announcement is not None,
            "sample": entry.sample_id if entry else None,
            "measurement": entry.measurement if entry else "",
            "condition": entry.condition if entry else "",
            "include": entry.include if entry else True,
            "note": entry.note if entry else "",
            "rev": entry.rev if entry else 0,
        }

    def _samples(self, catalog: Catalog) -> list[dict[str, Any]]:
        from nr_workbench.project.layout import ProjectLayout

        managed = catalog.sample_ids()
        cards = []
        for sample_id in managed:
            context = catalog.context_for(sample_id)
            runs = catalog.runs_for(sample_id)
            cards.append(
                {
                    "id": sample_id,
                    "managed": True,
                    "on_disk": (self.root / "samples" / sample_id / "sample.md").is_file(),
                    "title": context.title,
                    "description": context.description,
                    "details": context.details,
                    "mounting": context.mounting,
                    "measurement_conditions": context.measurement_conditions,
                    "fits_to_perform": context.fits_to_perform,
                    "rev": context.rev,
                    "runs": [e.key.run for e in runs if e.include],
                    "excluded": [e.key.run for e in runs if not e.include],
                }
            )
        for sample_id in ProjectLayout(root=self.root).list_samples():
            if sample_id not in managed:
                cards.append(
                    {"id": sample_id, "managed": False, "on_disk": True, "runs": [], "excluded": []}
                )
        return cards


def _scan(changes: Any) -> dict[str, Any]:
    return {
        "scanned_at": changes.scanned_at,
        "age": None if changes.scan_age is None else round(changes.scan_age, 1),
        "scanning": changes.scanning,
        "stuck": changes.stuck,
    }


def _required(item: dict[str, Any], name: str) -> Any:
    if name not in item:
        raise ValueError(f"'{name}' is required")
    return item[name]


def _rev(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"base_rev must be a whole number, not {value!r}")
    return value


def _sample_list(samples: Any) -> list[str] | None:
    if samples in (None, []):
        return None
    if not isinstance(samples, list) or not all(isinstance(s, str) for s in samples):
        raise ValueError("samples must be a list of sample ids")
    return [validate_sample_id(s) for s in samples]


def _run_keys(runs: Any) -> set[RunKey]:
    if runs in (None, []):
        return set()
    if not isinstance(runs, list):
        raise ValueError("confirmed must be a list of run numbers")
    return {RunKey.parse(run) for run in runs}
