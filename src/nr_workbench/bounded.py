"""Running something that may never answer: a read on a data mount that went away.

A hard-mounted NFS path that has gone away does not fail, it *blocks*, and no
Python code can interrupt a thread stuck inside it. What can be bounded is how
long a caller waits and how many threads are left waiting.

:class:`Bounded` runs a call on a daemon thread and waits at most a deadline.
A daemon thread rather than a pool worker: a pool's workers are joined when
the interpreter exits, so one stuck on a dead mount would make stopping
``nrw serve`` -- or a command-line check -- hang as well. A fixed number of
slots caps how many can be stuck at once; a call that finds them all taken is
refused at once instead of joining the queue behind a mount that will not
answer.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any


class TimedOut(TimeoutError):
    """The call did not finish in time -- usually a data mount that stopped answering."""


class Busy(Exception):
    """Every slot is held by a call still waiting, so this one was not started."""


class Bounded:
    """Calls with a deadline, and a cap on how many may be waiting at once.

    Args:
        slots: How many calls may run at once.
        timeout: Seconds a caller waits for one.
        busy: What :class:`Busy` says when the slots are all taken.
        name: Name for the threads, for a stack dump of a stuck server.
    """

    def __init__(self, *, slots: int, timeout: float, busy: str, name: str) -> None:
        self._slots = threading.BoundedSemaphore(slots)
        self.timeout = timeout
        self._busy = busy
        self._name = name

    def run(self, function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """``function(*args, **kwargs)``, if it finishes within the deadline.

        Raises:
            Busy: Every slot is held by a call still waiting.
            TimedOut: It did not finish in time. The call goes on in the
                background, holding its slot until it returns.
        """
        if not self._slots.acquire(blocking=False):
            raise Busy(self._busy)
        outcome: dict[str, Any] = {}
        done = threading.Event()

        def call() -> None:
            try:
                outcome["value"] = function(*args, **kwargs)
            except BaseException as exc:  # noqa: BLE001 - handed to the caller
                outcome["error"] = exc
            finally:
                done.set()
                self._slots.release()

        try:
            threading.Thread(target=call, name=self._name, daemon=True).start()
        except BaseException:
            self._slots.release()  # the thread that would give it back never ran
            raise
        if not done.wait(self.timeout):
            raise TimedOut(
                f"The data source did not answer within {self.timeout:.0f}s; the "
                "data mount may be unavailable."
            )
        if "error" in outcome:
            raise outcome["error"]
        return outcome["value"]
