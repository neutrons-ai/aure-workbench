"""The experiment as it is right now: polled in the background, read without waiting.

One :class:`LiveInventory` per server. A single background thread polls the
source and the feed, judges every run, and swaps in a new immutable
:class:`Snapshot`. Requests only ever read the latest snapshot, so a web
request never touches the data mount -- which matters because a hard-mounted
NFS path that has gone away does not fail, it *blocks*, uninterruptibly. Polled
from request threads, that is one more wedged thread every few seconds for as
long as the page is open; here it is one wedged scan, reported as such, while
the page keeps serving what it last saw.

The thread starts on first use, not when the server starts, and goes idle once
nobody has asked for a while: a folder on a shared file server should not be
listed every thirty seconds all night because a tab was left open yesterday.

The page asks "what changed since my cursor?" A cursor is ``<epoch>:<generation>``:
the epoch is random per server process, so a cursor from before a restart
resyncs instead of silently missing everything that changed in between.
"""

from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from nr_workbench.experiment.inventory import (
    Announcement,
    FeedUpdate,
    Inventory,
    SourceRun,
)
from nr_workbench.experiment.model import RunKey
from nr_workbench.experiment.status import RunStatus, judge
from nr_workbench.problems import Problem

#: Stop polling after this long without a request.
DEFAULT_IDLE_AFTER = 600.0


@dataclass(frozen=True)
class RunView:
    """One run as the page shows it: what the source and feed say, and its state.

    Attributes:
        key: Which run.
        source: What the data source lists, or ``None`` if nothing yet.
        announcement: What the feed says, or ``None``.
        status: Its arrival state.
        changed: The generation in which any of the above last changed.
    """

    key: RunKey
    source: SourceRun | None
    announcement: Announcement | None
    status: RunStatus
    changed: int

    def signature(self) -> tuple[Any, ...]:
        """What, if different, makes this a change worth sending."""
        return (
            self.source.fingerprint if self.source else None,
            self.source.problems if self.source else None,
            self.announcement,
            self.status.state,
            self.status.reason,
        )


@dataclass(frozen=True)
class Snapshot:
    """Everything known after one poll.

    Attributes:
        epoch: Identifies this server process.
        generation: Increments on every poll that changed something.
        runs: The runs, by key.
        removed: Keys whose files disappeared, with the generation they went.
        inventory: The raw listing, for its problems and counts.
        feed: The raw feed update, for its problems.
        scanned_at: When the poll finished, or ``None`` before the first.
        stale_since: When the source was last reachable, if it is not now.
    """

    epoch: str
    generation: int = 0
    runs: Mapping[RunKey, RunView] = field(default_factory=dict)
    removed: Mapping[RunKey, int] = field(default_factory=dict)
    inventory: Inventory = field(default_factory=Inventory)
    feed: FeedUpdate = field(default_factory=FeedUpdate)
    scanned_at: float | None = None
    stale_since: float | None = None

    @property
    def cursor(self) -> str:
        """Where a reader of this snapshot is up to."""
        return f"{self.epoch}:{self.generation}"


@dataclass(frozen=True)
class ChangeSet:
    """What changed since a reader's cursor.

    Attributes:
        cursor: The reader's new cursor.
        resync: The reader must drop what it has and take ``runs`` as the whole
            set (first request, or a server restart).
        runs: Runs that changed (all of them on a resync).
        removed: Runs whose files have gone.
        scanned_at: When the last poll finished, or ``None``.
        scan_age: Seconds since then, or ``None``.
        scanning: Whether a poll is under way.
        stuck: Whether that poll has been running implausibly long -- usually
            an unreachable data mount.
        problems: What the source, the feed and the scanner report.
    """

    cursor: str
    resync: bool
    runs: tuple[RunView, ...]
    removed: tuple[RunKey, ...]
    scanned_at: float | None
    scan_age: float | None
    scanning: bool
    stuck: bool
    problems: tuple[Problem, ...]


class LiveInventory:
    """Keep a current view of the experiment's runs, polled in the background.

    Args:
        source: A :class:`~nr_workbench.experiment.sources.DataSource`.
        feed: A :class:`~nr_workbench.experiment.feeds.RunFeed`.
        settle_seconds: How long files must be unchanged to count as settled.
        poll_seconds: Seconds between polls while someone is watching.
        clock: Wall clock, injectable for tests. It is compared with file
            modification times, so it must be a real timestamp.
        idle_after: Stop polling after this long without a request.
        stuck_after: Call a poll stuck after this long. Defaults to three poll
            intervals, and never less than a minute.
        autostart: Start the background thread on the first request. Off in
            tests that drive :meth:`scan_once` themselves.
    """

    def __init__(
        self,
        source: Any,
        feed: Any,
        *,
        settle_seconds: float,
        poll_seconds: float,
        clock: Callable[[], float] = time.time,
        idle_after: float = DEFAULT_IDLE_AFTER,
        stuck_after: float | None = None,
        autostart: bool = True,
    ) -> None:
        self.source = source
        self.feed = feed
        self.settle_seconds = settle_seconds
        self.poll_seconds = poll_seconds
        self.clock = clock
        self.idle_after = idle_after
        self.stuck_after = stuck_after or max(60.0, 3 * poll_seconds)
        self.autostart = autostart

        self._snapshot = Snapshot(epoch=secrets.token_hex(4))
        self._fingerprints: dict[RunKey, str] = {}
        self._lock = threading.Lock()
        self._scan_lock = threading.Lock()
        self._scan_started: float | None = None
        self._last_request = clock()
        self._resume = threading.Event()
        self._idle = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._thread_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def snapshot(self) -> Snapshot:
        """The latest snapshot. Never waits on the source."""
        with self._lock:
            return self._snapshot

    @property
    def idle(self) -> bool:
        """Whether the background thread is waiting for a request, not polling."""
        return self._idle.is_set()

    def changes(self, since: str | None = None) -> ChangeSet:
        """What changed since *since*, and keep polling for a while.

        Args:
            since: A cursor from an earlier :class:`ChangeSet`, or ``None``.
        """
        self.touch()
        snapshot = self.snapshot()
        epoch, generation = _parse_cursor(since)
        resync = (
            since is None or epoch != snapshot.epoch or generation > snapshot.generation
        )
        if resync:
            runs = tuple(snapshot.runs[k] for k in sorted(snapshot.runs))
            removed: tuple[RunKey, ...] = ()
        else:
            runs = tuple(
                snapshot.runs[k]
                for k in sorted(snapshot.runs)
                if snapshot.runs[k].changed > generation
            )
            removed = tuple(
                sorted(k for k, gen in snapshot.removed.items() if gen > generation)
            )

        now = self.clock()
        started = self._scan_started
        stuck = started is not None and now - started > self.stuck_after
        problems = list(snapshot.inventory.problems) + list(snapshot.feed.problems)
        if snapshot.stale_since is not None:
            problems.insert(
                0,
                Problem(
                    "source",
                    "The data source cannot be reached; showing what it listed "
                    f"{now - snapshot.stale_since:.0f}s ago.",
                ),
            )
        if stuck:
            problems.insert(
                0,
                Problem(
                    "source",
                    f"Listing the data source has taken {now - started:.0f}s so far. "
                    "The data mount may be unavailable; the page keeps showing "
                    "the last listing.",
                ),
            )
        return ChangeSet(
            cursor=snapshot.cursor,
            resync=resync,
            runs=runs,
            removed=removed,
            scanned_at=snapshot.scanned_at,
            scan_age=None if snapshot.scanned_at is None else now - snapshot.scanned_at,
            scanning=started is not None,
            stuck=stuck,
            problems=tuple(problems),
        )

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    def scan_once(self) -> Snapshot:
        """Poll the source and the feed once, and publish the result.

        If a poll is already under way this returns the current snapshot
        rather than starting a second one: a listing that is blocked on a dead
        mount must not be joined by another every few seconds.
        """
        if not self._scan_lock.acquire(blocking=False):
            return self.snapshot()
        try:
            return self._scan_locked()
        finally:
            self._scan_lock.release()

    def scan_fresh(self, timeout: float) -> Snapshot:
        """Poll now, waiting at most *timeout* for a poll already under way.

        :meth:`scan_once` returns the *previous* snapshot while another poll is
        running, which is right for a background loop and wrong for apply:
        whether a run is complete must be judged from a listing taken after the
        person clicked, not one started before. So this waits for the running
        poll to finish and then polls again.

        Raises:
            TimeoutError: The poll already under way did not finish in time --
                usually a data mount that has stopped answering.
        """
        if not self._scan_lock.acquire(timeout=timeout):
            raise TimeoutError(
                f"a poll of the data source has been running for more than "
                f"{timeout:.0f}s"
            )
        try:
            return self._scan_locked()
        finally:
            self._scan_lock.release()

    def _scan_locked(self) -> Snapshot:
        """One poll. The caller holds the scan lock."""
        try:
            self._scan_started = self.clock()
            inventory = _guarded_inventory(self.source)
            feed = _guarded_poll(self.feed, inventory)
            return self._publish(inventory, feed, self.clock())
        finally:
            self._scan_started = None

    def _publish(self, inventory: Inventory, feed: FeedUpdate, now: float) -> Snapshot:
        with self._lock:
            previous = self._snapshot

        if not inventory.reachable and previous.scanned_at is not None:
            # Keep what was last seen rather than reporting every run as gone
            # because the mount blinked. Say that it is stale instead.
            snapshot = Snapshot(
                epoch=previous.epoch,
                generation=previous.generation,
                runs=previous.runs,
                removed=previous.removed,
                inventory=Inventory(
                    runs=previous.inventory.runs,
                    reachable=False,
                    problems=inventory.problems,
                ),
                feed=feed,
                scanned_at=previous.scanned_at,
                stale_since=previous.stale_since or previous.scanned_at,
            )
            with self._lock:
                self._snapshot = snapshot
            return snapshot

        generation = previous.generation + 1
        announced = {a.run: a for a in feed.announcements}
        keys = set(inventory.runs) | {RunKey(run) for run in announced}
        latest = max((k.run for k in inventory.runs), default=None)

        views: dict[RunKey, RunView] = {}
        changed = False
        for key in sorted(keys):
            run = inventory.runs.get(key)
            status = judge(
                run,
                previous_fingerprint=self._fingerprints.get(key),
                now=now,
                settle_seconds=self.settle_seconds,
                latest_reduced=latest,
            )
            if run is not None:
                self._fingerprints[key] = run.fingerprint
            view = RunView(key, run, announced.get(key.run), status, generation)
            old = previous.runs.get(key)
            if old is not None and old.signature() == view.signature():
                view = old
            else:
                changed = True
            views[key] = view

        removed = dict(previous.removed)
        for key in set(previous.runs) - set(views):
            removed[key] = generation
            self._fingerprints.pop(key, None)
            changed = True
        for key in views:
            removed.pop(key, None)

        snapshot = Snapshot(
            epoch=previous.epoch,
            generation=generation if changed else previous.generation,
            runs=views,
            removed=removed,
            inventory=inventory,
            feed=feed,
            scanned_at=now,
            stale_since=None,
        )
        with self._lock:
            self._snapshot = snapshot
        return snapshot

    # ------------------------------------------------------------------
    # The background thread
    # ------------------------------------------------------------------

    def touch(self) -> None:
        """Note a request: start polling if idle, keep polling if not."""
        self._last_request = self.clock()
        if not self.autostart:
            return
        # Requests arrive on many threads at once; only one may start the
        # scanner, or two would poll the mount in parallel.
        with self._thread_lock:
            if self._thread is None or not self._thread.is_alive():
                self._stop.clear()
                self._thread = threading.Thread(
                    target=self._loop, name="nrw-experiment-scan", daemon=True
                )
                self._thread.start()
        self._resume.set()

    def stop(self) -> None:
        """Stop the background thread. A poll blocked on the source stays blocked."""
        self._stop.set()
        self._resume.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            if self.clock() - self._last_request > self.idle_after:
                self._resume.clear()
                # Re-check after clearing: a request that arrived between the
                # check above and the clear would otherwise be missed until
                # the next one, and the page would show a stale listing.
                if self.clock() - self._last_request > self.idle_after:
                    self._idle.set()
                    self._resume.wait()
                    self._idle.clear()
                continue
            self.scan_once()
            self._stop.wait(self.poll_seconds)


def _parse_cursor(since: str | None) -> tuple[str, int]:
    if not since or ":" not in since:
        return ("", -1)
    epoch, _, generation = since.partition(":")
    try:
        return (epoch, int(generation))
    except ValueError:
        return ("", -1)


def _guarded_inventory(source: Any) -> Inventory:
    """The source's inventory, or an unreachable one naming the failure."""
    try:
        return source.inventory()
    except Exception as exc:  # noqa: BLE001 - one bad poll must not end polling
        return Inventory(
            reachable=False,
            problems=(
                Problem(
                    "source",
                    f"Listing the data source failed ({type(exc).__name__}: {exc}). "
                    "This is a bug worth reporting; polling continues.",
                ),
            ),
        )


def _guarded_poll(feed: Any, inventory: Inventory) -> FeedUpdate:
    """The feed's update, or an empty one naming the failure."""
    try:
        return feed.poll(inventory)
    except Exception as exc:  # noqa: BLE001 - see _guarded_inventory
        return FeedUpdate(
            problems=(
                Problem(
                    "feed",
                    f"Polling the run feed failed ({type(exc).__name__}: {exc}).",
                ),
            )
        )
