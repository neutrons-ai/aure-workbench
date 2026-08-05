"""Warning channel for the analysis layer.

The original tool called ``notify(...)`` from inside its
numerical routines. Keeping click out of ``metrics/`` is what makes those
functions importable and testable without a CLI, so the calls route through
this shim instead -- same destination, no dependency.
"""

from __future__ import annotations

import sys


def notify(message: str) -> None:
    """Write a diagnostic to stderr.

    Args:
        message: The message to emit.
    """
    print(message, file=sys.stderr)
