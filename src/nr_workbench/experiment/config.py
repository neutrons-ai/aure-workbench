"""Where the experiment's data is, how new runs are noticed, where the catalog lives.

Read from ``nrw.toml``::

    [experiment.source]
    kind = "local"
    location = "/SNS/REF_L/{ipts}/shared/autoreduce/new_reduction"
    settle_seconds = 300

    [experiment.feed]
    kind = "directory"
    poll_seconds = 30

Every key is optional. A project with no ``[experiment]`` table at all watches
:data:`DEFAULT_LOCATION` for its own IPTS.

**An unknown key is reported, never ignored.** ``[conventions]`` in the same
file taught this: a block that looks like configuration but controls nothing
is the first thing edited when something is not found, and editing it then
eliminates the right suspect while changing nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nr_workbench.problems import Problem

#: Where REF_L's ``new_reduction`` pipeline writes reduced runs.
#:
#: **Provisional.** This is where the pipeline writes today, and it is expected
#: to move -- which is why it is one constant rather than a string repeated in
#: the docs, the template and the code. Override it per project with
#: ``[experiment.source] location``.
DEFAULT_LOCATION = "/SNS/REF_L/{ipts}/shared/autoreduce/new_reduction"

#: How long a run's files must be unchanged before they count as settled.
#: Settled is necessary for complete, not sufficient -- see
#: :mod:`nr_workbench.experiment.status`.
DEFAULT_SETTLE_SECONDS = 300.0

#: Seconds between polls of the feed and the source while someone is watching.
DEFAULT_POLL_SECONDS = 30.0

#: The data sources nrw can read, and the ones that are planned.
SOURCE_KINDS = ("local",)
PLANNED_SOURCE_KINDS = ("tiled",)

#: The ways nrw can learn that a run exists, and the ones that are planned.
FEED_KINDS = ("directory",)
PLANNED_FEED_KINDS = ("monitor", "tiled")

#: Where the catalog can be kept, and the ones that are planned.
CATALOG_KINDS = ("parquet",)
PLANNED_CATALOG_KINDS = ("api",)

_KNOWN_KEYS = {
    "source": {"kind", "location", "settle_seconds"},
    "feed": {"kind", "poll_seconds"},
    "catalog": {"kind"},
}

#: An IPTS as written in nrw.toml: ``IPTS-34347``, ``ipts-34347`` or ``34347``.
_IPTS_RE = re.compile(r"(?:IPTS-)?([0-9]+)", re.IGNORECASE | re.ASCII)


@dataclass(frozen=True)
class SourceConfig:
    """Where the reduced data is read from.

    Attributes:
        kind: As configured. Checked by :func:`~nr_workbench.experiment.
            sources.open_source`, not here.
        location: The location as configured, placeholders unresolved --
            this is what may be recorded, because it is true on every machine.
        path: ``location`` with the IPTS substituted, or ``None`` when it
            cannot be resolved (no IPTS configured).
        settle_seconds: How long files must be unchanged.
    """

    kind: str = "local"
    location: str = DEFAULT_LOCATION
    path: Path | None = None
    settle_seconds: float = DEFAULT_SETTLE_SECONDS


@dataclass(frozen=True)
class FeedConfig:
    """How new runs are noticed.

    Attributes:
        kind: As configured. Checked by :func:`~nr_workbench.experiment.
            feeds.open_feed`, not here.
        poll_seconds: Seconds between polls while someone is watching.
    """

    kind: str = "directory"
    poll_seconds: float = DEFAULT_POLL_SECONDS


@dataclass(frozen=True)
class ExperimentConfig:
    """The whole ``[experiment]`` table, resolved.

    Attributes:
        ipts: The project's IPTS, normalized to ``IPTS-<n>``, or ``None``.
        source: Where the data comes from.
        feed: How new runs are noticed.
        catalog_kind: As configured.
        problems: Everything that was wrong with the table, in words a person
            can act on. Nothing here raises: an experiment page that refuses to
            render over a typo in nrw.toml is one nobody can use to fix it.
    """

    ipts: str | None = None
    source: SourceConfig = field(default_factory=SourceConfig)
    feed: FeedConfig = field(default_factory=FeedConfig)
    catalog_kind: str = "parquet"
    problems: tuple[Problem, ...] = ()


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


def experiment_config(project: Any) -> ExperimentConfig:
    """Resolve the ``[experiment]`` table of a project's ``nrw.toml``.

    Args:
        project: A :class:`nr_workbench.project.config.ProjectConfig`, or
            ``None`` when nrw.toml could not be read.

    Returns:
        The resolved configuration, with any problems listed rather than
        raised.
    """
    problems: list[Problem] = []
    raw_ipts = getattr(project, "ipts", None)
    ipts = normalize_ipts(raw_ipts)
    if raw_ipts and ipts is None:
        problems.append(
            Problem(
                "config",
                f"[beamtime] ipts = {raw_ipts!r} is not an IPTS number, so the "
                "data location cannot be filled in.",
            )
        )

    document = getattr(project, "raw", None) or {}
    table = document.get("experiment", {})
    if not isinstance(table, dict):
        problems.append(Problem("config", "[experiment] must be a table."))
        table = {}

    for name in sorted(set(table) - set(_KNOWN_KEYS)):
        problems.append(
            Problem(
                "config",
                f"[experiment] has no {name!r} section; it is ignored. Known "
                f"sections: {', '.join(sorted(_KNOWN_KEYS))}.",
            )
        )

    sections: dict[str, dict[str, Any]] = {}
    for name, known in _KNOWN_KEYS.items():
        section = table.get(name, {})
        if not isinstance(section, dict):
            problems.append(Problem("config", f"[experiment.{name}] must be a table."))
            section = {}
        for key in sorted(set(section) - known):
            problems.append(
                Problem(
                    "config",
                    f"[experiment.{name}] {key} is not a setting and changes "
                    f"nothing. Known: {', '.join(sorted(known))}.",
                )
            )
        sections[name] = section

    source = _source(sections["source"], ipts, problems)
    feed = FeedConfig(
        kind=_kind(sections["feed"], "feed", FEED_KINDS[0], problems),
        poll_seconds=_seconds(
            sections["feed"], "feed", "poll_seconds", DEFAULT_POLL_SECONDS, problems
        ),
    )
    catalog_kind = _kind(sections["catalog"], "catalog", CATALOG_KINDS[0], problems)
    return ExperimentConfig(
        ipts=ipts,
        source=source,
        feed=feed,
        catalog_kind=catalog_kind,
        problems=tuple(problems),
    )


def _source(
    section: dict[str, Any], ipts: str | None, problems: list[Problem]
) -> SourceConfig:
    kind = _kind(section, "source", SOURCE_KINDS[0], problems)
    location = section.get("location", DEFAULT_LOCATION)
    if not isinstance(location, str) or not location.strip():
        problems.append(
            Problem("config", "[experiment.source] location must be a path.")
        )
        location = DEFAULT_LOCATION
    location = location.strip()

    path: Path | None
    if "{ipts}" in location and ipts is None:
        path = None
        problems.append(
            Problem(
                "source",
                f"The data location {location} needs the project's IPTS, and "
                "nrw.toml has none. Set [beamtime] ipts, or give "
                "[experiment.source] location as a full path.",
            )
        )
    else:
        # `replace`, not `format`: a path may legitimately contain braces, and
        # `format` would try to fill them.
        path = Path(location.replace("{ipts}", ipts or ""))
        if not path.is_absolute():
            problems.append(
                Problem(
                    "source",
                    f"The data location {location} is not an absolute path, so "
                    "it would mean something different from every directory "
                    "nrw is started in.",
                )
            )
            path = None

    return SourceConfig(
        kind=kind,
        location=location,
        path=path,
        settle_seconds=_seconds(
            section, "source", "settle_seconds", DEFAULT_SETTLE_SECONDS, problems
        ),
    )


def _kind(
    section: dict[str, Any], name: str, default: str, problems: list[Problem]
) -> str:
    """The configured kind, verbatim.

    Deliberately not checked against what is implemented: falling back to the
    local folder when someone asked for Tiled would quietly watch a path they
    never chose. The registries in ``sources`` and ``feeds`` refuse an
    unimplemented kind by name, which is the failure worth seeing.
    """
    value = section.get("kind", default)
    if not isinstance(value, str) or not value.strip():
        problems.append(
            Problem(
                "config", f"[experiment.{name}] kind must be a name, not {value!r}."
            )
        )
        return default
    return value.strip()


def _seconds(
    section: dict[str, Any],
    name: str,
    key: str,
    default: float,
    problems: list[Problem],
) -> float:
    value = section.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        problems.append(
            Problem(
                "config",
                f"[experiment.{name}] {key} must be a positive number of "
                f"seconds, not {value!r}; using {default:g}.",
            )
        )
        return default
    return float(value)
