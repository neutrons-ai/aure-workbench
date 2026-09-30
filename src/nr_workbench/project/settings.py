"""The settings a person makes for a project: its IPTS, and where its data comes from.

Checking a change and saving it, for the Settings page and ``nrw experiment
settings``. What can be set lives in :mod:`nr_workbench.project.experiment_schema`
(re-exported here); how ``nrw.toml`` is written, in
:mod:`nr_workbench.project.nrwtoml`, which every writer of the file shares.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nr_workbench.project.experiment_schema import (  # noqa: F401 - re-exported
    CATALOG_KINDS,
    CHANGE_KEYS,
    CONFIRM_IPTS_CHANGE,
    EXPERIMENT_KEYS,
    FEED_KINDS,
    FEED_OPTIONS,
    MAX_LABEL,
    MAX_LOCATION,
    PLANNED_CATALOG_KINDS,
    PLANNED_FEED_KINDS,
    PLANNED_SOURCE_KINDS,
    POLL_RANGE,
    RANGES,
    SETTLE_RANGE,
    SOURCE_KINDS,
    SOURCE_OPTIONS,
    ExperimentValues,
    Option,
    experiment_block,
    normalize_ipts,
    written_experiment,
)
from nr_workbench.project.layout import ProjectLayout
from nr_workbench.project.tomlfile import Changes, Set, Unset, Value

# ---------------------------------------------------------------------------
# Changing settings
# ---------------------------------------------------------------------------

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
    layout = ProjectLayout(root=Path(root).absolute())
    for area in (layout.samples_dir, layout.state_dir, layout.experiment_dir):
        inside = os.path.normpath(str(area))
        if folder == inside or folder.startswith(inside + os.sep):
            raise SettingsError(
                f"source.location is inside this project's {area.name}/, which "
                "nrw writes itself; point it at the facility's folder."
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
    catalogued_runs: int | None = 0,
    write: bool = True,
) -> SaveResult:
    """Change settings in ``nrw.toml``, losing nothing a person wrote.

    Checks each value, asks for a confirmation the change needs, and writes
    through :func:`nr_workbench.project.nrwtoml.write_as_nrw`, which edits only
    nrw's own lines, proves the edit and keeps the previous file.

    Args:
        root: Project root.
        changes: Setting name to new value; see :func:`validate`.
        base_revision: The revision the change was made against. A file
            changed since is not written over.
        confirmed: Confirmations already given, e.g. ``"ipts-change"``.
        catalogued_runs: How many runs the experiment catalog holds, which
            makes an IPTS change something to confirm; ``None`` when the
            catalog cannot be read, which is no reason to skip asking.
        write: ``False`` to only say what would change.

    Raises:
        SettingsError: A value is not allowed.
        NeedsConfirmation: The change needs a person's confirmation first.
        TomlConflictError: The file changed since ``base_revision``.
        TomlEditError: The file cannot be edited safely; its ``lines`` say
            what to change by hand.
        OSError: The file cannot be read or written.
    """
    from nr_workbench.project.nrwtoml import write_as_nrw

    edits, warnings = validate(Path(root).absolute(), changes)

    def ask_first(document: Mapping[str, Any]) -> None:
        # Against the file as it is under the lock -- the IPTS it names now.
        if "ipts" not in changes or CONFIRM_IPTS_CHANGE in confirmed:
            return
        if catalogued_runs == 0:
            return
        beamtime = document.get("beamtime")
        before = (
            normalize_ipts(beamtime.get("ipts"))
            if isinstance(beamtime, Mapping)
            else None
        )
        after = edits["beamtime"]["ipts"].value or None
        if after == before:
            return
        held = (
            f"The catalog holds {catalogued_runs} run(s) from {before or 'no IPTS'}."
            if catalogued_runs is not None
            else "The catalog cannot be read, so nrw cannot tell whether it holds runs."
        )
        raise NeedsConfirmation(
            CONFIRM_IPTS_CHANGE,
            f"{held} Changing the IPTS to {after or 'none'} points nrw at "
            "another experiment's data; runs already catalogued stay as they are.",
        )

    result = write_as_nrw(
        root, edits, base_revision=base_revision, write=write, check=ask_first
    )
    return SaveResult(
        changed=result.changed,
        diff=result.diff,
        written=result.written,
        backup=result.backup,
        notes=result.notes,
        warnings=tuple(warnings),
    )
