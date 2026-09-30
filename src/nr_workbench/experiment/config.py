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

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nr_workbench.arrival import DEFAULT_SETTLE_SECONDS
from nr_workbench.problems import Problem
from nr_workbench.project.config import (
    DEFAULT_CATALOG_KIND,
    DEFAULT_EXPERIMENT_LOCATION,
    DEFAULT_EXPERIMENT_POLL_SECONDS,
    DEFAULT_FEED_KIND,
    DEFAULT_SOURCE_KIND,
)

# What can be set, and which choices exist yet, is defined below this package
# so the scaffold and the Settings page share it; re-exported under the names
# this package has always used.
from nr_workbench.project.experiment_schema import (  # noqa: F401 - re-exported
    CATALOG_KINDS,
    EXPERIMENT_KEYS,
    FEED_KINDS,
    RANGES,
    SOURCE_KINDS,
    normalize_ipts,
)

#: The defaults, under the names this module uses. Each is defined once, where
#: something below this package needs it too: the settle time in
#: :mod:`nr_workbench.arrival`, which ``nrw agent watch`` shares, and the
#: location and poll interval in :mod:`nr_workbench.project.config`, which the
#: scaffolded ``nrw.toml`` is rendered from. The location is provisional --
#: see there. Settled is necessary for complete, not sufficient -- see
#: :mod:`nr_workbench.experiment.status`.
DEFAULT_LOCATION = DEFAULT_EXPERIMENT_LOCATION
DEFAULT_POLL_SECONDS = DEFAULT_EXPERIMENT_POLL_SECONDS

#: The keys each experiment table may hold: the schema's, plus the catalog kind.
_KNOWN_KEYS = {
    **{table.split(".", 1)[1]: set(keys) for table, keys in EXPERIMENT_KEYS.items()},
    "catalog": {"kind"},
}


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

    kind: str = DEFAULT_SOURCE_KIND
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

    kind: str = DEFAULT_FEED_KIND
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
    catalog_kind: str = DEFAULT_CATALOG_KIND
    problems: tuple[Problem, ...] = ()

    @property
    def needs_setup(self) -> bool:
        """Whether nothing can be watched until someone says where the data is.

        From the configuration alone: the source kind is not one nrw can use,
        or no data folder can be worked out (no IPTS to fill in, say). Asking
        the data mount would block on a dead one.
        """
        return self.source.kind not in SOURCE_KINDS or self.source.path is None


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
        kind=_kind(sections["feed"], "feed", DEFAULT_FEED_KIND, problems),
        poll_seconds=_seconds(
            sections["feed"], "feed", "poll_seconds", DEFAULT_POLL_SECONDS, problems
        ),
    )
    catalog_kind = _kind(sections["catalog"], "catalog", DEFAULT_CATALOG_KIND, problems)
    problems.extend(_settings_in_comments(project, document))
    return ExperimentConfig(
        ipts=ipts,
        source=source,
        feed=feed,
        catalog_kind=catalog_kind,
        problems=tuple(problems),
    )


def _settings_in_comments(project: Any, document: dict[str, Any]) -> list[Problem]:
    """A setting typed into a comment in ``nrw.toml``: said, not silently ignored.

    The template shows each setting's default in a comment. Typing a folder over
    that default, without removing the ``#``, leaves nrw on its default location
    -- with nothing on any page to say why.
    """
    from nr_workbench.problems import one_line
    from nr_workbench.project.tomlfile import settings_in_comments

    root = getattr(project, "root", None)
    if root is None:
        return []
    try:
        text = (Path(root) / "nrw.toml").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []  # load_config has read it already; this is a hint, not a gate
    problems = []
    for found in settings_in_comments(text, EXPERIMENT_KEYS, document):
        switch_on = (
            f"remove the `#` from that line and from the `# [{found.table}]` "
            "line above it"
            if found.table_commented
            else "remove the `#` from that line"
        )
        problems.append(
            Problem(
                "config",
                f"nrw.toml line {found.line} gives `{found.key}` in a comment "
                f"({one_line(found.text)}), so it is not read and nrw's default is "
                f"used instead. To set it, {switch_on} -- or set it on the "
                "Settings page.",
            )
        )
    return problems


def _source(
    section: dict[str, Any], ipts: str | None, problems: list[Problem]
) -> SourceConfig:
    kind = _kind(section, "source", DEFAULT_SOURCE_KIND, problems)
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
    low, high = RANGES.get((f"experiment.{name}", key), (0, float("inf")))
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not low <= value <= high
        or value <= 0
    ):
        # The same range the Settings page holds a value to: one typed by hand
        # outside it (a 1 s poll loads a shared file server) is said, not used.
        problems.append(
            Problem(
                "config",
                f"[experiment.{name}] {key} must be a number of seconds from "
                f"{low:g} to {high:g}, not {value!r}; using {default:g}.",
            )
        )
        return float(default)
    return float(value)


def experiment_config_for(root: Path) -> ExperimentConfig:
    """The experiment's configuration from ``root``'s ``nrw.toml``, whatever it holds.

    A file that cannot be read gives the defaults, with the reason as a
    problem: a page that could not render over a broken ``nrw.toml`` is one
    nobody could use to fix it.
    """
    from nr_workbench.project.config import ProjectConfigError, load_config

    try:
        return experiment_config(load_config(Path(root)))
    except ProjectConfigError as exc:
        config = experiment_config(None)
        return ExperimentConfig(
            ipts=config.ipts,
            source=config.source,
            feed=config.feed,
            catalog_kind=config.catalog_kind,
            problems=(Problem("config", str(exc)), *config.problems),
        )
