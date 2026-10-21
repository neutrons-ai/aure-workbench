"""What can be set about an experiment, and how nrw writes it into ``nrw.toml``.

The one registry of the experiment's settings -- which keys exist, their
defaults and ranges, the source and watcher choices that exist and those
still coming -- and the one renderer of the experiment tables, which the
scaffold template and the ``nrw.toml`` writer share, so ``nrw init`` renders
back exactly what a save wrote.

It imports nothing from the modules that use it (the renderer, the writer,
the experiment package), so any of them can import it at load time.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from nr_workbench.arrival import DEFAULT_SETTLE_SECONDS
from nr_workbench.project.config import (
    DEFAULT_CATALOG_KIND,
    DEFAULT_EXPERIMENT_LOCATION,
    DEFAULT_EXPERIMENT_POLL_SECONDS,
    DEFAULT_FEED_KIND,
    DEFAULT_SOURCE_KIND,
    DEFAULT_TILED_CATALOG_URL,
    DEFAULT_TILED_SOURCE_URL,
    TILED_KIND,
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
        TILED_KIND,
        "Tiled",
        False,
        "Coming: reads the same reduced files from the experiment's container "
        "on Tiled, from any machine -- at ORNL, projects/isaac/IPTS-<n>/"
        "autoreduction on tiled.ornl.gov.",
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
        TILED_KIND,
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

#: Where the experiment's samples, runs and their conditions are kept. Tiled
#: is how they will be shared -- between projects, and with other programs --
#: rather than by several projects writing one set of files.
CATALOG_OPTIONS = (
    Option(
        DEFAULT_CATALOG_KIND,
        "Parquet files in this project",
        True,
        "Two tables and a manifest in experiment/, committed with the project.",
    ),
    Option(
        TILED_KIND,
        "Tiled",
        False,
        "Coming: samples, runs and their conditions kept in the experiment's "
        "container on Tiled, where everyone on the experiment -- and other "
        "programs -- read and edit them, live.",
    ),
)

#: Where the catalog can be kept, and the ones that are planned.
CATALOG_KINDS = tuple(o.kind for o in CATALOG_OPTIONS if o.available)
PLANNED_CATALOG_KINDS = tuple(o.kind for o in CATALOG_OPTIONS if not o.available)

#: The experiment keys nrw writes, by table, each with nrw's default. A key
#: left unset follows the default -- which matters because the default
#: location is provisional and expected to move.
#:
#: **One default per key, whatever the others say.** A folder is ``location``
#: and an address on Tiled is ``url``, each read only for its own kind: a key
#: whose default followed another key's value would leave its placeholder
#: stale whenever that value changed by hand, and the render back, the check
#: for a setting typed into a comment, and the empty-table guard all compare
#: against one default.
EXPERIMENT_KEYS: dict[str, dict[str, Value]] = {
    "experiment.source": {
        "kind": DEFAULT_SOURCE_KIND,
        "location": DEFAULT_EXPERIMENT_LOCATION,
        "url": DEFAULT_TILED_SOURCE_URL,
        "settle_seconds": DEFAULT_SETTLE_SECONDS,
    },
    "experiment.feed": {
        "kind": DEFAULT_FEED_KIND,
        "poll_seconds": DEFAULT_EXPERIMENT_POLL_SECONDS,
    },
    "experiment.catalog": {
        "kind": DEFAULT_CATALOG_KIND,
        "url": DEFAULT_TILED_CATALOG_URL,
    },
}

#: Each place a table reads, and the kind that reads it there. A key its
#: table's kind does not read changes nothing, and is said to when it is set.
READ_BY: dict[tuple[str, str], str] = {
    ("experiment.source", "location"): DEFAULT_SOURCE_KIND,
    ("experiment.source", "url"): TILED_KIND,
    ("experiment.catalog", "url"): TILED_KIND,
}

#: What a table of experiment settings holds: the keys that are set.
ExperimentValues = Mapping[str, Mapping[str, Value]]


def section_of(document: Mapping[str, Any], table: str) -> Mapping[str, Any]:
    """``[experiment.<table>]`` in a parsed ``nrw.toml``, or ``{}``."""
    experiment = document.get("experiment")
    section = experiment.get(table) if isinstance(experiment, Mapping) else None
    return section if isinstance(section, Mapping) else {}


def kind_of(section: Mapping[str, Any], table: str) -> str:
    """A table's kind as nrw reads it: the name written, stripped, or the default.

    The one reading of a kind, for the reader of ``nrw.toml`` and for the
    checks of a change, so the two never take one file two ways.

    Args:
        section: The table, e.g. from :func:`section_of`.
        table: ``"source"``, ``"feed"`` or ``"catalog"``.
    """
    kind = section.get("kind")
    if isinstance(kind, str) and kind.strip():
        return kind.strip()
    return str(EXPERIMENT_KEYS[f"experiment.{table}"]["kind"])


#: An IPTS as written in nrw.toml: ``IPTS-34347``, ``ipts-34347`` or ``34347``.
# At most eight digits: IPTS numbers have five or six, and it becomes a path.
_IPTS_RE = re.compile(r"(?:IPTS-)?([0-9]{1,8})", re.IGNORECASE | re.ASCII)


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


#: Seconds a run's files must be unchanged: long enough that a file still
#: being written is not taken, short enough to notice a run within the hour.
SETTLE_RANGE = (10, 3600)

#: Seconds between polls: often enough to watch a beamtime, rarely enough not
#: to load a shared file server.
POLL_RANGE = (5, 600)

MAX_LOCATION = 1024
MAX_LABEL = 100

#: The range each numeric experiment setting must fall in, by table and key.
RANGES: dict[tuple[str, str], tuple[int, int]] = {
    ("experiment.source", "settle_seconds"): SETTLE_RANGE,
    ("experiment.feed", "poll_seconds"): POLL_RANGE,
}

#: The settings a change may name: the IPTS and label, and every experiment
#: key as ``<table>.<key>`` without the ``experiment.`` prefix.
CHANGE_KEYS = ("ipts", "label") + tuple(
    f"{table.split('.', 1)[1]}.{key}"
    for table, keys in EXPERIMENT_KEYS.items()
    for key in keys
)

#: The confirmation a change of IPTS needs while the catalog holds runs.
CONFIRM_IPTS_CHANGE = "ipts-change"
