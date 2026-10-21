"""Where the experiment's data is, how new runs are noticed, where the catalog lives.

Read from ``nrw.toml``::

    [experiment.source]
    kind = "local"
    location = "/SNS/REF_L/{ipts}/shared/autoreduce/new_reduction"
    settle_seconds = 300

    [experiment.feed]
    kind = "directory"
    poll_seconds = 30

    [experiment.catalog]
    kind = "parquet"

A source or a catalog whose kind is ``"tiled"`` reads ``url`` instead, an
address on the person's Tiled server (:mod:`nr_workbench.tiled.address`)::

    [experiment.source]
    kind = "tiled"
    url = "https://tiled.ornl.gov/projects/isaac/{ipts}/autoreduction"

and a key its kind does not read is said to change nothing.

Every key is optional. A project with no ``[experiment]`` table at all watches
:data:`DEFAULT_LOCATION` for its own IPTS.

**An unknown key is reported, never ignored.** ``[conventions]`` in the same
file taught this: a block that looks like configuration but controls nothing
is the first thing edited when something is not found, and editing it then
eliminates the right suspect while changing nothing.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from nr_workbench.arrival import DEFAULT_SETTLE_SECONDS
from nr_workbench.problems import Problem, one_line
from nr_workbench.project.config import (
    DEFAULT_CATALOG_KIND,
    DEFAULT_EXPERIMENT_LOCATION,
    DEFAULT_EXPERIMENT_POLL_SECONDS,
    DEFAULT_FEED_KIND,
    DEFAULT_SOURCE_KIND,
    DEFAULT_TILED_CATALOG_URL,
    DEFAULT_TILED_SOURCE_URL,
    IPTS_PLACEHOLDER,
    TILED_KIND,
)

# What can be set, and which choices exist yet, is defined below this package
# so the scaffold and the Settings page share it; re-exported under the names
# this package has always used.
from nr_workbench.project.experiment_schema import (  # noqa: F401 - re-exported
    CATALOG_KINDS,
    EXPERIMENT_KEYS,
    FEED_KINDS,
    RANGES,
    READ_BY,
    SOURCE_KINDS,
    kind_of,
    normalize_ipts,
)

if TYPE_CHECKING:
    from nr_workbench.tiled.address import Server

#: The defaults, under the names this module uses. Each is defined once, where
#: something below this package needs it too: the settle time in
#: :mod:`nr_workbench.arrival`, which ``nrw agent watch`` shares, and the
#: location and poll interval in :mod:`nr_workbench.project.config`, which the
#: scaffolded ``nrw.toml`` is rendered from. The location is provisional --
#: see there. Settled is necessary for complete, not sufficient -- see
#: :mod:`nr_workbench.experiment.status`.
DEFAULT_LOCATION = DEFAULT_EXPERIMENT_LOCATION
DEFAULT_POLL_SECONDS = DEFAULT_EXPERIMENT_POLL_SECONDS

#: The keys each experiment table may hold: the schema's.
_KNOWN_KEYS = {
    table.split(".", 1)[1]: set(keys) for table, keys in EXPERIMENT_KEYS.items()
}


@dataclass(frozen=True)
class SourceConfig:
    """Where the reduced data is read from.

    Attributes:
        kind: As configured. Checked by :func:`~nr_workbench.experiment.
            sources.open_source`, not here.
        location: The folder as configured, placeholders unresolved --
            this is what may be recorded, because it is true on every machine.
        path: ``location`` with the IPTS substituted, or ``None`` when it
            cannot be resolved (no IPTS configured), is not a folder, or is
            not what the kind reads.
        settle_seconds: How long files must be unchanged.
        url: The address on Tiled as configured, placeholders unresolved.
            Read only when ``kind`` is ``"tiled"``.
        address: ``url`` with the IPTS substituted, once held to every rule
            an address on Tiled is; ``None`` until then, or for another kind.
    """

    kind: str = DEFAULT_SOURCE_KIND
    location: str = DEFAULT_LOCATION
    path: Path | None = None
    settle_seconds: float = DEFAULT_SETTLE_SECONDS
    url: str = DEFAULT_TILED_SOURCE_URL
    address: str | None = None

    @property
    def configured(self) -> str:
        """What the kind reads, as configured: the address on Tiled, or the folder."""
        return self.url if self.kind == TILED_KIND else self.location

    @property
    def resolved(self) -> Path | str | None:
        """What the kind reads, worked out: the address, or the folder; else ``None``."""
        return self.address if self.kind == TILED_KIND else self.path


@dataclass(frozen=True)
class CatalogConfig:
    """Where the experiment's catalog is kept.

    Attributes:
        kind: As configured. Checked by :func:`~nr_workbench.experiment.
            store.open_store`, not here.
        url: Its address on Tiled as configured, placeholders unresolved.
            Read only when ``kind`` is ``"tiled"``.
        address: ``url`` resolved and held to the rules, when it is read.
    """

    kind: str = DEFAULT_CATALOG_KIND
    url: str = DEFAULT_TILED_CATALOG_URL
    address: str | None = None


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
        catalog: Where the catalog is kept.
        problems: Everything that was wrong with the table, in words a person
            can act on. Nothing here raises: an experiment page that refuses to
            render over a typo in nrw.toml is one nobody can use to fix it.
    """

    ipts: str | None = None
    source: SourceConfig = field(default_factory=SourceConfig)
    feed: FeedConfig = field(default_factory=FeedConfig)
    catalog: CatalogConfig = field(default_factory=CatalogConfig)
    problems: tuple[Problem, ...] = ()

    @property
    def catalog_kind(self) -> str:
        """The catalog's kind, as configured."""
        return self.catalog.kind

    @property
    def needs_setup(self) -> bool:
        """Whether nothing can be watched until someone says where the data is.

        From the configuration alone: the source kind is not one nrw can use,
        or no data folder or address can be worked out (no IPTS to fill in,
        say). Asking the data mount would block on a dead one.
        """
        return self.source.kind not in SOURCE_KINDS or self.source.resolved is None


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
                    f"[experiment.{name}] {one_line(key)} is not a setting and "
                    f"changes nothing. Known: {', '.join(sorted(known))}.",
                )
            )
        sections[name] = section

    from nr_workbench.tiled.address import lazily

    server = lazily(getattr(project, "root", None))
    source = _source(sections["source"], ipts, problems, server)
    feed = FeedConfig(
        kind=_kind(sections["feed"], "feed", problems),
        poll_seconds=_seconds(
            sections["feed"], "feed", "poll_seconds", DEFAULT_POLL_SECONDS, problems
        ),
    )
    catalog = _catalog(sections["catalog"], ipts, problems, server)
    problems.extend(_settings_in_comments(project, document))
    return ExperimentConfig(
        ipts=ipts,
        source=source,
        feed=feed,
        catalog=catalog,
        problems=tuple(problems),
    )


def _text(
    section: dict[str, Any],
    table: str,
    key: str,
    kind: str,
    problems: list[Problem],
) -> str | None:
    """A folder or an address, as written; ``None`` when it is read and is not text.

    A key the table's kind does not read, set to anything but its default,
    is said to change nothing. Configuration that controls nothing is the
    first thing edited when something is not found; see the module's
    docstring. The default written out, as an uncommented placeholder is, is
    no setting, so it is not said.
    """
    default = str(EXPERIMENT_KEYS[f"experiment.{table}"][key])
    value = section.get(key, default)
    text = value.strip() if isinstance(value, str) else ""
    reads = READ_BY[(f"experiment.{table}", key)]
    if kind != reads:
        if key in section and text != default:
            problems.append(
                Problem(
                    "config",
                    f'[experiment.{table}] {key} is read only when kind = "{reads}", '
                    f"and kind is {kind!r}; it changes nothing.",
                )
            )
        return text or default
    if not text:
        what = "an address" if key == "url" else "a path"
        problems.append(
            Problem("config", f"[experiment.{table}] {key} must be {what}.")
        )
        return None
    return text


def _tiled_address(
    url: str | None,
    ipts: str | None,
    table: str,
    server: Callable[[], Server],
    problems: list[Problem],
) -> str | None:
    """An address on Tiled, filled in; or ``None``, with the reason as a problem.

    ``None`` too for an address that is not text, already said by
    :func:`_text`: never nrw's default in its place, which would read, or
    write, a container nobody chose.
    """
    from nr_workbench.tiled.address import address_problem, resolved, shown

    if url is None:
        return None
    name = f"[experiment.{table}] url"
    problem = address_problem(url, server(), name)
    if problem is not None:
        problems.append(Problem(table, problem))
        return None
    if IPTS_PLACEHOLDER in url and ipts is None:
        problems.append(
            Problem(
                table,
                f"{name} {shown(url)} needs the project's IPTS, and nrw.toml has "
                "none. Set [beamtime] ipts.",
            )
        )
        return None
    try:
        return resolved(url, ipts)
    except ValueError as exc:  # an IPTS normalize_ipts did not make: never one
        problems.append(Problem(table, f"{name}: {exc}."))
        return None


def _catalog(
    section: dict[str, Any],
    ipts: str | None,
    problems: list[Problem],
    server: Callable[[], Server],
) -> CatalogConfig:
    kind = _kind(section, "catalog", problems)
    url = _text(section, "catalog", "url", kind, problems)
    if kind != TILED_KIND:
        return CatalogConfig(kind=kind, url=url or DEFAULT_TILED_CATALOG_URL)
    address = _tiled_address(url, ipts, "catalog", server, problems)
    return CatalogConfig(kind=kind, url=url or "", address=address)


def _settings_in_comments(project: Any, document: dict[str, Any]) -> list[Problem]:
    """A setting typed into a comment in ``nrw.toml``: said, not silently ignored.

    The template shows each setting's default in a comment. Typing a folder over
    that default, without removing the ``#``, leaves nrw on its default location
    -- with nothing on any page to say why.
    """
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
        # An address may hold a key (`?api_key=`), and this is shown on pages
        # anyone on the machine can read: its line is named, not repeated.
        written = "" if found.key == "url" else f" ({one_line(found.text)})"
        problems.append(
            Problem(
                "config",
                f"nrw.toml line {found.line} gives `{found.key}` in a comment"
                f"{written}, so it is not read and nrw's default is used "
                f"instead. To set it, {switch_on} -- or set it on the Settings "
                "page.",
            )
        )
    return problems


def _source(
    section: dict[str, Any],
    ipts: str | None,
    problems: list[Problem],
    server: Callable[[], Server],
) -> SourceConfig:
    kind = _kind(section, "source", problems)
    # A folder that is not text reads the default folder, as it always has; an
    # address that is not text reads nothing (see _tiled_address).
    location = _text(section, "source", "location", kind, problems) or DEFAULT_LOCATION
    url = _text(section, "source", "url", kind, problems)
    settle = _seconds(
        section, "source", "settle_seconds", DEFAULT_SETTLE_SECONDS, problems
    )
    if kind == TILED_KIND:
        address = _tiled_address(url, ipts, "source", server, problems)
        return SourceConfig(
            kind=kind,
            location=location,
            settle_seconds=settle,
            url=url or "",
            address=address,
        )

    path: Path | None
    if IPTS_PLACEHOLDER in location and ipts is None:
        path = None
        problems.append(
            Problem(
                "source",
                f"The data location {one_line(location)} needs the project's "
                "IPTS, and nrw.toml has none. Set [beamtime] ipts, or give "
                "[experiment.source] location as a full path.",
            )
        )
    else:
        # `replace`, not `format`: a path may legitimately contain braces, and
        # `format` would try to fill them.
        path = Path(location.replace(IPTS_PLACEHOLDER, ipts or ""))
        if not path.is_absolute():
            problems.append(
                Problem(
                    "source",
                    f"The data location {one_line(location)} is not an absolute "
                    "path, so it would mean something different from every "
                    "directory nrw is started in.",
                )
            )
            path = None

    return SourceConfig(
        kind=kind,
        location=location,
        path=path,
        settle_seconds=settle,
        url=url or DEFAULT_TILED_SOURCE_URL,
    )


def _kind(section: dict[str, Any], name: str, problems: list[Problem]) -> str:
    """The configured kind, as :func:`kind_of` reads it.

    Deliberately not checked against what is implemented: falling back to the
    local folder when someone asked for Tiled would quietly watch a path they
    never chose. The registries in ``sources`` and ``feeds`` refuse an
    unimplemented kind by name, which is the failure worth seeing.
    """
    value = section.get("kind")
    if "kind" in section and (not isinstance(value, str) or not value.strip()):
        problems.append(
            Problem(
                "config", f"[experiment.{name}] kind must be a name, not {value!r}."
            )
        )
    return kind_of(section, name)


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
            catalog=config.catalog,
            problems=(Problem("config", str(exc)), *config.problems),
        )
