"""Apply: project the catalog into ``samples/<id>/``, where the workflow reads it.

Nothing downstream knows the catalog exists. ``nrw model new``, ``nrw data
reconcile``, ``nrw agent watch`` and the rest read ``samples/<id>/data/`` and
``sample.md`` exactly as before; apply is the one step that writes them from
what the experimenter decided. It is explicit -- planned, shown, then done --
because it writes into a directory people and agents are working in.

The rules, each of which exists because the obvious version fails quietly:

**Only complete runs are copied.** A run that has merely stopped changing may
be a third of a measurement (see :mod:`~nr_workbench.experiment.status`). An
*unconfirmed* run is copied only when a person confirms it; a quarantined,
arriving or clock-skewed one never.

**A run is copied whole or not at all.** Its files are staged under names no
reader looks for, checked, and only then renamed into ``data/steady`` in one
burst. They get a fresh modification time, not the source's: a copy dated
hours ago would look settled to ``nrw agent watch`` the moment it landed.

**Nothing is overwritten.** ``data/sources.json`` records the digest of every
file nrw copied, so a later apply can tell "the facility re-reduced it" from
"someone edited the copy" from "someone else put a different file here" -- and
reports all three rather than choosing.

**Exclusion is real.** A copied run that is now excluded, or assigned to
another sample, is *moved* to ``data/excluded/<run>/``: reversibly, and out of
every reader's way, rather than left for each one to remember a flag -- also
out of a sample folder the catalog no longer has. Files nrw did not copy are
reported and never moved.

**Nothing is written through a link.** A link among a sample's folders would
carry a copy, or a move, to wherever it points; whoever can write in the
project can plant one, so such a sample is refused.

**sample.md goes through the scaffold's three-way rule.** A hand-edited one is
kept and the catalog's version is written beside it as ``sample.md.nrw-new``.
"""

from __future__ import annotations

import dataclasses
import errno
import hashlib
import json
import os
import secrets
import shutil
import stat
import threading
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from nr_workbench.experiment.inventory import SourceFile, SourceRun
from nr_workbench.experiment.model import (
    Catalog,
    CatalogValidationError,
    RunEntry,
    RunKey,
    validate_sample_id,
)
from nr_workbench.experiment.render import (
    SampleRenderError,
    plan_sample,
    prose_for,
    sample_md_relpath,
)
from nr_workbench.experiment.status import RunStatus
from nr_workbench.fsutil import advisory_lock, atomic_write_bytes, is_bare_name
from nr_workbench.instrument.reduced import reduced_table
from nr_workbench.problems import Problem
from nr_workbench.project.layout import ProjectLayout
from nr_workbench.project.render import RenderContext
from nr_workbench.project.scaffold import (
    Outcome,
    PlannedFile,
    apply_scaffold,
    classify,
    load_lock,
    lock_problem,
)
from nr_workbench.provenance.hashing import sha256_file_or_none

#: The record of what nrw copied into a sample, beside the data.
SOURCES_FILE = "sources.json"
SOURCES_SCHEMA = "nrw-sources/1"

#: Largest copy record read. A sample's copies make a few hundred entries of a
#: few hundred bytes; this stops a file that is not nrw's record from being
#: read whole on every poll of the page.
MAX_SOURCES_BYTES = 8 * 1024 * 1024

#: How the copy record is opened: without waiting, so a FIFO by that name
#: cannot hold a poll, and as bytes on every system.
_RECORD_OPEN = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)

#: Where a sample's steady-state data is, under ``data/`` -- what every reader
#: of the sample looks at.
STEADY_DIR = "steady"

#: Where excluded and reassigned runs are moved, under ``data/``.
EXCLUDED_DIR = "excluded"

#: Largest file apply will copy. Reduced REF_L files are tens of kilobytes; this
#: only stops something that is not reduced data from being copied because it
#: happened to be named like it.
MAX_FILE_BYTES = 16 * 1024 * 1024

#: One apply at a time in this process; see :class:`ApplyBusy`.
_APPLY_LOCK = threading.Lock()


class ApplyError(Exception):
    """Apply cannot go ahead. The message says why."""


class PlanChanged(ApplyError):
    """What apply would do is no longer what was reviewed."""


class ApplyBusy(ApplyError):
    """Another apply is running."""


class ApplyRefused(ApplyError):
    """Something must be fixed by a person before anything is written."""


class Action(StrEnum):
    """What apply does, or declines to do, with one file."""

    COPY = "copy"
    RESTORE = "restore"
    UNCHANGED = "unchanged"
    RECORD = "record"
    MOVE_OUT = "move-out"
    DEFERRED = "deferred"
    SOURCE_CHANGED = "source-changed"
    LOCAL_EDITED = "local-edited"
    CONFLICT = "conflict"
    SOURCE_MISSING = "source-missing"
    NOT_MANAGED = "not-managed"


#: What the Review says of a folder the catalog no longer has, after its name.
FORMER_NOTE = (
    "is no longer in the catalog: its copies of runs the catalog places "
    "elsewhere move out, and its sample.md is left as it is. Remove the folder "
    "if the sample is gone."
)

#: Actions that change the working tree.
WRITING_ACTIONS = frozenset(
    {Action.COPY, Action.RESTORE, Action.RECORD, Action.MOVE_OUT}
)

#: Actions that mean a person should look.
ATTENTION_ACTIONS = frozenset(
    {Action.SOURCE_CHANGED, Action.LOCAL_EDITED, Action.CONFLICT, Action.SOURCE_MISSING}
)

#: The scaffold's verdicts on ``sample.md`` that mean the catalog cannot reach
#: it: edited by hand since nrw wrote it, or never written by nrw at all.
UNREACHABLE_SAMPLE_MD = frozenset({Outcome.DRIFTED, Outcome.UNTRACKED})


@dataclass(frozen=True)
class FileAction:
    """One file's part in an apply.

    Attributes:
        run: The run the file belongs to.
        name: The file's name.
        action: What happens to it.
        detail: Why, in a sentence.
        source: The source's listing of it, when there is one.
        sha256: The digest of the bytes involved, when known.
    """

    run: int
    name: str
    action: Action
    detail: str = ""
    source: SourceFile | None = None
    sha256: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return the JSON form."""
        return {
            "run": self.run,
            "name": self.name,
            "action": str(self.action),
            "detail": self.detail,
        }


@dataclass(frozen=True)
class SamplePlan:
    """What apply would do for one sample.

    Attributes:
        sample_id: The sample.
        creates: Whether ``samples/<id>/`` is created.
        files: One entry per data file concerned.
        sample_md: The scaffold's verdict on ``sample.md``.
        sample_md_detail: What that verdict means, for a person.
        scaffold: The files handed to the scaffold.
        problems: Why nothing is done for this sample, if anything.
        former: A folder the catalog no longer has (see
            :func:`_former_samples`): only its copies of runs placed elsewhere
            are moved out, and its ``sample.md`` is not looked at.
    """

    sample_id: str
    creates: bool
    files: tuple[FileAction, ...]
    sample_md: Outcome | None
    sample_md_detail: str
    scaffold: tuple[PlannedFile, ...] = ()
    problems: tuple[Problem, ...] = ()
    former: bool = False

    @property
    def writes(self) -> bool:
        """Whether applying this sample changes anything on disk."""
        return (
            self.creates
            or any(f.action in WRITING_ACTIONS for f in self.files)
            or self.sample_md in (Outcome.CREATE, Outcome.UPGRADE, Outcome.DRIFTED)
        )

    @property
    def needs_attention(self) -> bool:
        """Whether a person must look before this sample is in step.

        Includes a ``sample.md`` the catalog cannot reach. Nothing *failed*
        there, but reporting success would read as "in step" when the file
        still says what it said before.
        """
        return (
            bool(self.problems)
            or any(f.action in ATTENTION_ACTIONS for f in self.files)
            or self.sample_md in UNREACHABLE_SAMPLE_MD
        )

    def as_dict(self) -> dict[str, Any]:
        """Return the JSON form."""
        return {
            "sample": self.sample_id,
            "creates": self.creates,
            "files": [f.as_dict() for f in self.files],
            "sample_md": str(self.sample_md) if self.sample_md else None,
            "sample_md_detail": self.sample_md_detail,
            "former": self.former,
            "note": FORMER_NOTE if self.former else "",
            "writes": self.writes,
            "needs_attention": self.needs_attention,
            "problems": [p.as_dict() for p in self.problems],
        }


@dataclass(frozen=True)
class ApplyPlan:
    """What apply would do, for review.

    Attributes:
        samples: One plan per sample.
        plan_id: Digest of the plan. :func:`apply` refuses to go ahead unless
            what it would do still has this digest, so what was reviewed is
            what is written.
        problems: Why nothing can be done at all, if anything.
    """

    samples: tuple[SamplePlan, ...]
    plan_id: str
    problems: tuple[Problem, ...] = ()

    @property
    def writes(self) -> bool:
        """Whether applying changes anything on disk: never while a problem
        stops apply for every sample, so the Review offers no Apply that
        would only be refused."""
        return not self.problems and any(sample.writes for sample in self.samples)

    @property
    def needs_attention(self) -> bool:
        """Whether a person must look at anything in this plan."""
        return bool(self.problems) or any(s.needs_attention for s in self.samples)

    def as_dict(self) -> dict[str, Any]:
        """Return the JSON form."""
        return {
            "plan_id": self.plan_id,
            "writes": self.writes,
            "needs_attention": self.needs_attention,
            "samples": [s.as_dict() for s in self.samples],
            "problems": [p.as_dict() for p in self.problems],
        }


@dataclass
class ApplyReport:
    """What apply did.

    Attributes:
        done: Actions carried out.
        failed: Actions that were attempted and did not happen, with why.
        sample_md: The scaffold's outcome per sample.
    """

    done: list[FileAction] = field(default_factory=list)
    failed: list[FileAction] = field(default_factory=list)
    sample_md: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """Return the JSON form."""
        return {
            "done": [f.as_dict() for f in self.done],
            "failed": [f.as_dict() for f in self.failed],
            "sample_md": dict(self.sample_md),
        }


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------


def plan_apply(
    root: Path,
    catalog: Catalog,
    runs: Mapping[RunKey, SourceRun],
    statuses: Mapping[RunKey, RunStatus],
    source: Any,
    context: RenderContext,
    *,
    samples: Collection[str] | None = None,
    confirmed: Collection[RunKey] = (),
) -> ApplyPlan:
    """Work out what applying the catalog would do. Writes nothing.

    Args:
        root: Project root.
        catalog: The catalog.
        runs: What the source lists, by key.
        statuses: Each listed run's arrival state.
        source: The data source, read only to compare a file whose version
            changed since it was copied.
        context: The project's render context.
        samples: Restrict to these samples. If omitted: every sample the
            catalog has, and every folder it no longer has that still holds
            copies of runs it places elsewhere.
        confirmed: Unconfirmed runs a person has confirmed may be copied.

    Returns:
        The plan.
    """
    root = Path(root)
    problems = _plan_problems(root)
    lock = load_lock(ProjectLayout(root=root).scaffold_lock) if not problems else {}
    existing = _existing_sample_dirs(root)
    former = _former_samples(root, catalog, existing)
    chosen = (
        sorted(samples)
        if samples is not None
        else sorted({*catalog.sample_ids(), *former})
    )
    plans = []
    for sample_id in chosen:
        if sample_id in former:
            plans.append(former[sample_id])
            continue
        if not catalog.manages(sample_id):
            problems.append(
                Problem(
                    f"sample:{sample_id}",
                    f"{sample_id} is not in the catalog; there is nothing to apply.",
                )
            )
            continue
        try:
            plans.append(
                _plan_sample(
                    root,
                    catalog,
                    sample_id,
                    runs,
                    statuses,
                    source,
                    context,
                    lock,
                    existing,
                    set(confirmed),
                )
            )
        except ApplyRefused as exc:
            plans.append(_refused(sample_id, Problem(f"sample:{sample_id}", str(exc))))
        except OSError as exc:
            plans.append(_refused(sample_id, _cannot_look(sample_id, exc)))
    return ApplyPlan(
        samples=tuple(plans),
        plan_id=_plan_id(plans, catalog, source),
        problems=tuple(problems),
    )


@dataclass(frozen=True)
class NewData:
    """What applying would bring in, by apply's own rules, read off a listing.

    Attributes:
        ready: Per sample, the runs apply would copy now: assigned, included,
            complete, and with a file the sample does not have yet. An
            unconfirmed run waits for a person in the Review, so it is not here.
        reduced_again: Per sample, the runs with a copy whose file the source
            now lists in another version. Only the Review, which reads both,
            can say whether the bytes changed.
        unassigned: Complete runs in no sample that nobody excluded. Nothing
            copies them until someone assigns them.
        problems: What stops apply, for one sample or for all of them; the
            sign counts nothing there, as apply would copy nothing.
    """

    ready: Mapping[str, tuple[int, ...]] = field(default_factory=dict)
    reduced_again: Mapping[str, tuple[int, ...]] = field(default_factory=dict)
    unassigned: tuple[int, ...] = ()
    problems: tuple[Problem, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        """Return the JSON form: run numbers, over all samples and per sample."""
        return {
            "ready": sorted(run for runs in self.ready.values() for run in runs),
            "reduced_again": sorted(
                run for runs in self.reduced_again.values() for run in runs
            ),
            "unassigned": sorted(self.unassigned),
            "samples": {
                sample_id: {
                    "ready": list(self.ready.get(sample_id, ())),
                    "reduced_again": list(self.reduced_again.get(sample_id, ())),
                }
                for sample_id in sorted({*self.ready, *self.reduced_again})
            },
        }


def new_data(
    root: Path,
    catalog: Catalog,
    runs: Mapping[RunKey, SourceRun],
    statuses: Mapping[RunKey, RunStatus],
) -> NewData:
    """What applying would bring in now, without reading a single data file.

    The page asks on every poll, so this takes the steps :func:`plan_apply`
    takes and stops where planning would read: it stops at the same
    refusals, then compares the source's listing with each sample's
    ``data/sources.json`` and looks at the files the sample uses, by name and
    type, nothing more -- never through a link, which ``nrw import`` points
    into the facility's tree. What it counts, apply copies; what only a read
    can tell, the Review tells: a copy edited by hand, and whether a file
    listed in a new version really changed.

    Args:
        root: Project root.
        catalog: The catalog.
        runs: What the source lists, by key.
        statuses: Each listed run's arrival state.

    Returns:
        The runs to copy and those reduced again, per sample; the runs in no
        sample; and what stops apply, where it stops it.
    """
    root = Path(root)
    # Assigning is not applying: what stops apply leaves these waiting.
    unassigned = tuple(
        sorted(
            {
                key.run
                for key, status in statuses.items()
                if status.complete and _in_no_sample(catalog.runs.get(key))
            }
        )
    )
    # What stops apply stops the rest of the sign: copies promised that apply
    # will not make would stay on the page after every apply.
    blocked = _plan_problems(root)
    if blocked:
        return NewData(unassigned=unassigned, problems=tuple(blocked))
    existing = _existing_sample_dirs(root)
    ready: dict[str, tuple[int, ...]] = {}
    again: dict[str, tuple[int, ...]] = {}
    problems: list[Problem] = []
    for sample_id in catalog.sample_ids():
        sample_dir = root / "samples" / sample_id
        try:
            refusal = _sample_refusal(root, catalog, sample_id, existing)
            if refusal is not None:
                problems.append(refusal)
                continue
            steps = _copy_steps(
                _wanted(catalog, sample_id),
                runs,
                statuses,
                sample_dir / "data" / STEADY_DIR,
                read_sources(sample_dir),
                set(),  # nobody confirms a run outside a Review
            )
        except ApplyRefused as exc:
            problems.append(Problem(f"sample:{sample_id}", str(exc)))
            continue
        except OSError as exc:
            problems.append(_cannot_look(sample_id, exc))
            continue
        copies = {
            s.run
            for s in steps
            if isinstance(s, FileAction) and s.action is Action.COPY
        }
        moved = {s.run for s in steps if isinstance(s, _UnreadCopy) and s.version_moved}
        if copies:
            ready[sample_id] = tuple(sorted(copies))
        if moved:
            again[sample_id] = tuple(sorted(moved))
    return NewData(ready, again, unassigned, tuple(problems))


def _in_no_sample(entry: RunEntry | None) -> bool:
    """Whether a run waits for someone to put it in a sample.

    An excluded run is one somebody decided about -- a direct beam, an
    alignment scan -- so it is no longer waiting.
    """
    return entry is None or (entry.sample_id is None and entry.include)


def _plan_problems(root: Path) -> list[Problem]:
    """What stops apply for every sample at once: a scaffold lock it must not
    write over. Planning and the sign of new data both ask here."""
    trouble = lock_problem(ProjectLayout(root=root).scaffold_lock)
    return [Problem("scaffold", trouble)] if trouble else []


def _sample_refusal(
    root: Path, catalog: Catalog, sample_id: str, existing: Mapping[str, str]
) -> Problem | None:
    """Why apply would do nothing at all for this sample, or ``None``.

    Planning and the sign of new data both stop here, so neither counts on a
    sample the other refuses. A new refusal belongs here: anywhere else, the
    sign would go on promising copies apply never makes. The one other is a
    copy record that cannot be read (:func:`read_sources`), which both catch.
    Folders the catalog no longer has are planning's alone: the sign counts
    new data for the catalog's own samples.
    """
    try:
        # nrw's own edits hold every sample id to this, so one that fails it
        # was written some other way -- and could name a path outside samples/.
        validate_sample_id(sample_id)
    except CatalogValidationError as exc:
        return Problem(
            f"sample:{sample_id}",
            f"{exc} The catalog names it, written some other way than nrw; "
            "nothing is applied to it until that is corrected.",
        )
    clash = existing.get(sample_id.casefold())
    if clash is not None and clash != sample_id:
        return Problem(
            f"sample:{sample_id}",
            f"samples/{clash}/ already exists and differs from {sample_id} only "
            "in case; on macOS and Windows they are one directory. Rename the "
            "sample in the catalog to match.",
        )
    in_the_way = _link_in_the_way(root, root / "samples" / sample_id)
    if in_the_way is not None:
        return Problem(
            f"sample:{sample_id}",
            f"{in_the_way} is a link, or not a folder; apply writes nowhere "
            f"through one, so nothing is applied to {sample_id} until it is a "
            "folder again.",
        )
    try:
        # Everything that stops sample.md being written from the catalog is
        # refused while its text is put together, from the catalog alone.
        prose_for(catalog, sample_id)
    except SampleRenderError as exc:
        return Problem(f"sample:{sample_id}", str(exc))
    return None


def _refused(sample_id: str, problem: Problem) -> SamplePlan:
    """The plan for a sample apply does nothing for, and the reason."""
    return SamplePlan(
        sample_id,
        creates=False,
        files=(),
        sample_md=None,
        sample_md_detail="",
        problems=(problem,),
    )


def _cannot_look(sample_id: str, exc: OSError) -> Problem:
    """A sample's folder that cannot be looked at, said without its full path."""
    return Problem(
        f"sample:{sample_id}",
        f"samples/{sample_id}/ cannot be looked at "
        f"({exc.strerror or type(exc).__name__}); nothing is applied to it "
        "until it can be.",
    )


def _former_samples(
    root: Path, catalog: Catalog, existing: Mapping[str, str]
) -> dict[str, SamplePlan]:
    """Plans for folders the catalog no longer has, still holding copies of its runs.

    A sample is the catalog's while a run or a record names it. One made by
    assigning runs to a new id has no record, so moving its last run away
    drops it -- with its copies still in ``data/steady/``, where every reader
    looks, and the run then in two samples. Apply moves those copies out, as
    for any sample: each copy the folder's record lists, still there as a
    file, of a run the catalog has a record of, and so places elsewhere or in
    no sample. A catalog that knows none of a folder's runs moves nothing:
    one emptied or lost, or a folder copied in from another experiment.
    """
    managed = {sample_id.casefold() for sample_id in catalog.sample_ids()}
    plans: dict[str, SamplePlan] = {}
    for folded, name in sorted(existing.items()):
        if folded in managed:
            continue
        sample_dir = root / "samples" / name
        try:
            validate_sample_id(name)
            if (
                _link_in_the_way(root, sample_dir) is not None
                or _lstat(sample_dir / "data" / SOURCES_FILE) is None
            ):
                continue
            record = read_sources(sample_dir)
            steady = sample_dir / "data" / STEADY_DIR
            stale = {
                file: entry
                for file, entry in record.items()
                if entry.in_steady
                and RunKey(entry.run) in catalog.runs
                and _is_file(steady / file)
            }
        except ApplyRefused as exc:
            # nrw's record, and unreadable: what it holds cannot be told.
            plans[name] = dataclasses.replace(
                _refused(
                    name,
                    Problem(
                        f"sample:{name}", f"{name} is not in the catalog, and {exc}"
                    ),
                ),
                former=True,
            )
            continue
        except (CatalogValidationError, OSError):
            # Not a name nrw gives a sample, or a folder this account may not
            # look into: nothing in it is nrw's to move.
            continue
        if stale:
            plans[name] = SamplePlan(
                name,
                creates=False,
                files=tuple(
                    _move_out_steps(
                        catalog, name, set(), stale, sample_dir / "data" / EXCLUDED_DIR
                    )
                ),
                sample_md=None,
                sample_md_detail="",
                former=True,
            )
    return plans


def _existing_sample_dirs(root: Path) -> dict[str, str]:
    """Casefolded name to the real name of every directory under samples/."""
    base = root / "samples"
    if not base.is_dir():
        return {}
    return {p.name.casefold(): p.name for p in base.iterdir() if p.is_dir()}


def _plan_sample(
    root: Path,
    catalog: Catalog,
    sample_id: str,
    runs: Mapping[RunKey, SourceRun],
    statuses: Mapping[RunKey, RunStatus],
    source: Any,
    context: RenderContext,
    lock: Mapping[str, Any],
    existing: Mapping[str, str],
    confirmed: set[RunKey],
) -> SamplePlan:
    refusal = _sample_refusal(root, catalog, sample_id, existing)
    if refusal is not None:
        return _refused(sample_id, refusal)

    sample_dir = root / "samples" / sample_id
    creates = _lstat(sample_dir / "sample.md") is None
    steady = sample_dir / "data" / STEADY_DIR
    record = read_sources(sample_dir)
    wanted = _wanted(catalog, sample_id)
    steps = [
        *_copy_steps(wanted, runs, statuses, steady, record, confirmed),
        *_move_out_steps(
            catalog, sample_id, wanted, record, sample_dir / "data" / EXCLUDED_DIR
        ),
        *_not_managed_steps(steady, record, wanted),
    ]
    actions = [
        step if isinstance(step, FileAction) else step.decide(source) for step in steps
    ]

    try:
        planned = plan_sample(root, context, sample_id, catalog=catalog)
    except SampleRenderError as exc:
        # What the catalog's text refuses was refused above, where the sign
        # refuses it too; this is for a refusal of plan_sample's own.
        return _refused(sample_id, Problem(f"sample:{sample_id}", str(exc)))
    relpath = sample_md_relpath(sample_id)
    scaffold = tuple(
        planned if creates else [p for p in planned if p.relpath == relpath]
    )
    md = next(p for p in planned if p.relpath == relpath)
    outcome = classify(md, root / relpath, lock.get(relpath))
    return SamplePlan(
        sample_id,
        creates=creates,
        files=tuple(actions),
        sample_md=outcome,
        sample_md_detail=_describe_md(outcome),
        scaffold=scaffold,
    )


@dataclass(frozen=True)
class _UnreadCopy:
    """A copy nrw made, which only reading it, and the source, says more about.

    Planning reads them (:meth:`decide`); the sign of new data stops here, so
    a poll reads neither the copy nor the source.

    Attributes:
        run: The run the file belongs to.
        name: The file's name.
        target: The copy, in the sample.
        listed: The source's listing of the file, when it lists it.
        entry: nrw's record of copying it.
    """

    run: int
    name: str
    target: Path
    listed: SourceFile | None
    entry: CopyEntry

    @property
    def version_moved(self) -> bool:
        """Whether the source lists another version than the one nrw copied.

        Probably reduced again; only the bytes can tell that from a touch.
        """
        return (
            self.listed is not None and self.listed.version != self.entry.source_version
        )

    def decide(self, source: Any) -> FileAction:
        """Read the copy, and the source when its version moved, and decide."""
        return _check_copy(self, source)


@dataclass(frozen=True)
class _UnreadForeign:
    """A file with the source's name that nrw did not copy.

    Only comparing its bytes with the source's says whether it is the same.

    Attributes:
        run: The run the file belongs to.
        name: The file's name.
        target: The file, in the sample.
        listed: The source's listing of it.
    """

    run: int
    name: str
    target: Path
    listed: SourceFile

    def decide(self, source: Any) -> FileAction:
        """Read both, and decide."""
        return _hand_copied(self.run, self.name, self.target, self.listed, source)


#: One step of a plan before anything is read: decided, or still to read.
_Step = FileAction | _UnreadCopy | _UnreadForeign


def _wanted(catalog: Catalog, sample_id: str) -> set[RunKey]:
    """The runs a sample uses: assigned to it, and included."""
    return {entry.key for entry in catalog.runs_for(sample_id) if entry.include}


def _copy_steps(
    wanted: set[RunKey],
    runs: Mapping[RunKey, SourceRun],
    statuses: Mapping[RunKey, RunStatus],
    steady: Path,
    record: Mapping[str, CopyEntry],
    confirmed: set[RunKey],
) -> list[_Step]:
    """What happens to the files of the runs a sample uses, short of reading.

    Every copy apply makes, and every copy whose source moved, is one of
    these; the sign of new data takes these steps and no others.
    """
    steps: list[_Step] = []
    for key in sorted(wanted):
        steps.extend(
            _run_steps(key, runs.get(key), statuses.get(key), steady, record, confirmed)
        )
    return steps


def _move_out_steps(
    catalog: Catalog,
    sample_id: str,
    wanted: set[RunKey],
    record: Mapping[str, CopyEntry],
    excluded: Path,
) -> list[FileAction]:
    """nrw's own copies of runs that no longer belong here: moved aside."""
    steps: list[FileAction] = []
    for name, entry in sorted(record.items()):
        key = RunKey(entry.run)
        if key in wanted or not entry.in_steady:
            continue
        if _not_a_folder(excluded / str(key.run)):
            steps.append(
                FileAction(
                    key.run,
                    name,
                    Action.CONFLICT,
                    f"data/{EXCLUDED_DIR}/{key.run} is a link, or not a folder; "
                    "nothing is moved through it",
                )
            )
            continue
        other = catalog.runs.get(key)
        if other is None or other.sample_id is None:
            why = "no longer assigned to this sample"
        elif other.sample_id != sample_id:
            why = f"assigned to {other.sample_id} now"
        else:
            why = "excluded"
        steps.append(
            FileAction(
                key.run,
                name,
                Action.MOVE_OUT,
                f"run {key.run} is {why}; its copy moves to data/{EXCLUDED_DIR}/{key.run}/",
                sha256=entry.sha256,
            )
        )
    return steps


def _not_managed_steps(
    steady: Path, record: Mapping[str, CopyEntry], wanted: set[RunKey]
) -> list[FileAction]:
    """Files nrw did not copy, for runs this sample does not use: said, and left.

    A link is said too, and never followed: `nrw import` points them into the
    facility's tree, on a mount that may not answer.
    """
    from nr_workbench.instrument.reduced import (
        parse_combined_name,
        parse_segment_name,
    )

    try:
        paths = sorted(steady.iterdir())
    except (FileNotFoundError, NotADirectoryError):
        return []
    steps: list[FileAction] = []
    for path in paths:
        info = _lstat(path)
        if (
            path.name in record
            or info is None
            or not (stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode))
        ):
            continue
        segment = parse_segment_name(path.name)
        run = segment.run if segment else parse_combined_name(path.name)
        # A name for run 0 is no run, and still not nrw's to move.
        if run is None or (run > 0 and RunKey(run) in wanted):
            continue
        steps.append(
            FileAction(
                run,
                path.name,
                Action.NOT_MANAGED,
                "not copied by nrw, and its run is not assigned here; left alone",
            )
        )
    return steps


def _run_steps(
    key: RunKey,
    listed: SourceRun | None,
    status: RunStatus | None,
    steady: Path,
    record: Mapping[str, CopyEntry],
    confirmed: set[RunKey],
) -> list[_Step]:
    """What happens to one assigned, included run's files, short of reading them."""
    run = key.run
    recorded = {n: e for n, e in record.items() if e.run == run}
    steps: list[_Step] = []

    if listed is None:
        for name, entry in sorted(recorded.items()):
            steps.append(_already_copied(run, name, entry, None, steady))
        if not recorded:
            steps.append(
                FileAction(
                    run,
                    f"run {run}",
                    Action.SOURCE_MISSING,
                    "assigned here, but the data source does not list it -- not "
                    "reduced yet, or the source cannot be reached",
                )
            )
        return steps

    listed_by_name = {f.name: f for f in listed.files}
    fitting = {f.name for f in listed.fitting_files}
    for name, entry in sorted(recorded.items()):
        if name not in fitting:
            # No longer listed; or an artifact nrw copied before only the
            # files a fit reads were copied -- still nrw's, still checked.
            steps.append(
                _already_copied(run, name, entry, listed_by_name.get(name), steady)
            )

    may_copy, why_not = _may_copy(key, status, confirmed)
    # What a fit of the sample reads, and nothing else: the segments, or the
    # combined curve when there are none. A combined curve beside segments
    # stays at the source; one nrw copied before this rule is still managed.
    for source_file in listed.fitting_files:
        name = source_file.name
        entry = recorded.get(name)
        if entry is not None:
            steps.append(_already_copied(run, name, entry, source_file, steady))
            continue
        target = steady / name
        info = _lstat(target)
        if info is not None and stat.S_ISLNK(info.st_mode):
            steps.append(
                FileAction(
                    run,
                    name,
                    Action.CONFLICT,
                    "a symbolic link with this name is already here (from `nrw "
                    "import`?); left alone",
                )
            )
            continue
        if info is not None:
            steps.append(_UnreadForeign(run, name, target, source_file))
            continue
        if not may_copy:
            steps.append(FileAction(run, name, Action.DEFERRED, why_not, source_file))
            continue
        steps.append(FileAction(run, name, Action.COPY, "", source_file))
    return steps


def _link_in_the_way(root: Path, sample_dir: Path) -> str | None:
    """The first of a sample's folders apply writes into that is a link, or
    not a folder, as a path from the project root; ``None`` if there is none.

    Folders not made yet are none: apply makes them, as folders. Each is
    looked at by its own status, the outermost first, so none is looked
    through.
    """
    data = sample_dir / "data"
    for path in (sample_dir, data, data / STEADY_DIR, data / EXCLUDED_DIR):
        if _not_a_folder(path):
            return path.relative_to(root).as_posix()
    return None


def _not_a_folder(path: Path) -> bool:
    """Whether something is at *path* that is not a folder: a link, or a file."""
    info = _lstat(path)
    return info is not None and not stat.S_ISDIR(info.st_mode)


def _is_file(path: Path) -> bool:
    """Whether *path* is a plain file, itself and not by a link."""
    info = _lstat(path)
    return info is not None and stat.S_ISREG(info.st_mode)


def _lstat(path: Path) -> os.stat_result | None:
    """A file's own status, not following a link; ``None`` if there is none.

    Not ``Path.exists()`` or ``is_file()``: on Python 3.14 they answer False
    for a file in a folder this account may not look into, where 3.11 to 3.13
    raise -- and a copy that cannot be seen is not a copy that is not there.

    Raises:
        OSError: The file may not be looked at, or the look failed.
    """
    try:
        return os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        return None


def _may_copy(
    key: RunKey, status: RunStatus | None, confirmed: set[RunKey]
) -> tuple[bool, str]:
    if status is None:
        return (False, "its state is not known yet")
    if status.complete:
        return (True, "")
    if status.state == "unconfirmed" and key in confirmed:
        return (True, "")
    if status.state == "unconfirmed":
        return (False, f"{status.reason}")
    return (False, f"{status.state}: {status.reason}")


def _already_copied(
    run: int,
    name: str,
    entry: CopyEntry,
    listed: SourceFile | None,
    steady: Path,
) -> _Step:
    """A file nrw copied earlier, as far as can be told without reading it."""
    if entry.location == EXCLUDED_DIR:
        if _not_a_folder(steady.parent / EXCLUDED_DIR / str(run)):
            return FileAction(
                run,
                name,
                Action.CONFLICT,
                f"data/{EXCLUDED_DIR}/{run} is a link, or not a folder; nothing "
                "is moved back through it",
            )
        return FileAction(
            run,
            name,
            Action.RESTORE,
            f"run {run} is included again; its copy moves back from data/{EXCLUDED_DIR}/",
            listed,
            entry.sha256,
        )
    target = steady / name
    # Its own status, not where a link points: `nrw import` points them at a
    # mount that may not answer.
    if not _is_file(target):
        return FileAction(
            run,
            name,
            Action.CONFLICT,
            "nrw copied this file, but it is no longer here as a plain file; "
            "remove its entry from data/sources.json to copy it again",
        )
    return _UnreadCopy(run, name, target, listed, entry)


def _check_copy(step: _UnreadCopy, source: Any) -> FileAction:
    """A copy nrw made: is it still what nrw copied, and is the source?"""
    run, name, listed = step.run, step.name, step.listed
    local = sha256_file_or_none(step.target)
    if local != step.entry.sha256:
        # The sign of new data, which reads nothing, counted a moved version
        # as reduced again: say so here too, or the Review never mentions it.
        also = (
            ", and the facility's file was rewritten since -- probably re-reduced"
            if step.version_moved
            else ""
        )
        return FileAction(
            run,
            name,
            Action.LOCAL_EDITED,
            f"the copy has been edited since nrw copied it{also}; left alone",
        )
    if not step.version_moved:
        return FileAction(run, name, Action.UNCHANGED, "", listed, local)
    # The listing says the source file changed. Only its bytes can say whether
    # it really did: a new inode or ctime alone (another client, a restore) is
    # not a re-reduction.
    try:
        fresh = hashlib.sha256(
            source.read_bytes(listed, max_bytes=MAX_FILE_BYTES)
        ).hexdigest()
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        return FileAction(
            run,
            name,
            Action.SOURCE_CHANGED,
            f"the source changed and cannot be read ({exc})",
        )
    if fresh == local:
        return FileAction(
            run, name, Action.RECORD, "same bytes, new source version", listed, local
        )
    return FileAction(
        run,
        name,
        Action.SOURCE_CHANGED,
        "the facility's file was rewritten after it was copied -- probably "
        "re-reduced. The copy is kept; to take the new version, move the copy "
        "away and apply again.",
        listed,
    )


def _hand_copied(
    run: int, name: str, target: Path, listed: SourceFile, source: Any
) -> FileAction:
    """A file with the source's name that nrw did not copy."""
    try:
        fresh = hashlib.sha256(
            source.read_bytes(listed, max_bytes=MAX_FILE_BYTES)
        ).hexdigest()
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        return FileAction(
            run,
            name,
            Action.CONFLICT,
            f"already here, and the source cannot be read ({exc})",
        )
    local = sha256_file_or_none(target)
    if local == fresh:
        return FileAction(
            run,
            name,
            Action.RECORD,
            "already here with the same bytes; recorded",
            listed,
            local,
        )
    return FileAction(
        run,
        name,
        Action.CONFLICT,
        "a different file with this name is already here, not copied by nrw; left alone",
    )


def _describe_md(outcome: Outcome) -> str:
    return {
        Outcome.CREATE: "will be created from the catalog",
        Outcome.UNCHANGED: "already matches the catalog",
        Outcome.UPGRADE: "will be updated from the catalog",
        Outcome.DRIFTED: (
            "was edited by hand, so it is kept; the catalog's version will be "
            "written beside it as sample.md.nrw-new"
        ),
        Outcome.UNTRACKED: (
            "exists but was not written by nrw (imported, or copied in), so it "
            "is left alone and the catalog's context does not reach it. Adopt "
            "it to let the catalog manage it."
        ),
    }.get(outcome, str(outcome))


def _plan_id(plans: Iterable[SamplePlan], catalog: Catalog, source: Any) -> str:
    digest = hashlib.sha256()
    # Which data source, too: a plan reviewed against one folder must never be
    # carried out against another after the settings change in between.
    identity = source.describe()
    digest.update(json.dumps(identity, sort_keys=True, default=str).encode())
    for plan in plans:
        digest.update(
            json.dumps(
                {
                    "sample": plan.sample_id,
                    "creates": plan.creates,
                    "md": str(plan.sample_md),
                    # sample.md's bytes -- what the person reviewed -- but only
                    # the *names* of the other scaffold files: a new sample's
                    # sample.yaml is stamped with the current second, so
                    # hashing its bytes made a review and an apply that
                    # straddled a second boundary look like two plans.
                    "md_content": [
                        hashlib.sha256(p.content).hexdigest()
                        for p in plan.scaffold
                        if p.relpath.endswith("/sample.md")
                    ],
                    "scaffold": sorted(p.relpath for p in plan.scaffold),
                    "files": [
                        [
                            f.run,
                            f.name,
                            str(f.action),
                            f.source.version if f.source else None,
                        ]
                        for f in plan.files
                    ],
                    "problems": [p.message for p in plan.problems],
                },
                sort_keys=True,
            ).encode()
        )
    digest.update(
        json.dumps(
            sorted([k.slug(), e.rev] for k, e in catalog.runs.items())
            + sorted([k, e.rev] for k, e in catalog.samples.items())
        ).encode()
    )
    return digest.hexdigest()[:16]


# ---------------------------------------------------------------------------
# Applying
# ---------------------------------------------------------------------------


def apply(
    root: Path,
    catalog: Catalog,
    runs: Mapping[RunKey, SourceRun],
    statuses: Mapping[RunKey, RunStatus],
    source: Any,
    context: RenderContext,
    *,
    expected_plan_id: str,
    samples: Collection[str] | None = None,
    confirmed: Collection[RunKey] = (),
) -> ApplyReport:
    """Carry out a reviewed plan.

    The plan is worked out again first, from what is on disk now, and applied
    only if its digest matches the one reviewed.

    Raises:
        ApplyBusy: Another apply is running in this process.
        ApplyRefused: The plan has a problem that stops everything.
        PlanChanged: The plan is not the one that was reviewed.
    """
    if not _APPLY_LOCK.acquire(blocking=False):
        raise ApplyBusy(
            "Another apply is already running. Wait for it, then review again."
        )
    try:
        with advisory_lock(
            ProjectLayout(root=Path(root)).experiment_cache_dir / "apply"
        ):
            plan = plan_apply(
                root,
                catalog,
                runs,
                statuses,
                source,
                context,
                samples=samples,
                confirmed=confirmed,
            )
            if plan.problems:
                raise ApplyRefused("; ".join(p.message for p in plan.problems))
            if plan.plan_id != expected_plan_id:
                raise PlanChanged(
                    "What apply would do has changed since it was reviewed -- a "
                    "file arrived, or the catalog was edited. Review it again."
                )
            report = ApplyReport()
            for sample in plan.samples:
                if sample.problems:
                    continue
                _apply_sample(Path(root), sample, source, report)
            return report
    finally:
        _APPLY_LOCK.release()


def _apply_sample(
    root: Path, plan: SamplePlan, source: Any, report: ApplyReport
) -> None:
    sample_dir = root / "samples" / plan.sample_id
    # Planned without a link among its folders, and so looked at again now:
    # one planted since would carry every write below to where it points.
    in_the_way = _link_in_the_way(root, sample_dir)
    if in_the_way is not None:
        report.failed.extend(
            dataclasses.replace(
                action, detail=f"not done: {in_the_way} is a link, or not a folder"
            )
            for action in plan.files
            if action.action in WRITING_ACTIONS
        )
        return
    if plan.creates:
        apply_scaffold(root, list(plan.scaffold))
        report.sample_md[plan.sample_id] = str(plan.sample_md)

    steady = sample_dir / "data" / STEADY_DIR
    steady.mkdir(parents=True, exist_ok=True)
    record = read_sources(sample_dir)
    try:
        _copy_runs(steady, plan, source, record, report)
        _move(sample_dir, steady, plan, record, report)
        _record_matches(plan, record, report)
    finally:
        # Whatever happened, the record must describe what is on disk: a copy
        # nrw made but did not record would read as someone else's file.
        write_sources(sample_dir, record)

    if not plan.creates and plan.scaffold:
        md = apply_scaffold(root, list(plan.scaffold))
        report.sample_md[plan.sample_id] = str(md.files[0].outcome) if md.files else ""


def _record_matches(
    plan: SamplePlan, record: dict[str, CopyEntry], report: ApplyReport
) -> None:
    """Record files found to hold exactly the source's bytes."""
    for action in plan.files:
        if (
            action.action is Action.RECORD
            and action.source is not None
            and action.sha256 is not None
        ):
            fresh = CopyEntry.copied(action.run, action.sha256, action.source)
            earlier = record.get(action.name)
            if earlier is not None:
                # Same bytes under a new source version: nothing was copied,
                # so when it *was* copied stays as it was.
                fresh = dataclasses.replace(
                    fresh,
                    copied_at=earlier.copied_at or fresh.copied_at,
                    extra=earlier.extra,
                )
            record[action.name] = fresh
            report.done.append(action)


def _copy_runs(
    steady: Path,
    plan: SamplePlan,
    source: Any,
    record: dict[str, CopyEntry],
    report: ApplyReport,
) -> None:
    """Stage each run's files, check them, then rename them in together."""
    by_run: dict[int, list[FileAction]] = {}
    for action in plan.files:
        if action.action is Action.COPY:
            by_run.setdefault(action.run, []).append(action)

    staging = steady.parent / f".nrw-staging-{secrets.token_hex(4)}"
    try:
        for run, actions in sorted(by_run.items()):
            staged: list[tuple[FileAction, Path, str]] = []
            failure: str | None = None
            for action in actions:
                assert action.source is not None
                try:
                    data = source.read_bytes(action.source, max_bytes=MAX_FILE_BYTES)
                    reduced_table(data)
                except Exception as exc:  # noqa: BLE001 - the run is skipped, and says why
                    failure = f"{action.name}: {exc}"
                    break
                staging.mkdir(parents=True, exist_ok=True)
                part = staging / f"{action.name}.part"
                with part.open("wb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                staged.append((action, part, hashlib.sha256(data).hexdigest()))

            if failure is not None:
                for _, part, _ in staged:
                    part.unlink(missing_ok=True)
                for action in actions:
                    report.failed.append(
                        FileAction(
                            run,
                            action.name,
                            action.action,
                            f"run {run} was not copied, none of its files: {failure}",
                        )
                    )
                continue

            placed: list[tuple[FileAction, str]] = []
            try:
                for action, part, digest in staged:
                    _place(part, steady / action.name)
                    placed.append((action, digest))
            except OSError as exc:
                # Whole or not at all: take back what this run already placed.
                # Each was a new file, planned because nothing was there.
                for action, _ in placed:
                    (steady / action.name).unlink(missing_ok=True)
                for action in actions:
                    report.failed.append(
                        FileAction(
                            run,
                            action.name,
                            action.action,
                            f"run {run} was not copied, none of its files: "
                            f"placing {action.name} failed ({exc})",
                        )
                    )
                continue
            for action, digest in placed:
                record[action.name] = CopyEntry.copied(run, digest, action.source)
                report.done.append(action)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _place(source: Path, target: Path) -> None:
    """Move *source* to *target*, refusing if *target* exists by then.

    ``os.replace`` would silently overwrite a file that appeared at the target
    after the plan was made -- a copy made by hand in the minutes between
    review and apply. A hard link fails instead, atomically, which is the
    never-overwrite rule enforced by the filesystem rather than by a check
    that could race.

    Raises:
        FileExistsError: *target* exists.
        OSError: Anything else that stops the move.
    """
    try:
        os.link(source, target)
    except FileExistsError:
        raise
    except OSError:
        # No hard links on this filesystem: the check-then-replace fallback,
        # with its small race, is the best that remains.
        if target.exists() or target.is_symlink():
            raise FileExistsError(f"{target.name} already exists") from None
        os.replace(source, target)
        return
    source.unlink()


def _move(
    sample_dir: Path,
    steady: Path,
    plan: SamplePlan,
    record: dict[str, CopyEntry],
    report: ApplyReport,
) -> None:
    """Move copies out of, or back into, the data readers look at."""
    for action in plan.files:
        excluded = sample_dir / "data" / EXCLUDED_DIR / str(action.run)
        if action.action is Action.MOVE_OUT:
            origin, target, location = (
                steady / action.name,
                excluded / action.name,
                EXCLUDED_DIR,
            )
        elif action.action is Action.RESTORE:
            origin, target, location = (
                excluded / action.name,
                steady / action.name,
                STEADY_DIR,
            )
        else:
            continue
        if _not_a_folder(excluded):
            report.failed.append(
                FileAction(
                    action.run,
                    action.name,
                    action.action,
                    f"not moved: data/{EXCLUDED_DIR}/{action.run} is a link, or "
                    "not a folder",
                )
            )
            continue
        if target.exists() or not origin.is_file() or origin.is_symlink():
            report.failed.append(
                FileAction(
                    action.run,
                    action.name,
                    action.action,
                    f"not moved: {target.relative_to(sample_dir)} already exists"
                    if target.exists()
                    else f"not moved: {origin.relative_to(sample_dir)} is missing",
                )
            )
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            _place(origin, target)
        except OSError as exc:
            report.failed.append(
                FileAction(action.run, action.name, action.action, f"not moved: {exc}")
            )
            continue
        record[action.name] = dataclasses.replace(
            record[action.name], location=location
        )
        report.done.append(action)


# ---------------------------------------------------------------------------
# The copy record
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CopyEntry:
    """What nrw copied into a sample, for one file.

    Attributes:
        run: The run the file belongs to.
        sha256: Digest of the bytes copied: how a later edit of the copy is
            noticed.
        size: Their size, when the source listed it.
        source_version: The source's identity of those bytes when they were
            copied: how a re-reduction at the facility is noticed.
        copied_at: When, UTC.
        location: :data:`STEADY_DIR` or :data:`EXCLUDED_DIR`, where under
            ``data/`` the copy is now.
        kind: The run's kind; ``steady`` until tNR series are copied.
        extra: Keys this version of nrw does not know, kept so that
            rewriting the record never drops what a newer one wrote.
    """

    run: int
    sha256: str
    size: int | None = None
    source_version: str | None = None
    copied_at: str = ""
    location: str = STEADY_DIR
    kind: str = "steady"
    extra: Mapping[str, Any] = field(default_factory=dict)

    @property
    def in_steady(self) -> bool:
        """Whether the copy is in ``data/steady/``, where readers look. A
        location this nrw does not know is left alone."""
        return self.location == STEADY_DIR

    @classmethod
    def copied(cls, run: int, sha256: str, source: SourceFile | None) -> CopyEntry:
        """The entry for bytes placed in ``data/steady/`` now."""
        return cls(
            run=run,
            sha256=sha256,
            size=source.size if source else None,
            source_version=source.version if source else None,
            copied_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> CopyEntry:
        """Read one entry of ``sources.json``, already checked by the caller."""
        known = {f for f in cls.__dataclass_fields__ if f != "extra"}
        return cls(
            **{k: data[k] for k in known if k in data},
            extra={k: v for k, v in data.items() if k not in known},
        )

    def as_json(self) -> dict[str, Any]:
        """The entry as ``sources.json`` holds it."""
        return {
            **self.extra,
            "run": self.run,
            "kind": self.kind,
            "sha256": self.sha256,
            "size": self.size,
            "source_version": self.source_version,
            "copied_at": self.copied_at,
            "location": self.location,
        }


def read_sources(sample_dir: Path) -> dict[str, CopyEntry]:
    """What nrw has copied into a sample, by file name. Empty if nothing yet.

    Raises:
        ApplyRefused: The record exists but cannot be read. Treating it as
            empty would make every copy look hand-made, and the next apply
            would stop managing all of them.
    """
    path = Path(sample_dir) / "data" / SOURCES_FILE
    shown = path.relative_to(Path(sample_dir).parent.parent)
    try:
        raw = _read_record(path)
    except FileNotFoundError:
        # Tried, not checked first: on Python 3.14 `exists()` answers False
        # where it may not look, and a record read as empty loses every copy.
        return {}
    except PermissionError as exc:
        # Nothing is wrong with the file that git could restore.
        raise ApplyRefused(
            f"{shown} cannot be read: this account may not read it, or its "
            "folder. Ask whoever owns it to let you, before applying."
        ) from exc
    except OSError as exc:
        raise ApplyRefused(
            f"{shown} cannot be read ({exc.strerror or type(exc).__name__}). "
            "Restore it from git before applying."
        ) from exc
    try:
        document = json.loads(raw.decode("utf-8"))
    except (ValueError, RecursionError) as exc:
        # A bad encoding, bad JSON, or an integer too long to convert, are
        # ValueErrors; nesting too deep to parse is a RecursionError.
        raise ApplyRefused(
            f"{shown} cannot be read ({exc}). Restore it from git before applying."
        ) from exc
    files = document.get("files") if isinstance(document, dict) else None
    if not isinstance(document, dict) or document.get("schema") != SOURCES_SCHEMA:
        raise ApplyRefused(f"{path.name} is not an nrw copy record.")
    if not isinstance(files, dict):
        raise ApplyRefused(f"{path.name} has no file list.")
    record: dict[str, CopyEntry] = {}
    for name, entry in files.items():
        run = entry.get("run") if isinstance(entry, dict) else None
        if (
            # Apply moves files by these names: `../../x` would move one from
            # outside the sample.
            not is_bare_name(name)
            or isinstance(run, bool)
            or not isinstance(run, int)
            or run <= 0
            or not isinstance(entry.get("sha256"), str)
        ):
            raise ApplyRefused(
                f"{path.name}: the entry for {str(name)[:80]!r} is not one nrw "
                "wrote. Restore the file from git before applying."
            )
        try:
            record[str(name)] = CopyEntry.from_json(entry)
        except TypeError as exc:
            raise ApplyRefused(
                f"{path.name}: the entry for {str(name)[:80]!r} is not one nrw "
                f"wrote ({exc}). Restore the file from git before applying."
            ) from exc
    return record


def _read_record(path: Path) -> bytes:
    """A regular file's bytes, up to :data:`MAX_SOURCES_BYTES`.

    Raises:
        FileNotFoundError: There is no such file.
        OSError: It cannot be opened or read, is not a regular file -- a FIFO
            would hold the read forever, a device never end it -- or is too
            large.
    """
    with os.fdopen(os.open(path, _RECORD_OPEN), "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise OSError(errno.EINVAL, "not a regular file")
        data = handle.read(MAX_SOURCES_BYTES + 1)
    if len(data) > MAX_SOURCES_BYTES:
        raise OSError(errno.EFBIG, f"larger than {MAX_SOURCES_BYTES} bytes")
    return data


def write_sources(sample_dir: Path, record: Mapping[str, CopyEntry]) -> None:
    """Write the copy record atomically, only if it changed."""
    path = Path(sample_dir) / "data" / SOURCES_FILE
    document = {
        "schema": SOURCES_SCHEMA,
        "files": {k: record[k].as_json() for k in sorted(record)},
    }
    text = json.dumps(document, indent=2) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return
    if not record and not path.exists():
        return
    atomic_write_bytes(path, text.encode("utf-8"))
