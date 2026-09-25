"""The settings a person makes for a project: its IPTS, and where its data comes from.

Read from and written to ``nrw.toml``, which stays the project's one
configuration file. This module sits below :mod:`nr_workbench.experiment` so
that the scaffold, the Settings page and the command line share one idea of
what can be set, what each choice means, and which choices exist yet.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nr_workbench.arrival import DEFAULT_SETTLE_SECONDS
from nr_workbench.project.config import (
    DEFAULT_EXPERIMENT_LOCATION,
    DEFAULT_EXPERIMENT_POLL_SECONDS,
)
from nr_workbench.project.tomlfile import (
    Changes,
    Set,
    Unset,
    Value,
    key_line,
    placeholder_line,
)


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


# ---------------------------------------------------------------------------
# Changing settings
# ---------------------------------------------------------------------------

#: The settings a change may name.
CHANGE_KEYS = (
    "ipts",
    "label",
    "source.kind",
    "source.location",
    "source.settle_seconds",
    "feed.kind",
    "feed.poll_seconds",
)

#: Seconds a run's files must be unchanged: long enough that a file still
#: being written is not taken, short enough to notice a run within the hour.
SETTLE_RANGE = (10, 3600)

#: Seconds between polls: often enough to watch a beamtime, rarely enough not
#: to load a shared file server.
POLL_RANGE = (5, 600)

MAX_LOCATION = 1024
MAX_LABEL = 100

#: Characters no single-line setting may hold: control characters, and the
#: Unicode line and paragraph separators that editors show as line breaks.
_CONTROL = re.compile(r"[\x00-\x1f\x7f\x85\u2028\u2029]")


class SettingsError(ValueError):
    """A setting cannot take the value asked for. The message says why."""


class NeedsConfirmation(Exception):
    """The change is allowed, once a person has confirmed it.

    Attributes:
        needs: What to confirm, e.g. ``"ipts-change"``, sent back with the
            change to confirm it.
    """

    def __init__(self, needs: str, message: str) -> None:
        super().__init__(message)
        self.needs = needs


@dataclass(frozen=True)
class Current:
    """The settings as ``nrw.toml`` has them now.

    Attributes:
        revision: sha256 of the file, to send back with a change.
        ipts: ``[beamtime] ipts`` as written, or ``""``.
        label: ``[beamtime] label`` as written, or ``""``.
        experiment: The experiment keys that are set, by table.
    """

    revision: str
    ipts: str
    label: str
    experiment: dict[str, dict[str, Value]]


@dataclass(frozen=True)
class SaveResult:
    """What a save did, or would do.

    Attributes:
        changed: Whether ``nrw.toml`` differs.
        diff: The change to ``nrw.toml``, as a unified diff.
        written: Project-relative paths written.
        backup: Where the previous ``nrw.toml`` was kept.
        notes: What else a person should know.
        warnings: Doubts about the values, which did not stop the save.
    """

    changed: bool
    diff: str
    written: tuple[str, ...] = ()
    backup: str | None = None
    notes: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        """Return the JSON form."""
        return {
            "changed": self.changed,
            "diff": self.diff,
            "written": list(self.written),
            "backup": self.backup,
            "notes": list(self.notes),
            "warnings": list(self.warnings),
        }


def read(root: Path) -> Current:
    """The settings ``nrw.toml`` holds now.

    Raises:
        TomlEditError: The file cannot be edited safely (see
            :func:`nr_workbench.project.tomlfile.read_config`).
        OSError: It cannot be read.
    """
    import tomllib

    from nr_workbench.project.layout import ProjectLayout
    from nr_workbench.project.tomlfile import read_config

    base = read_config(ProjectLayout(root=Path(root)).config_file)
    document = tomllib.loads(base.text)
    beamtime = document.get("beamtime")
    beamtime = beamtime if isinstance(beamtime, Mapping) else {}
    return Current(
        revision=base.revision,
        ipts=_text(beamtime.get("ipts")),
        label=_text(beamtime.get("label")),
        experiment=written_experiment(document),
    )


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def validate(root: Path, changes: Mapping[str, Any]) -> tuple[Changes, list[str]]:
    """The edits a change asks for, each checked; and any doubts worth saying.

    Only the settings being changed are checked: a value typed into nrw.toml
    by hand must not stop someone saving an unrelated one. Every check is on
    the text alone -- nothing here looks at the file system, which on a dead
    data mount would hang the request.

    Args:
        root: Project root, so a data location inside the project is refused.
        changes: Setting name (one of :data:`CHANGE_KEYS`) to its new value.
            For an experiment setting, ``None`` means "back to nrw's default".

    Returns:
        The edits, by table then key, and warnings.

    Raises:
        SettingsError: A setting is unknown, or its value is not allowed.
    """
    unknown = sorted(set(changes) - set(CHANGE_KEYS))
    if unknown:
        raise SettingsError(
            f"{', '.join(unknown)}: not a setting. Known: {', '.join(CHANGE_KEYS)}."
        )
    edits: dict[str, dict[str, Set | Unset]] = {}
    warnings: list[str] = []
    source = EXPERIMENT_KEYS["experiment.source"]
    feed = EXPERIMENT_KEYS["experiment.feed"]
    for name, value in changes.items():
        if name == "ipts":
            edits.setdefault("beamtime", {})["ipts"] = Set(_ipts(value))
        elif name == "label":
            edits.setdefault("beamtime", {})["label"] = Set(
                _line(value, "label", MAX_LABEL)
            )
        elif name == "source.kind":
            edits.setdefault("experiment.source", {})["kind"] = _kind(
                value, SOURCE_OPTIONS, source["kind"], name
            )
        elif name == "source.location":
            edits.setdefault("experiment.source", {})["location"] = (
                Unset(source["location"])
                if value is None
                else Set(_location(root, value, warnings), default=source["location"])
            )
        elif name == "source.settle_seconds":
            edits.setdefault("experiment.source", {})["settle_seconds"] = (
                Unset(source["settle_seconds"])
                if value is None
                else Set(
                    _seconds(value, SETTLE_RANGE, name),
                    default=source["settle_seconds"],
                )
            )
        elif name == "feed.kind":
            edits.setdefault("experiment.feed", {})["kind"] = _kind(
                value, FEED_OPTIONS, feed["kind"], name
            )
        elif name == "feed.poll_seconds":
            edits.setdefault("experiment.feed", {})["poll_seconds"] = (
                Unset(feed["poll_seconds"])
                if value is None
                else Set(
                    _seconds(value, POLL_RANGE, name), default=feed["poll_seconds"]
                )
            )
    return edits, warnings


def _ipts(value: Any) -> str:
    if value is None or (isinstance(value, str) and not value.strip()):
        return ""
    if not isinstance(value, str):
        raise SettingsError(f"ipts must be text, e.g. IPTS-34347, not {value!r}.")
    normalized = normalize_ipts(value)
    if normalized is None:
        raise SettingsError(
            f"{value!r} is not an IPTS number; write it as IPTS-34347 or 34347."
        )
    return normalized


def _line(value: Any, name: str, limit: int) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise SettingsError(f"{name} must be text, not {value!r}.")
    text = value.strip()
    if _CONTROL.search(text):
        raise SettingsError(f"{name} must be one line of plain text.")
    try:
        text.encode("utf-8")
    except UnicodeEncodeError as exc:
        # A lone surrogate reaches here from JSON (`"\\ud800"`); nrw.toml is
        # UTF-8, so it cannot be written, and must be refused, not crash.
        raise SettingsError(f"{name} holds a character that is not text.") from exc
    if len(text) > limit:
        raise SettingsError(f"{name} is longer than {limit} characters.")
    return text


def _kind(
    value: Any, options: tuple[Option, ...], default: Value, name: str
) -> Set | Unset:
    if value is None or value == default:
        return Unset(default)
    option = next((o for o in options if o.kind == value), None)
    if option is None:
        raise SettingsError(
            f"{name} {value!r} is not known. Known: {', '.join(o.kind for o in options)}."
        )
    if not option.available:
        raise SettingsError(f"{option.label} is not available yet. {option.detail}")
    return Set(option.kind, default=default)


def _location(root: Path, value: Any, warnings: list[str]) -> str:
    import os

    text = _line(value, "source.location", MAX_LOCATION)
    if not text:
        raise SettingsError(
            "source.location is empty; give a folder, or use nrw's default location."
        )
    if text.startswith("~"):
        raise SettingsError(
            "source.location must be a full path; `~` is not expanded, and would "
            "mean someone else's home directory to anyone who opens this project."
        )
    concrete = text.replace("{ipts}", "IPTS-0")
    if not Path(concrete).is_absolute():
        raise SettingsError(
            f"source.location {text!r} is not a full path, so it would mean "
            "something different from every directory nrw is started in."
        )
    # Lexical only: resolving a path on a dead mount blocks the request.
    folder = os.path.normpath(concrete)
    project = os.path.normpath(str(Path(root).absolute()))
    for inside in ("samples", ".nrw", "experiment"):
        area = os.path.join(project, inside)
        if folder == area or folder.startswith(area + os.sep):
            raise SettingsError(
                f"source.location is inside this project's {inside}/, which nrw "
                "writes itself; point it at the facility's folder."
            )
    home = os.path.normpath(str(Path.home()))
    if folder == home or folder.startswith(home + os.sep):
        warnings.append(
            "The data location is in your home directory. nrw.toml is committed "
            "with the project, so the path will be wrong for anyone else who "
            "opens it."
        )
    return text


def _seconds(value: Any, bounds: tuple[int, int], name: str) -> int | float:
    import math

    low, high = bounds
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(value)
        or not low <= value <= high
    ):
        raise SettingsError(
            f"{name} must be a number of seconds from {low} to {high}, not {value!r}."
        )
    return int(value) if float(value).is_integer() else float(value)


def save(
    root: Path,
    changes: Mapping[str, Any],
    *,
    base_revision: str | None = None,
    confirmed: Collection[str] = (),
    catalogued_runs: int = 0,
    write: bool = True,
) -> SaveResult:
    """Change settings in ``nrw.toml``, losing nothing a person wrote.

    If the file is exactly nrw's own -- what ``nrw init`` would write, or
    untouched since it did -- the new file is ``init``'s render with the new
    values, so ``init`` finds nothing to do afterwards. Otherwise only nrw's
    own lines change (:mod:`nr_workbench.project.tomlfile`), and the edit is
    proved before it is written. Either way the previous file is kept.

    Args:
        root: Project root.
        changes: Setting name to new value; see :func:`validate`.
        base_revision: The revision the change was made against. A file
            changed since is not written over.
        confirmed: Confirmations already given, e.g. ``"ipts-change"``.
        catalogued_runs: How many runs the experiment catalog holds, which
            makes an IPTS change something to confirm.
        write: ``False`` to only say what would change.

    Raises:
        SettingsError: A value is not allowed.
        NeedsConfirmation: The change needs a person's confirmation first.
        TomlConflictError: The file changed since ``base_revision``.
        TomlEditError: The file cannot be edited safely; its ``lines`` say
            what to change by hand.
        OSError: The file cannot be read or written.
    """
    import difflib
    import tomllib

    from nr_workbench.project.layout import ProjectLayout
    from nr_workbench.project.tomlfile import (
        TomlConflictError,
        read_config,
        write_config,
    )

    layout = ProjectLayout(root=Path(root).absolute())
    base = read_config(layout.config_file)
    if base_revision is not None and base_revision != base.revision:
        raise TomlConflictError(
            "nrw.toml changed since these settings were shown -- edited by hand, "
            "or saved from somewhere else. Nothing was written; look again."
        )
    edits, warnings = validate(layout.root, changes)
    document = tomllib.loads(base.text)

    if "ipts" in changes and catalogued_runs and "ipts-change" not in confirmed:
        beamtime = document.get("beamtime")
        before = (
            normalize_ipts(beamtime.get("ipts"))
            if isinstance(beamtime, Mapping)
            else None
        )
        after = edits["beamtime"]["ipts"].value or None
        if after != before:
            raise NeedsConfirmation(
                "ipts-change",
                f"The catalog holds {catalogued_runs} run(s) from "
                f"{before or 'no IPTS'}. Changing the IPTS to {after or 'none'} "
                "points nrw at another experiment's data; the runs already "
                "catalogued stay as they are.",
            )

    new_text, upgraded = _new_text(layout, base.text, document, edits)
    if new_text == base.text:
        return SaveResult(changed=False, diff="", warnings=tuple(warnings))
    diff = "".join(
        difflib.unified_diff(
            base.text.splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            "nrw.toml",
            "nrw.toml",
        )
    )
    notes = []
    if upgraded:
        notes.append(
            "nrw.toml had not been edited since nrw wrote it, so it was also "
            "brought up to date with this version of nrw -- as `nrw init` would."
        )
    if not write:
        return SaveResult(True, diff, notes=tuple(notes), warnings=tuple(warnings))

    backup = write_config(
        layout.config_file,
        base,
        new_text,
        cache_dir=layout.cache_dir,
        backups_dir=layout.backups_dir,
    )
    written, more = _keep_in_step(layout, beamtime_changed="beamtime" in edits)
    return SaveResult(
        changed=True,
        diff=diff,
        written=("nrw.toml", *written),
        backup=backup.relative_to(layout.root).as_posix(),
        notes=tuple(notes + more),
        warnings=tuple(warnings),
    )


def _new_text(
    layout: Any, text: str, document: Mapping[str, Any], edits: Changes
) -> tuple[str, bool]:
    """The new file, and whether it also took this version of the template."""
    from nr_workbench.project.render import init_context
    from nr_workbench.project.scaffold import Outcome, classify, load_lock
    from nr_workbench.project.tomlfile import verify

    planned = _planned(init_context(layout.root))["nrw.toml"]
    outcome = classify(
        planned, layout.config_file, load_lock(layout.scaffold_lock).get("nrw.toml")
    )
    if outcome in (Outcome.UNCHANGED, Outcome.UPGRADE):
        # Nothing in the file is a person's: write init's render, new values in.
        new = _planned(_with(init_context(layout.root), edits))["nrw.toml"]
        new_text = new.content.decode("utf-8")
        if outcome is Outcome.UNCHANGED:
            verify(text, new_text, edits)
        else:
            # An upgrade takes the rest of the template too, so the edit cannot
            # be proved against the old file; the new one must still parse.
            _must_parse(new_text)
        return new_text, outcome is Outcome.UPGRADE
    return _edited(text, document, edits), False


def _must_parse(text: str) -> None:
    import tomllib

    from nr_workbench.project.tomlfile import TomlEditError

    try:
        tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise TomlEditError(
            f"the new nrw.toml would not be valid TOML ({exc})."
        ) from exc


def _edited(text: str, document: Mapping[str, Any], edits: Changes) -> str:
    """Change only nrw's own lines of a file a person has edited."""
    from nr_workbench.project.tomlfile import edit, replace_block, verify

    rest = {
        table: keys
        for table, keys in edits.items()
        if not table.startswith("experiment.")
    }
    ours = {
        table: keys for table, keys in edits.items() if table.startswith("experiment.")
    }
    new = text
    if ours:
        current = written_experiment(document)
        wanted = _apply(current, ours)
        replaced = replace_block(
            new, experiment_block(current), experiment_block(wanted)
        )
        if replaced is not None:
            # The experiment block is still exactly nrw's: rewrite it as a unit.
            new = replaced
        elif "experiment" not in document and "[experiment." not in text:
            # A file from before the block existed: add nrw's, in full.
            newline = "\r\n" if "\r\n" in text else "\n"
            block = experiment_block(wanted).replace("\n", newline)
            new = new.rstrip("\r\n") + newline + newline + block + newline
        else:
            rest.update(ours)
    if rest:
        new = edit(new, rest)
    verify(text, new, edits)
    return new


def _apply(current: ExperimentValues, edits: Changes) -> dict[str, dict[str, Value]]:
    values = {table: dict(keys) for table, keys in current.items()}
    for table, keys in edits.items():
        chosen = values.setdefault(table, {})
        for key, change in keys.items():
            if isinstance(change, Set):
                chosen[key] = change.value
            else:
                chosen.pop(key, None)
        if not chosen:
            del values[table]
    return values


def _with(context: Any, edits: Changes) -> Any:
    """``nrw init``'s render context, with the edits made."""
    import dataclasses

    beamtime = edits.get("beamtime", {})
    ipts = beamtime["ipts"].value if "ipts" in beamtime else context.ipts
    label = beamtime["label"].value if "label" in beamtime else context.beamtime
    experiment = _apply(
        context.experiment,
        {t: k for t, k in edits.items() if t.startswith("experiment.")},
    )
    return dataclasses.replace(
        context, ipts=ipts or None, beamtime=label or None, experiment=experiment
    )


def _planned(context: Any) -> dict[str, Any]:
    from nr_workbench.project.render import render_tree

    return {
        p.relpath: p
        for p in render_tree("project", context)
        if p.relpath in ("nrw.toml", "README.md")
    }


def _keep_in_step(
    layout: Any, *, beamtime_changed: bool
) -> tuple[list[str], list[str]]:
    """Record nrw.toml as nrw's, and refresh README.md if it is untouched.

    Returns:
        Paths also written, and notes.
    """
    from nr_workbench.fsutil import atomic_write_bytes
    from nr_workbench.project.render import init_context
    from nr_workbench.project.scaffold import (
        Outcome,
        classify,
        load_lock,
        lock_problem,
        record_installed,
        writing_scaffold,
    )

    written: list[str] = []
    notes: list[str] = []
    with writing_scaffold(layout.root):
        trouble = lock_problem(layout.scaffold_lock)
        if trouble:
            notes.append(f"The scaffold lock was not updated: {trouble}")
            return written, notes
        planned = _planned(init_context(layout.root))
        # True only when the file is exactly init's render; a file with a
        # person's edits elsewhere stays theirs, as it was.
        record_installed(layout.root, planned["nrw.toml"])
        if not beamtime_changed:
            return written, notes
        readme = planned["README.md"]
        target = layout.root / readme.relpath
        outcome = classify(
            readme, target, load_lock(layout.scaffold_lock).get(readme.relpath)
        )
        if outcome is Outcome.UPGRADE:
            atomic_write_bytes(target, readme.content)
            record_installed(layout.root, readme)
            written.append(readme.relpath)
        elif outcome in (Outcome.DRIFTED, Outcome.UNTRACKED):
            notes.append(
                "README.md names the IPTS and beamtime too. It has been edited, "
                "so nrw left it alone; update it by hand."
            )
    return written, notes
