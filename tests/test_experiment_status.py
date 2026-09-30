"""When a run counts as complete, and how the page learns that it changed.

The failure these tests exist for is quiet: a run declared complete after its
first segment, copied, and fitted -- a third of a measurement, which fits
perfectly well. The timeline test replays the reference corpus's *real*
reduction times, where segments landed 15.5 and 52.4 minutes apart.
"""

from __future__ import annotations

import os
import shutil
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from nr_workbench.experiment.feeds.directory import DirectoryFeed
from nr_workbench.experiment.inventory import SourceFile, SourceRun
from nr_workbench.experiment.live import LiveInventory
from nr_workbench.experiment.model import RunKey
from nr_workbench.experiment.sources.local import LocalDirectorySource
from nr_workbench.experiment.status import judge

from .experiment_fixtures import (
    REFERENCE_REDUCED_AT,
    REFERENCE_STEADY,
    InMemoryFeed,
    InMemorySource,
)

T0 = 1_750_000_000.0
SETTLE = 300.0


class Clock:
    """A settable wall clock. File mtimes are real timestamps, so this is too."""

    def __init__(self, now: float = T0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def run_with(segments, *, n=None, changed_at=T0, problems=(), subrun_base=None):
    base = subrun_base or 234277
    files = tuple(
        SourceFile(
            name=f"f{s}",
            size=1,
            mtime=changed_at,
            version="v",
            segment=s,
            subrun=base + s - 1,
        )
        for s in segments
    )
    return SourceRun(
        key=RunKey(base),
        files=files,
        n_segments=n,
        changed_at=changed_at,
        fingerprint="fp",
        problems=tuple(problems),
    )


def settled(run, latest=None):
    return judge(
        run,
        previous_fingerprint="fp",
        now=T0 + SETTLE + 1,
        settle_seconds=SETTLE,
        latest_reduced=latest,
    )


# --------------------------------------------------------------------------
# judge
# --------------------------------------------------------------------------


def test_judge_an_announced_run_without_files_is_awaiting_reduction() -> None:
    status = judge(
        None,
        previous_fingerprint=None,
        now=T0,
        settle_seconds=SETTLE,
        latest_reduced=None,
    )
    assert status.state == "awaiting" and not status.complete


def test_judge_a_run_with_problems_is_quarantined() -> None:
    status = settled(run_with([1, 2, 3], n=3, problems=["segment 2 is here twice"]))
    assert status.state == "quarantined"
    assert status.reason == "segment 2 is here twice"


def test_judge_a_fulfilled_plan_alone_does_not_prove_completion() -> None:
    """The header's count is unverified in a run's first file, so it cannot prove."""
    status = settled(run_with([1, 2, 3], n=3))
    assert status.state == "unconfirmed" and not status.complete
    assert "later run" in status.reason


def test_judge_a_fulfilled_plan_and_a_later_run_is_complete() -> None:
    status = settled(run_with([1, 2, 3], n=3), latest=234290)
    assert status.state == "complete" and status.complete


def test_judge_a_first_segment_header_sized_one_is_not_complete() -> None:
    """If the arrays grow as segments land, segment 1's file says "1 of 1".

    Trusting that would copy a third of a measurement five minutes after it
    arrived -- the failure this module exists to prevent.
    """
    status = settled(run_with([1], n=1))
    assert status.state == "unconfirmed" and not status.complete


def test_judge_a_plan_that_grows_with_each_segment_is_not_complete() -> None:
    status = settled(run_with([1, 2], n=2))
    assert status.state == "unconfirmed" and not status.complete


def test_judge_a_known_plan_with_a_missing_segment_is_never_complete() -> None:
    """Even a later run cannot override a plan that says more is coming."""
    status = settled(run_with([1, 2], n=3), latest=234290)
    assert status.state == "unconfirmed" and not status.complete
    assert "2 of 3" in status.reason


def test_judge_settled_is_not_complete_without_evidence_the_run_ended() -> None:
    status = settled(run_with([1], n=None), latest=234277)
    assert status.state == "unconfirmed" and not status.complete


def test_judge_a_later_reduced_run_completes_an_unplanned_run() -> None:
    status = settled(run_with([1, 2, 3], n=None), latest=234280)
    assert status.state == "complete" and "234280" in status.reason


def test_judge_a_later_subrun_of_the_same_run_is_not_a_later_run() -> None:
    """234279 is this run's own third segment, not the next measurement."""
    status = settled(run_with([1, 2, 3], n=None), latest=234279)
    assert status.state == "unconfirmed"


def test_judge_files_dated_in_the_future_are_clock_skew() -> None:
    run = run_with([1, 2, 3], n=3, changed_at=T0 + 3600)
    status = judge(
        run,
        previous_fingerprint="fp",
        now=T0,
        settle_seconds=SETTLE,
        latest_reduced=None,
    )
    assert status.state == "clock-skew" and not status.complete


def test_judge_a_run_still_changing_is_not_settled() -> None:
    status = judge(
        run_with([1], n=1),
        previous_fingerprint="fp",
        now=T0 + 20,
        settle_seconds=SETTLE,
        latest_reduced=None,
    )
    assert status.state == "settling" and not status.settled


# --------------------------------------------------------------------------
# The real timeline, through the real folder source
# --------------------------------------------------------------------------


def _arrive(folder: Path, name: str) -> None:
    target = folder / name
    shutil.copy(REFERENCE_STEADY / name, target)
    stamp = T0 + REFERENCE_REDUCED_AT[name]
    os.utime(target, (stamp, stamp))


def test_the_reference_timeline_is_complete_only_once_the_next_run_arrives(
    tmp_path: Path,
) -> None:
    """218386's segments landed 0, 15.5 and 67.9 minutes in; 218393 at 3h40."""
    clock = Clock()
    live = LiveInventory(
        LocalDirectorySource(tmp_path, str(tmp_path), ipts="IPTS-34347"),
        DirectoryFeed(),
        settle_seconds=SETTLE,
        poll_seconds=30,
        clock=clock,
        autostart=False,
    )
    key = RunKey(218386)

    def state_at(offset: float) -> str:
        clock.now = T0 + offset
        return live.scan_once().runs[key].status.state

    _arrive(tmp_path, "REFL_218386_1_218386_partial.txt")
    assert state_at(10) == "settling"
    # Five minutes of quiet after segment 1 is where a settle-only rule would
    # have copied a third of the measurement.
    assert state_at(400) == "unconfirmed"

    _arrive(tmp_path, "REFL_218386_2_218387_partial.txt")
    assert state_at(935) == "arriving"
    assert state_at(1300) == "unconfirmed"

    _arrive(tmp_path, "REFL_218386_3_218388_partial.txt")
    # A change between two polls restarts the settle clock, whatever the mtime.
    assert state_at(4080) == "arriving"
    # All three segments, settled -- and still not complete: the old dialect
    # does not say three was the plan, and nothing yet shows it ended.
    assert state_at(4400) == "unconfirmed"

    _arrive(tmp_path, "REFL_218393_1_218393_partial.txt")
    clock.now = T0 + 13250
    snapshot = live.scan_once()
    assert snapshot.runs[key].status.state == "complete"
    assert "218393" in snapshot.runs[key].status.reason
    # First sight of a run has no earlier poll to compare with, so its quiet
    # time is read from the file itself: three seconds, still settling.
    assert snapshot.runs[RunKey(218393)].status.state == "settling"


def test_a_planned_new_reduction_run_completes_once_the_next_run_is_reduced() -> None:
    clock = Clock()
    source = InMemorySource()
    live = LiveInventory(
        source,
        DirectoryFeed(),
        settle_seconds=SETTLE,
        poll_seconds=30,
        clock=clock,
        autostart=False,
    )
    key = RunKey(234277)

    source.add_segments(234277, [1], planned=3, mtime=T0)
    clock.now = T0 + 400
    assert live.scan_once().runs[key].status.state == "unconfirmed"

    source.add_segments(234277, [2, 3], planned=3, mtime=T0 + 500)
    clock.now = T0 + 510
    assert live.scan_once().runs[key].status.state == "arriving"
    clock.now = T0 + 900
    # Every planned segment, settled: still waiting for proof the run ended.
    assert live.scan_once().runs[key].status.state == "unconfirmed"

    source.add_segments(234290, [1], planned=3, mtime=T0 + 950)
    clock.now = T0 + 960
    assert live.scan_once().runs[key].status.state == "complete"


# --------------------------------------------------------------------------
# The feed is a separate seam
# --------------------------------------------------------------------------


def test_a_run_the_feed_announces_before_any_data_is_awaiting(tmp_path: Path) -> None:
    """The web monitor knows a run was acquired before reduction writes a file."""
    feed = InMemoryFeed()
    feed.announce(234280, state="acquiring")
    live = LiveInventory(
        LocalDirectorySource(tmp_path, str(tmp_path), ipts=None),
        feed,
        settle_seconds=SETTLE,
        poll_seconds=30,
        clock=Clock(),
        autostart=False,
    )

    view = live.scan_once().runs[RunKey(234280)]

    assert view.status.state == "awaiting"
    assert view.source is None and view.announcement.state == "acquiring"


def test_an_announced_later_run_does_not_complete_an_earlier_one() -> None:
    """Acquisition starting is not reduction finishing."""
    clock = Clock(T0 + 400)
    source = InMemorySource()
    source.add_segments(234277, [1, 2, 3], planned=None, mtime=T0)
    feed = InMemoryFeed()
    feed.announce(234290)
    live = LiveInventory(
        source,
        feed,
        settle_seconds=SETTLE,
        poll_seconds=30,
        clock=clock,
        autostart=False,
    )

    snapshot = live.scan_once()

    assert snapshot.runs[RunKey(234277)].status.state == "unconfirmed"
    assert snapshot.runs[RunKey(234290)].status.state == "awaiting"


# --------------------------------------------------------------------------
# Cursors
# --------------------------------------------------------------------------


@pytest.fixture
def steady_live() -> tuple[LiveInventory, InMemorySource, Clock]:
    clock = Clock(T0 + 400)
    source = InMemorySource()
    source.add_segments(234277, [1, 2, 3], mtime=T0)
    source.add_segments(234280, [1, 2, 3], mtime=T0)
    live = LiveInventory(
        source,
        DirectoryFeed(),
        settle_seconds=SETTLE,
        poll_seconds=30,
        clock=clock,
        autostart=False,
    )
    live.scan_once()
    return live, source, clock


def test_changes_without_a_cursor_is_a_full_resync(steady_live) -> None:
    live, _, _ = steady_live

    changes = live.changes(None)

    assert changes.resync
    assert [v.key.run for v in changes.runs] == [234277, 234280]


def test_changes_after_a_quiet_poll_sends_nothing(steady_live) -> None:
    live, _, _ = steady_live
    cursor = live.changes(None).cursor

    live.scan_once()
    changes = live.changes(cursor)

    assert not changes.resync and changes.runs == () and changes.cursor == cursor


def test_changes_sends_only_the_run_that_changed(steady_live) -> None:
    live, source, clock = steady_live
    cursor = live.changes(None).cursor

    source.touch("REFL_234280_2_234281_autoreduction.dat", mtime=clock.now)
    live.scan_once()
    changes = live.changes(cursor)

    assert [v.key.run for v in changes.runs] == [234280]


def test_a_cursor_from_another_server_process_resyncs(steady_live) -> None:
    live, source, clock = steady_live
    cursor = live.changes(None).cursor
    restarted = LiveInventory(
        source,
        DirectoryFeed(),
        settle_seconds=SETTLE,
        poll_seconds=30,
        clock=clock,
        autostart=False,
    )
    restarted.scan_once()

    assert restarted.changes(cursor).resync
    assert live.changes("garbage").resync


def test_a_run_whose_files_disappear_is_reported_removed(steady_live) -> None:
    live, source, _ = steady_live
    cursor = live.changes(None).cursor

    for name in [n for n in source.files if "234280" in n]:
        del source.files[name]
    live.scan_once()
    changes = live.changes(cursor)

    assert changes.removed == (RunKey(234280),)


def test_an_unreachable_source_keeps_the_last_listing_and_says_so(steady_live) -> None:
    """A blink of the mount must not empty the page."""
    live, source, _ = steady_live
    source.reachable = False

    live.scan_once()
    changes = live.changes(None)

    assert [v.key.run for v in changes.runs] == [234277, 234280]
    assert "cannot be reached" in changes.problems[0].message


def test_a_source_that_raises_does_not_end_polling(steady_live) -> None:
    live, source, _ = steady_live

    def broken():
        raise RuntimeError("boom")

    source.inventory = broken  # type: ignore[method-assign]
    live.scan_once()

    assert any("RuntimeError" in p.message for p in live.changes(None).problems)


# --------------------------------------------------------------------------
# The background thread, against a dead mount
# --------------------------------------------------------------------------


def wait_until(condition: Callable[[], bool], timeout: float = 5.0) -> bool:
    """Wait for *condition*; generous, so a slow machine cannot fail the test."""
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.002)
    return True


def test_a_request_never_lists_the_source_itself() -> None:
    """Only the background thread touches the mount; requests read snapshots."""
    source = InMemorySource()
    source.add_segments(234277, [1], mtime=T0)
    live = LiveInventory(
        source,
        DirectoryFeed(),
        settle_seconds=SETTLE,
        poll_seconds=30,
        clock=Clock(),
        autostart=False,
    )

    for _ in range(3):
        live.changes(None)
        live.snapshot()

    assert source.inventories == 0


def test_a_request_returns_while_the_source_listing_is_blocked() -> None:
    """A hard-mounted NFS path that has gone away blocks; the page must not."""
    gate = threading.Event()
    clock = Clock()
    source = InMemorySource(block=gate)
    live = LiveInventory(
        source,
        DirectoryFeed(),
        settle_seconds=SETTLE,
        poll_seconds=30,
        clock=clock,
        stuck_after=60,
        autostart=False,
    )
    scanner = threading.Thread(target=live.scan_once, daemon=True)
    scanner.start()
    try:
        assert source.entered.wait(timeout=5)

        answered: list = []
        request = threading.Thread(
            target=lambda: answered.append(live.changes(None)), daemon=True
        )
        request.start()
        request.join(timeout=5)
        assert answered, "a request waited for the blocked listing"
        assert answered[0].scanning and not answered[0].stuck

        clock.now += 61
        assert live.changes(None).stuck

        # Neither another caller nor the loop starts a second listing.
        live.scan_once()
        assert source.inventories == 1
    finally:
        gate.set()
        scanner.join(timeout=5)
    assert not scanner.is_alive()


def test_polling_stops_when_nobody_is_watching_and_resumes_on_a_request() -> None:
    """Idleness is judged by the injected clock, so no test waits it out."""
    clock = Clock()
    source = InMemorySource()
    live = LiveInventory(
        source,
        DirectoryFeed(),
        settle_seconds=SETTLE,
        poll_seconds=0.001,
        idle_after=600,
        clock=clock,
    )
    try:
        live.changes(None)
        assert wait_until(lambda: source.inventories >= 2)
        assert not live.idle

        clock.now += 601
        assert wait_until(lambda: live.idle)
        idle_count = source.inventories
        time.sleep(0.05)  # fifty poll intervals
        assert source.inventories == idle_count

        live.changes(None)
        assert wait_until(lambda: source.inventories > idle_count)
    finally:
        live.stop()
