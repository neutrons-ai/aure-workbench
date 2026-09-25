"""The settings a person makes for a project: its IPTS, and where its data comes from.

Read from and written to ``nrw.toml``, which stays the project's one
configuration file. This module sits below :mod:`nr_workbench.experiment` so
that the scaffold, the Settings page and the command line share one idea of
what can be set, what each choice means, and which choices exist yet.
"""

from __future__ import annotations

import re
from typing import Any

#: The data sources nrw can read, and the ones that are planned.
SOURCE_KINDS = ("local",)
PLANNED_SOURCE_KINDS = ("tiled",)

#: The ways nrw can learn that a run exists, and the ones that are planned.
FEED_KINDS = ("directory",)
PLANNED_FEED_KINDS = ("monitor", "tiled")

#: Where the catalog can be kept, and the ones that are planned.
CATALOG_KINDS = ("parquet",)
PLANNED_CATALOG_KINDS = ("api",)

#: An IPTS as written in nrw.toml: ``IPTS-34347``, ``ipts-34347`` or ``34347``.
_IPTS_RE = re.compile(r"(?:IPTS-)?([0-9]+)", re.IGNORECASE | re.ASCII)


def normalize_ipts(value: Any) -> str | None:
    """``34347`` / ``ipts-34347`` / ``IPTS-34347`` -> ``IPTS-34347``.

    Returns:
        The normalized identifier, or ``None`` when ``value`` is empty or is
        not an IPTS number.
    """
    if value is None:
        return None
    text = str(value).strip()
    match = _IPTS_RE.fullmatch(text)
    # The digits exactly as written. `int()` would turn IPTS-00001 into
    # IPTS-1 -- a different directory from the one the person typed.
    return f"IPTS-{match.group(1)}" if match else None
