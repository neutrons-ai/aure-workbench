"""The settings a person makes for a project: its IPTS, and where its data comes from.

Read from and written to ``nrw.toml``, which stays the project's one
configuration file. This module sits below :mod:`nr_workbench.experiment` so
that the scaffold, the Settings page and the command line share one idea of
what can be set, what each choice means, and which choices exist yet.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from nr_workbench.arrival import DEFAULT_SETTLE_SECONDS
from nr_workbench.project.config import (
    DEFAULT_EXPERIMENT_LOCATION,
    DEFAULT_EXPERIMENT_POLL_SECONDS,
)
from nr_workbench.project.tomlfile import Value, key_line, placeholder_line


@dataclass(frozen=True)
class Option:
    """One choice the Settings page offers, and whether it exists yet.

    Attributes:
        kind: The name written in ``nrw.toml``.
        label: How the page names it.
        available: Whether nrw can use it today. A planned one is shown, so
            people know it is coming, but cannot be chosen.
        detail: One sentence on what it does, or what it will do.
    """

    kind: str
    label: str
    available: bool
    detail: str


#: Where the reduced data can come from.
SOURCE_OPTIONS = (
    Option(
        "local",
        "A folder on this machine",
        True,
        "Reads the reduced files from one folder, usually on the data mount.",
    ),
    Option(
        "tiled",
        "Tiled",
        False,
        "Coming: for working away from the data mount. At ORNL an experiment "
        "is the container projects/isaac/IPTS-<n> on tiled.ornl.gov.",
    ),
)

#: How nrw learns that a run exists.
FEED_OPTIONS = (
    Option(
        "directory",
        "Files appearing in the data folder",
        True,
        "A run is noticed when its reduced files appear.",
    ),
    Option(
        "monitor",
        "SNS web monitor",
        False,
        "Coming: reports the run being measured, so a run can count as "
        "complete once its last angle is reduced -- the last run of a "
        "beamtime included.",
    ),
    Option(
        "tiled",
        "Tiled",
        False,
        "Coming: Tiled can announce runs whatever the data comes from.",
    ),
)

#: The data sources nrw can read, and the ones that are planned.
SOURCE_KINDS = tuple(o.kind for o in SOURCE_OPTIONS if o.available)
PLANNED_SOURCE_KINDS = tuple(o.kind for o in SOURCE_OPTIONS if not o.available)

#: The ways nrw can learn that a run exists, and the ones that are planned.
FEED_KINDS = tuple(o.kind for o in FEED_OPTIONS if o.available)
PLANNED_FEED_KINDS = tuple(o.kind for o in FEED_OPTIONS if not o.available)

#: Where the catalog can be kept, and the ones that are planned.
CATALOG_KINDS = ("parquet",)
PLANNED_CATALOG_KINDS = ("api",)

#: The experiment keys nrw writes, by table, each with nrw's default. A key
#: left unset follows the default -- which matters because the default
#: location is provisional and expected to move.
EXPERIMENT_KEYS: dict[str, dict[str, Value]] = {
    "experiment.source": {
        "kind": SOURCE_KINDS[0],
        "location": DEFAULT_EXPERIMENT_LOCATION,
        "settle_seconds": DEFAULT_SETTLE_SECONDS,
    },
    "experiment.feed": {
        "kind": FEED_KINDS[0],
        "poll_seconds": DEFAULT_EXPERIMENT_POLL_SECONDS,
    },
}

#: What a table of experiment settings holds: the keys that are set.
ExperimentValues = Mapping[str, Mapping[str, Value]]

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


def written_experiment(document: Mapping[str, Any]) -> dict[str, dict[str, Value]]:
    """The experiment keys a parsed ``nrw.toml`` sets, as written.

    Only nrw's own keys, and only values it can write back: a file whose
    experiment tables hold anything else is not one nrw rendered, and is
    left to its people.
    """
    table = document.get("experiment")
    found: dict[str, dict[str, Value]] = {}
    if not isinstance(table, Mapping):
        return found
    for name, defaults in EXPERIMENT_KEYS.items():
        section = table.get(name.split(".", 1)[1])
        if not isinstance(section, Mapping):
            continue
        kept = {
            key: section[key]
            for key in defaults
            if key in section and isinstance(section[key], str | int | float | bool)
        }
        if kept:
            found[name] = kept
    return found


def experiment_block(values: ExperimentValues) -> str:
    """The experiment tables as nrw writes them, without a final newline.

    One function for ``nrw init``'s template and for the Settings page, so
    the two write the same bytes and ``nrw init`` finds nothing to do after
    a save. A table with nothing set stays commented out -- a fresh project,
    and advice to "add this table", keep working. A table with something set
    is written out, its unset keys as commented placeholders showing the
    default they follow.
    """
    blocks = []
    for table, defaults in EXPERIMENT_KEYS.items():
        chosen = values.get(table, {})
        active = any(key in chosen for key in defaults)
        lines = [f"[{table}]" if active else f"# [{table}]"]
        for key, default in defaults.items():
            if key in chosen:
                lines.append(key_line(key, chosen[key]))
            else:
                lines.append(placeholder_line(key, default))
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)

