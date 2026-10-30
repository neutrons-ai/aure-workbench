"""The experiment, its runs and samples, as the page and the command line report them.

Both show the same things, and each used to build its own dictionary for them.
They had already drifted: the command line said "the source does not list it"
where the page said "the data source does not list it", and a field added to
one surface was missing from the other. One builder for each, here.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from nr_workbench.experiment.held import HeldRun
from nr_workbench.experiment.model import (
    SERIES,
    Catalog,
    ExperimentContext,
    RunEntry,
    RunKey,
)
from nr_workbench.experiment.render import sample_md_relpath
from nr_workbench.instrument.reduced import COMBINED, SEGMENT

#: The state shown for a run the catalog records but the data source does not
#: list -- deleted at the facility, or a source that cannot be reached.
NOT_LISTED = "not listed"

#: The state shown for a run the data source does not list but a sample holds
#: in its own ``data/`` folder: copied there by hand, or kept after the source
#: lost it. Its files are where a fit of the sample reads them.
IN_SAMPLE = "in sample"


def run_row(
    key: RunKey, view: Any, entry: RunEntry | None, held: HeldRun | None = None
) -> dict[str, Any]:
    """One run: what the source lists, how far it has arrived, what was decided.

    Args:
        key: Which run.
        view: Its :class:`~nr_workbench.experiment.live.RunView`, or ``None``
            when only the catalog or a sample knows it.
        entry: Its catalog record, or ``None`` when it has none yet.
        held: What a sample holds of it in its ``data/`` folder, or ``None``.

    Returns:
        The JSON-ready row.
    """
    source = view.source if view is not None else None
    status = view.status if view is not None else None
    if status is not None:
        state, reason, complete = status.state, status.reason, status.complete
    elif held is not None:
        state, reason, complete = IN_SAMPLE, _held_reason(held), key.kind != SERIES
    else:
        state, reason, complete = NOT_LISTED, "the data source does not list it", False

    if source is not None:
        segments = list(source.segments)
        artifacts = [
            {"name": f.name, "role": f.role, "segment": f.segment} for f in source.files
        ]
        thetas = list(source.thetas)
    elif held is not None:
        segments = list(held.segments)
        artifacts = _held_artifacts(held)
        thetas = list(held.thetas)
    else:
        segments, artifacts, thetas = [], [], []

    # A run in a sample whose type was never set is steady, as sample.md
    # writes it; an unassigned one has no type to show.
    if entry is not None:
        measurement = entry.measurement_type if entry.sample_id else entry.measurement
    else:
        measurement = ""

    return {
        "key": key.slug(),
        "run": key.run,
        "kind": key.kind,
        "state": state,
        "reason": reason,
        "complete": bool(complete),
        # The source's title and start time when it lists the run; the catalog
        # keeps a snapshot of both so a run that has gone is still recognizable.
        "title": (source.title if source else "")
        or (entry.title if entry else "")
        or (held.title if held else ""),
        "start_time": (source.start_time if source else "")
        or (entry.start_time if entry else ""),
        "segments": segments,
        "n_segments": source.n_segments if source else None,
        # Every file of the measurement and what it is to it: the segments a
        # fit co-refines, and other artifacts such as the combined curve.
        "artifacts": artifacts,
        "thetas": [round(t, 3) if t is not None else None for t in thetas],
        "n_slices": held.n_slices if held is not None else 0,
        "announced": view is not None and view.announcement is not None,
        # The sample the catalog assigns it to. A sample whose folder holds it
        # is in held_by; the run is the catalog's once it is assigned.
        "sample": entry.sample_id if entry else None,
        "measurement": measurement,
        "condition": entry.condition if entry else "",
        "include": entry.include if entry else True,
        "note": entry.note if entry else "",
        "rev": entry.rev if entry else 0,
        # The samples whose data/ folders hold its files.
        "held_by": list(held.sample_ids) if held is not None else [],
        # Whether the page may assign it. A series is listed only: its run's
        # metadata is recorded on the run's steady key.
        "organizable": key.kind != SERIES,
    }


def run_rows(
    views: Mapping[RunKey, Any],
    catalog: Catalog,
    held: Mapping[RunKey, HeldRun],
) -> list[dict[str, Any]]:
    """Every run the source, the catalog or a sample knows, in run order.

    Args:
        views: The source's runs, by key.
        catalog: The catalog.
        held: What the samples hold, by key.

    Returns:
        One row per run.
    """
    keys = set(views) | set(catalog.runs) | set(held)
    return [
        run_row(key, views.get(key), catalog.runs.get(key), held.get(key))
        for key in sorted(keys)
    ]


def _held_reason(held: HeldRun) -> str:
    """Where a held run's files are, for the row's tooltip."""
    if held.key.kind == SERIES:
        return (
            f"{held.n_slices} slices in {held.folder}; listed here, organized "
            "on its run's steady row"
        )
    return f"its files are in {held.folder}, where a fit of the sample reads them"


def _held_artifacts(held: HeldRun) -> list[dict[str, Any]]:
    """A held steady run's files, described as the source describes its own."""
    if held.key.kind == SERIES:
        return []
    artifacts = []
    for index, relative in enumerate(held.files):
        segment = held.segments[index] if index < len(held.segments) else None
        artifacts.append(
            {
                "name": Path(relative).name,
                "role": SEGMENT if segment is not None else COMBINED,
                "segment": segment,
            }
        )
    return artifacts


def sample_card(root: Path, catalog: Catalog, sample_id: str) -> dict[str, Any]:
    """A sample the catalog manages: its context, and which runs it holds.

    Args:
        root: Project root, to say whether ``sample.md`` exists yet.
        catalog: The catalog.
        sample_id: A sample in it.

    Returns:
        The JSON-ready card.
    """
    context = catalog.context_for(sample_id)
    runs = catalog.runs_for(sample_id)
    return {
        "id": sample_id,
        "managed": True,
        "on_disk": (Path(root) / sample_md_relpath(sample_id)).is_file(),
        "title": context.title,
        "description": context.description,
        "details": context.details,
        "mounting": context.mounting,
        "measurement_conditions": context.measurement_conditions,
        "fits_to_perform": context.fits_to_perform,
        "rev": context.rev,
        "runs": [e.key.run for e in runs if e.include],
        "excluded": [e.key.run for e in runs if not e.include],
    }


def experiment_card(record: ExperimentContext) -> dict[str, Any]:
    """The experiment's own context: what every sample in it is part of.

    Args:
        record: The catalog's experiment record.

    Returns:
        The JSON-ready card, with the revision an edit is made against. The
        columns a newer nrw added are left out, as they are of a sample's
        card: they are that version's to show.
    """
    return {
        "summary": record.summary,
        "hypotheses": record.hypotheses,
        "notes": record.notes,
        "rev": record.rev,
    }


def unmanaged_card(sample_id: str, runs: Iterable[int] = ()) -> dict[str, Any]:
    """A sample on disk that the catalog does not manage, or not yet.

    Args:
        sample_id: The sample's directory name.
        runs: The runs its ``data/`` folder holds.

    Returns:
        The JSON-ready card.
    """
    return {
        "id": sample_id,
        "managed": False,
        "on_disk": True,
        "runs": sorted(set(runs)),
        "excluded": [],
    }


def settings_view(root: Path, *, catalogued_runs: int | None = 0) -> dict[str, Any]:
    """The experiment's settings as the Settings page and ``nrw experiment
    settings`` show them: what is set, what follows nrw's default, which
    choices exist yet, and what is wrong.

    Read from ``nrw.toml`` alone -- nothing here touches the data mount.

    Args:
        root: Project root.
        catalogued_runs: How many runs the catalog holds, which makes an IPTS
            change something to confirm.
    """
    import dataclasses

    from nr_workbench.experiment.config import default_path, experiment_config
    from nr_workbench.problems import Problem
    from nr_workbench.project import experiment_schema as project
    from nr_workbench.project.config import (
        FACILITY_REDUCTION_LOCATION,
        ProjectConfigError,
        load_config,
    )
    from nr_workbench.project.settings import read as read_settings
    from nr_workbench.project.tomlfile import TomlEditError

    problems: list[Problem] = []
    current = None
    try:
        current = read_settings(root)
    except (TomlEditError, OSError) as exc:
        problems.append(Problem("config", f"nrw.toml cannot be edited here: {exc}"))
    try:
        config = experiment_config(load_config(root))
    except ProjectConfigError:
        config = experiment_config(None, root=Path(root).resolve())
    problems.extend(config.problems)
    if catalogued_runs is None:
        problems.append(
            Problem(
                "catalog",
                "The experiment catalog cannot be read, so a change of IPTS asks "
                "first: nrw cannot tell whether it holds runs.",
            )
        )

    chosen = current.experiment if current else {}
    default_folder = default_path(Path(root).resolve(), config.ipts)
    values: dict[str, Any] = {
        "ipts": current.ipts if current else "",
        "label": current.label if current else "",
    }
    defaults: dict[str, Any] = {}
    for table, keys in project.EXPERIMENT_KEYS.items():
        prefix = table.split(".", 1)[1]
        for key, default in keys.items():
            values[f"{prefix}.{key}"] = chosen.get(table, {}).get(key)
            defaults[f"{prefix}.{key}"] = default
    return {
        "schema": "nrw-settings/1",
        "revision": current.revision if current else None,
        "editable": current is not None,
        "values": values,
        "defaults": defaults,
        "default_path": str(default_folder) if default_folder else None,
        "facility_location": FACILITY_REDUCTION_LOCATION,
        "effective": {
            "ipts": config.ipts,
            "location": config.source.location,
            "path": str(config.source.path) if config.source.path else None,
            "needs_setup": config.needs_setup,
            "catalog_kind": config.catalog_kind,
        },
        "options": {
            "source": [dataclasses.asdict(o) for o in project.SOURCE_OPTIONS],
            "feed": [dataclasses.asdict(o) for o in project.FEED_OPTIONS],
            "catalog": [dataclasses.asdict(o) for o in project.CATALOG_OPTIONS],
        },
        "ranges": {
            "source.settle_seconds": list(project.SETTLE_RANGE),
            "feed.poll_seconds": list(project.POLL_RANGE),
        },
        "suggested_ipts": ipts_in_path(Path(root)),
        "catalogued_runs": catalogued_runs,
        "problems": [p.as_dict() for p in problems],
    }


def ipts_in_path(root: Path) -> str | None:
    """An IPTS the project's own path names, e.g. /SNS/REF_L/IPTS-34347/shared/x."""
    from nr_workbench.project.experiment_schema import normalize_ipts

    for part in Path(root).absolute().parts:
        if part.upper().startswith("IPTS-"):
            found = normalize_ipts(part)
            if found:
                return found
    return None
