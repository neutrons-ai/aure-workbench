"""Calls with a deadline: what a request on a dead data mount costs.

A read on a hard-mounted NFS path that has gone away blocks for good, and no
Python code can interrupt it. What :class:`Bounded` promises instead is that
the caller waits no longer than the deadline, that only so many calls can be
stuck at once, and that a stuck call does not keep the process alive.
"""

from __future__ import annotations

import subprocess
import sys
import threading

import pytest

from nr_workbench.bounded import Bounded, Busy, TimedOut

NAME = "test-bounded"


def bounded(slots: int = 1, timeout: float = 5.0) -> Bounded:
    return Bounded(slots=slots, timeout=timeout, busy="all taken", name=NAME)


def join_stuck_calls() -> None:
    for thread in threading.enumerate():
        if thread.name == NAME:
            thread.join(timeout=5)


def test_a_call_that_finishes_in_time_returns_its_value() -> None:
    assert bounded().run(lambda a, b=0: a + b, 1, b=2) == 3


def test_an_error_in_the_call_reaches_the_caller() -> None:
    def fails() -> None:
        raise ValueError("the reason")

    with pytest.raises(ValueError, match="the reason"):
        bounded().run(fails)


def test_a_late_call_times_out_and_holds_its_slot_until_it_returns() -> None:
    gate = threading.Event()
    runner = bounded(slots=1, timeout=0.05)
    try:
        with pytest.raises(TimedOut):
            runner.run(gate.wait)
        with pytest.raises(Busy, match="all taken"):
            runner.run(lambda: "not started")
    finally:
        gate.set()
        join_stuck_calls()

    assert runner.run(lambda: "started") == "started"


def test_a_call_that_cannot_start_gives_its_slot_back(monkeypatch) -> None:
    runner = bounded(slots=1)

    def cannot_start(self) -> None:
        raise RuntimeError("can't start new thread")

    with monkeypatch.context() as patched:
        patched.setattr(threading.Thread, "start", cannot_start)
        with pytest.raises(RuntimeError, match="can't start"):
            runner.run(lambda: "never")

    assert runner.run(lambda: "started") == "started"


def test_a_stuck_call_does_not_keep_the_process_alive() -> None:
    """Why a daemon thread, not a pool: a pool joins its workers at exit."""
    script = (
        "import threading\n"
        "from nr_workbench.bounded import Bounded, TimedOut\n"
        "runner = Bounded(slots=1, timeout=0.1, busy='', name='stuck')\n"
        "try:\n"
        "    runner.run(threading.Event().wait)\n"
        "except TimedOut:\n"
        "    print('timed out')\n"
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "timed out"
