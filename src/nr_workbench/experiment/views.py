"""The experiment's runs and samples, as the page and the command line report them.

Both show the same things, and each used to build its own dictionary for them.
They had already drifted: the command line said "the source does not list it"
where the page said "the data source does not list it", and a field added to
one surface was missing from the other. One builder for each, here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from nr_workbench.experiment.model import Catalog, RunEntry, RunKey
from nr_workbench.experiment.render import sample_md_relpath

#: The state shown for a run the catalog records but the data source does not
#: list -- deleted at the facility, or a source that cannot be reached.
NOT_LISTED = "not listed"


def run_row(key: RunKey, view: Any, entry: RunEntry | None) -> dict[str, Any]:
    """One run: what the source lists, how far it has arrived, what was decided.

    Args:
        key: Which run.
        view: Its :class:`~nr_workbench.experiment.live.RunView`, or ``None``
            when only the catalog knows it.
        entry: Its catalog record, or ``None`` when it has none yet.

    Returns:
        The JSON-ready row.
    """
    source = view.source if view is not None else None
    status = view.status if view is not None else None
    return {
        "key": key.slug(),
        "run": key.run,
        "kind": key.kind,
        "state": status.state if status else NOT_LISTED,
        "reason": status.reason if status else "the data source does not list it",
        "complete": bool(status and status.complete),
        # The source's title and start time when it lists the run; the catalog
        # keeps a snapshot of both so a run that has gone is still recognizable.
        "title": (source.title if source else "") or (entry.title if entry else ""),
        "start_time": (source.start_time if source else "")
        or (entry.start_time if entry else ""),
        "segments": list(source.segments) if source else [],
        "n_segments": source.n_segments if source else None,
        "thetas": [round(t, 3) if t is not None else None for t in source.thetas]
        if source
        else [],
        "announced": view is not None and view.announcement is not None,
        "sample": entry.sample_id if entry else None,
        "measurement": entry.measurement if entry else "",
        "condition": entry.condition if entry else "",
        "include": entry.include if entry else True,
        "note": entry.note if entry else "",
        "rev": entry.rev if entry else 0,
    }


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


def unmanaged_card(sample_id: str) -> dict[str, Any]:
    """A sample on disk that the catalog does not manage, or not yet.

    Args:
        sample_id: The sample's directory name.

    Returns:
        The JSON-ready card.
    """
    return {
        "id": sample_id,
        "managed": False,
        "on_disk": True,
        "runs": [],
        "excluded": [],
    }
