"""Warning channel for the analysis layer.

The original tool called ``notify(...)`` from inside its
numerical routines. Keeping click out of ``metrics/`` is what makes those
functions importable and testable without a CLI, so the calls route through
this shim instead -- same destination, no dependency.

The messages matter. "101 interval file(s) listed in JSON not found" is a fact
about the data, not chatter, and a caller that is not a terminal -- the web UI,
or an agent reading ``--result-out`` -- needs somewhere to put it other than a
log nobody reads. :func:`collect` redirects the stream so those callers can
show the warnings where the person looking at the data will see them.
"""

from __future__ import annotations

import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager

#: Active collector for this thread, or None to write to stderr. Thread-local
#: because a web server handles requests concurrently, and one request's
#: warnings must not land in another's response.
_local = threading.local()


def notify(message: str) -> None:
    """Emit a diagnostic.

    Goes to stderr unless a :func:`collect` block is active on this thread, in
    which case it is collected instead.

    Args:
        message: The message to emit.
    """
    collector = getattr(_local, "collector", None)
    if collector is None:
        print(message, file=sys.stderr)
    else:
        collector.append(message)


@contextmanager
def collect() -> Iterator[list[str]]:
    """Capture :func:`notify` messages raised inside the block.

    Nests: an inner block collects on its own list and restores the outer one
    on exit, so a caller cannot accidentally silence its caller's warnings.

    Yields:
        The list of messages, populated as the block runs.
    """
    messages: list[str] = []
    previous = getattr(_local, "collector", None)
    _local.collector = messages
    try:
        yield messages
    finally:
        _local.collector = previous
