"""The one way nrw writes ``nrw.toml``.

Every writer of the file -- the Settings page, ``nrw experiment settings``,
``nrw audience`` -- goes through :func:`write_as_nrw`, so they all keep the
same promises:

* **Nothing a person wrote is lost.** Only nrw's own lines change, in shapes
  :mod:`nr_workbench.project.tomlfile` recognises, and every edit is proved by
  parsing both versions before anything is written.
* **``nrw init`` agrees afterwards.** The lines nrw writes are the ones its
  template renders, so a file that was nrw's own stays nrw's own: the scaffold
  lock records what was written, and the next ``nrw init`` finds nothing to do,
  or upgrades the rest of the template around the new values.
* **No writer undoes another.** The whole read, edit, write and record runs
  under the scaffold's writer lock, which ``nrw init`` also holds from planning
  its files to writing them. Without that, a save landing between init's plan
  and its write was silently put back.
"""

from __future__ import annotations

import difflib
import tomllib
from collections.abc import Callable, Mapping
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nr_workbench.fsutil import atomic_write_bytes
from nr_workbench.project.experiment_schema import (
    ExperimentValues,
    experiment_block,
    written_experiment,
)
from nr_workbench.project.layout import ProjectLayout
from nr_workbench.project.render import init_context, render_tree
from nr_workbench.project.scaffold import (
    Outcome,
    PlannedFile,
    classify,
    load_lock,
    lock_problem,
    record_installed,
    writing_scaffold,
)
from nr_workbench.project.tomlfile import (
    Changes,
    Set,
    TomlConflictError,
    Value,
    edit,
    read_config,
    replace_block,
    verify,
    write_config,
)

#: What a change made against an out-of-date view of the file is told.
CHANGED_SINCE = (
    "nrw.toml changed since these settings were shown -- edited by hand, or "
    "saved from somewhere else. Nothing was written; look again."
)


@dataclass(frozen=True)
class Written:
    """What a write did, or would do.

    Attributes:
        changed: Whether ``nrw.toml`` differs.
        diff: The change, as a unified diff.
        written: Project-relative paths written.
        backup: Where the previous ``nrw.toml`` was kept.
        notes: What else a person should know.
    """

    changed: bool
    diff: str
    written: tuple[str, ...] = ()
    backup: str | None = None
    notes: tuple[str, ...] = ()


def write_as_nrw(
    root: Path,
    edits: Changes,
    *,
    base_revision: str | None = None,
    write: bool = True,
    check: Callable[[Mapping[str, Any]], None] | None = None,
) -> Written:
    """Make ``edits`` to ``nrw.toml`` the way nrw does.

    Args:
        root: Project root.
        edits: By table, then key.
        base_revision: The revision the change was made against; a file
            changed since is not written over.
        write: ``False`` to only say what would change.
        check: Called with the parsed file, under the lock and before any edit,
            to refuse a change that depends on what the file says now.

    Raises:
        TomlConflictError: The file changed since ``base_revision``.
        TomlEditError: The file cannot be edited safely; its ``lines`` say
            what to change by hand.
        OSError: It cannot be read or written.
    """
    layout = ProjectLayout(root=Path(root).absolute())
    # A preview writes nothing, so it needs no lock and takes none: a look at
    # a project on a read-only mount must still work.
    with writing_scaffold(layout.root) if write else nullcontext():
        base = read_config(layout.config_file)
        if base_revision is not None and base_revision != base.revision:
            raise TomlConflictError(CHANGED_SINCE)
        document = tomllib.loads(base.text)
        if check is not None:
            check(document)
        new_text = edited(base.text, document, edits)
        if new_text == base.text:
            return Written(changed=False, diff="")
        diff = "".join(
            difflib.unified_diff(
                base.text.splitlines(keepends=True),
                new_text.splitlines(keepends=True),
                "nrw.toml",
                "nrw.toml",
            )
        )
        if not write:
            return Written(changed=True, diff=diff)

        # Whose the file was, decided before it changes: only a file that was
        # nrw's own is recorded as nrw's after it.
        planned = _planned(layout.root)
        before = classify(
            planned["nrw.toml"],
            layout.config_file,
            load_lock(layout.scaffold_lock).get("nrw.toml"),
        )
        backup = write_config(
            layout.config_file,
            base,
            new_text,
            cache_dir=layout.cache_dir,
            backups_dir=layout.backups_dir,
        )
        notes: list[str] = []
        written = ["nrw.toml"]
        if before in (Outcome.UNCHANGED, Outcome.UPGRADE):
            trouble = lock_problem(layout.scaffold_lock)
            if trouble:
                notes.append(f"The scaffold lock was not updated: {trouble}")
            else:
                record_installed(
                    layout.root,
                    PlannedFile(
                        relpath="nrw.toml",
                        content=new_text.encode("utf-8"),
                        template_id=planned["nrw.toml"].template_id,
                        template_version=planned["nrw.toml"].template_version,
                    ),
                )
                if before is Outcome.UPGRADE:
                    notes.append(
                        "nrw.toml was written by an older nrw; the next `nrw init` "
                        "brings the rest of it up to date and keeps these settings."
                    )
        if "beamtime" in edits:
            written.extend(_refresh_readme(layout, notes))
    return Written(
        changed=True,
        diff=diff,
        written=tuple(written),
        backup=backup.relative_to(layout.root).as_posix(),
        notes=tuple(notes),
    )


def edited(text: str, document: Mapping[str, Any], edits: Changes) -> str:
    """The file with ``edits`` made to nrw's own lines, and proved.

    The experiment tables are rewritten as a unit while they are still exactly
    nrw's text, which keeps a file that matches ``nrw init``'s render matching
    it. Otherwise each key is edited where it stands -- a commented-out table
    nrw wrote is switched on in place, never duplicated below -- and a file
    with no experiment block at all gets nrw's.

    Raises:
        TomlEditError: The file writes a setting in a shape nrw does not edit,
            or the edit would change anything else.
    """
    rest = {table: keys for table, keys in edits.items() if not table.startswith("experiment.")}
    ours = {table: keys for table, keys in edits.items() if table.startswith("experiment.")}
    new = text
    if ours:
        current = written_experiment(document)
        wanted = apply_edits(current, ours)
        replaced = replace_block(new, experiment_block(current), experiment_block(wanted))
        if replaced is not None:
            new = replaced
        elif "experiment" not in document and "[experiment." not in text:
            newline = "\r\n" if "\r\n" in text else "\n"
            block = experiment_block(wanted).replace("\n", newline)
            new = new.rstrip("\r\n") + newline + newline + block + newline
        else:
            rest.update(ours)
    if rest:
        new = edit(new, rest)
    verify(text, new, edits)
    return new


def apply_edits(
    current: ExperimentValues, edits: Changes
) -> dict[str, dict[str, Value]]:
    """The experiment values that are set once ``edits`` are made."""
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


def _planned(root: Path) -> dict[str, PlannedFile]:
    """``nrw init``'s render of the two files a settings change touches."""
    return {
        p.relpath: p
        for p in render_tree("project", init_context(root))
        if p.relpath in ("nrw.toml", "README.md")
    }


def _refresh_readme(layout: ProjectLayout, notes: list[str]) -> list[str]:
    """README.md names the IPTS and the beamtime: refresh it if it is untouched."""
    readme = _planned(layout.root)["README.md"]
    target = layout.root / readme.relpath
    outcome = classify(readme, target, load_lock(layout.scaffold_lock).get(readme.relpath))
    if outcome is Outcome.UPGRADE and not lock_problem(layout.scaffold_lock):
        atomic_write_bytes(target, readme.content)
        record_installed(layout.root, readme)
        return [readme.relpath]
    if outcome in (Outcome.DRIFTED, Outcome.UNTRACKED):
        notes.append(
            "README.md names the IPTS and beamtime too. It has been edited, so "
            "nrw left it alone; update it by hand."
        )
    return []
