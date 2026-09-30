"""Which fits a person stars, finalizes, sets aside or deletes -- as index events.

Curation is recorded the way a promotion always has been: appended to
``.nrw/index.jsonl`` and never rewritten, with who and when. It merges in git,
and a fit's history -- the bad ones included -- stays readable. What a fit is
now is the last word on it: starred until unstarred, discarded until restored,
and a label held by the last fit of its sample promoted to it.

**Discard, then delete.** Discarding hides a fit from the listings and keeps
every file, and a reason is kept with it. Deleting frees the disk: a second,
explicit step, only for a fit already discarded, and only when nothing uses it
-- no report cites it or drew a figure from it, no other fit read its files,
and no ISAAC record was made from it. The index keeps the record that it ran;
the Fits page shows it as deleted.

**The answer is never discarded.** A fit holding a label -- ``final`` -- cannot
be set aside; finalize another first. A discarded fit cannot be finalized or
starred until it is restored.

These are a person's decisions, as a promotion is: the commands refuse under
``NRW_AGENT``, and the web server they are reached from is read-only for an
agent. Nothing here imports click; the commands and the page wrap it.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nr_workbench.project.layout import ProjectLayout
from nr_workbench.provenance.index import EVENT_PROMOTE, EVENT_SUPERSEDE, FitIndex
from nr_workbench.provenance.lookup import fit_dir as find_fit_dir
from nr_workbench.provenance.record import format_timestamp, utc_now

EVENT_STAR = "star"
EVENT_UNSTAR = "unstar"
EVENT_DISCARD = "discard"
EVENT_RESTORE = "restore"
EVENT_DELETE = "delete"

#: The label a fit is finalized with: the answer, for its sample.
FINAL = "final"

#: The longest reason kept: it is read in a listing, beside the fit.
MAX_REASON = 500


class CurationRefused(ValueError):
    """Not done: the message says why, and what to do instead."""


class NoSuchFit(CurationRefused):
    """No fit matches, or the prefix matches several."""


class InputsChanged(CurationRefused):
    """Its inputs changed since it ran: finalizing it needs saying so."""

    #: What the request must say to go ahead, as the page's API reports it.
    needs = "force"


@dataclass(frozen=True)
class Curation:
    """What a person has said about one fit, as it stands.

    Attributes:
        starred: Whether it is starred.
        discarded: Why, by whom and when it was set aside; ``None`` if not.
        deleted: By whom and when its files were deleted; ``None`` if not.
        labels: The labels it holds now -- ``final``, say.
    """

    starred: bool = False
    discarded: dict[str, Any] | None = None
    deleted: dict[str, Any] | None = None
    labels: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        """The JSON form."""
        return {
            "starred": self.starred,
            "discarded": self.discarded,
            "deleted": self.deleted,
            "labels": list(self.labels),
        }


#: The curation of a fit nothing has been said about.
NONE = Curation()


def curation_of(entries: list[dict[str, Any]]) -> dict[str, Curation]:
    """Each fit's curation, replayed from the index's entries in order.

    Args:
        entries: The index, oldest first (:meth:`FitIndex.entries`).

    Returns:
        Fit id to its curation, for the fits something was said about.
    """
    starred: dict[str, bool] = {}
    discarded: dict[str, dict[str, Any]] = {}
    deleted: dict[str, dict[str, Any]] = {}
    # The last promotion of a label, per sample, holds it.
    holders: dict[tuple[str, str], str] = {}
    for entry in entries:
        event, fit_id = entry.get("event"), str(entry.get("fit_id") or "")
        if not fit_id:
            continue
        if event in (EVENT_STAR, EVENT_UNSTAR):
            starred[fit_id] = event == EVENT_STAR
        elif event == EVENT_DISCARD:
            discarded[fit_id] = {k: entry.get(k) for k in ("reason", "who", "at")}
        elif event == EVENT_RESTORE:
            discarded.pop(fit_id, None)
        elif event == EVENT_DELETE:
            deleted[fit_id] = {k: entry.get(k) for k in ("who", "at")}
        elif event == EVENT_PROMOTE:
            holders[(str(entry.get("sample")), str(entry.get("label")))] = fit_id
    labels: dict[str, list[str]] = {}
    for (_, label), fit_id in holders.items():
        labels.setdefault(fit_id, []).append(label)
    return {
        fit_id: Curation(
            starred=starred.get(fit_id, False),
            discarded=discarded.get(fit_id),
            deleted=deleted.get(fit_id),
            labels=tuple(sorted(labels.get(fit_id, []))),
        )
        for fit_id in {*starred, *discarded, *deleted, *labels}
    }


# --------------------------------------------------------------------------
# Deciding
# --------------------------------------------------------------------------


def star(layout: ProjectLayout, fit_id: str, *, starred: bool = True) -> bool:
    """Star a fit, or take its star away.

    Args:
        layout: The project.
        fit_id: The fit, or a unique prefix of its id.
        starred: Star it (``True``) or unstar it.

    Returns:
        Whether anything changed.

    Raises:
        CurationRefused: No such fit, or it is discarded.
    """
    index, entry, state = _look_up(layout, fit_id)
    if starred and state.discarded:
        raise CurationRefused(
            f"{entry['fit_id']} is discarded: restore it before starring it."
        )
    if state.starred == starred:
        return False
    _append(index, entry, EVENT_STAR if starred else EVENT_UNSTAR)
    return True


def discard(layout: ProjectLayout, fit_id: str, *, reason: str) -> bool:
    """Set a fit aside: out of the listings, every file kept.

    Args:
        layout: The project.
        fit_id: The fit, or a unique prefix of its id.
        reason: Why -- kept with it, and shown when it is restored.

    Returns:
        Whether anything changed.

    Raises:
        CurationRefused: No such fit, no reason, or it holds a label.
    """
    reason = _reason(reason)
    index, entry, state = _look_up(layout, fit_id)
    if state.discarded:
        return False
    if state.labels:
        raise CurationRefused(
            f"{entry['fit_id']} is the {state.labels[0]} fit of "
            f"{entry.get('sample')}: an answer is not set aside. Finalize "
            "another fit first."
        )
    _append(index, entry, EVENT_DISCARD, reason=reason)
    return True


def restore(layout: ProjectLayout, fit_id: str) -> bool:
    """Bring a discarded fit back into the listings.

    Returns:
        Whether anything changed.

    Raises:
        CurationRefused: No such fit, or its files were deleted.
    """
    index, entry, state = _look_up(layout, fit_id)
    if not state.discarded:
        return False
    if state.deleted:
        raise CurationRefused(
            f"{entry['fit_id']}'s files were deleted: there is nothing to restore."
        )
    _append(index, entry, EVENT_RESTORE)
    return True


def delete_files(layout: ProjectLayout, fit_id: str) -> Path:
    """Delete a discarded fit's files; the record that it ran stays.

    Args:
        layout: The project.
        fit_id: The fit, or a unique prefix of its id.

    Returns:
        The directory removed, relative to the project.

    Raises:
        CurationRefused: As :func:`deletable` says.
    """
    index, entry, state, directory = _deletable(layout, fit_id)
    shutil.rmtree(directory)
    _append(index, entry, EVENT_DELETE, reason=(state.discarded or {}).get("reason"))
    return directory.relative_to(layout.root)


def deletable(layout: ProjectLayout, fit_id: str) -> Path:
    """The directory deleting a fit's files would remove -- asked before asking.

    Args:
        layout: The project.
        fit_id: The fit, or a unique prefix of its id.

    Returns:
        The fit's result directory.

    Raises:
        CurationRefused: No such fit; not discarded; already deleted; used by a
            report, a figure, another fit or an ISAAC record; or its directory
            is not one this would remove.
    """
    return _deletable(layout, fit_id)[3]


def _deletable(
    layout: ProjectLayout, fit_id: str
) -> tuple[FitIndex, dict[str, Any], Curation, Path]:
    index, entry, state = _look_up(layout, fit_id)
    resolved = str(entry["fit_id"])
    if not state.discarded:
        raise CurationRefused(
            f"Discard {resolved} first: deleting its files is the second step, "
            "for a fit already set aside."
        )
    if state.deleted:
        raise CurationRefused(f"{resolved}'s files were deleted already.")
    directory = find_fit_dir(layout, entry)
    if directory is None:
        raise CurationRefused(f"{resolved} has no files left to delete.")
    users = used_by(layout, index, entry, directory)
    if users:
        raise CurationRefused(
            f"{resolved} is used by " + "; ".join(users) + ". Its files stay."
        )
    # Never through a link, and never anything but a fit's own directory.
    results = directory.parent
    if (
        directory.is_symlink()
        or results.is_symlink()
        or results.name != "results"
        or directory.name != resolved
    ):
        raise CurationRefused(f"{directory} is not a fit directory this removes.")
    return index, entry, state, directory


def used_by(
    layout: ProjectLayout,
    index: FitIndex,
    entry: dict[str, Any],
    directory: Path,
) -> list[str]:
    """What would lose its footing if this fit's files were deleted.

    Args:
        layout: The project.
        index: The fit index.
        entry: The fit's index entry.
        directory: Its result directory.

    Returns:
        One phrase per user, for a person to read; empty when nothing uses it.
    """
    from nr_workbench.notes import notes_about, sample_notes

    fit_id = str(entry["fit_id"])
    sample = entry.get("sample")
    users: list[str] = []
    if sample:
        for note in notes_about(sample_notes(layout.root, str(sample)), fit_id):
            users.append(f"the report {note.path}")
        reports = layout.sample(str(sample)) / "reports"
        for manifest in sorted(reports.glob("**/*.figures.json")):
            try:
                read = json.loads(manifest.read_text(encoding="utf-8")).get("fits")
            except (OSError, ValueError, AttributeError):
                continue
            if isinstance(read, list) and fit_id in read:
                users.append(
                    f"the figures of {manifest.relative_to(layout.root).as_posix()}"
                )
    if (directory / "isaac").is_dir():
        users.append("the ISAAC records made from it (isaac/)")
    own = directory.relative_to(layout.root).as_posix() + "/"
    for other in index.fits():
        other_id = str(other.get("fit_id") or "")
        other_dir = find_fit_dir(layout, other) if other_id != fit_id else None
        if other_dir is None:
            continue
        try:
            inputs = json.loads((other_dir / "inputs.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        paths = [
            i.get("path") for i in inputs.get("inputs") or [] if isinstance(i, dict)
        ]
        if any(isinstance(p, str) and p.startswith(own) for p in paths):
            users.append(f"fit {other_id}, which read its files")
    return users


def promote(
    layout: ProjectLayout,
    fit_id: str,
    *,
    label: str = FINAL,
    reason: str,
    force: bool = False,
) -> dict[str, Any]:
    """Mark a fit as the answer for its sample, recording who decided and why.

    "Final" is never implicit. The most recent fit is not the answer, and the
    lowest chi-squared is not automatically the answer: a person decides, and
    that decision is itself provenance. The fit that held the label before is
    superseded, and that is recorded too.

    Args:
        layout: The project.
        fit_id: The fit, or a unique prefix of its id.
        label: The label to apply.
        reason: Why this fit is the answer. Required.
        force: Promote it though its inputs have changed since it ran.

    Returns:
        The promotion recorded, with ``supersedes``: the fit that held the
        label before, if another.

    Raises:
        CurationRefused: No reason; no such fit; it did not succeed, is
            discarded, or its files are gone.
        InputsChanged: Its inputs changed, and ``force`` was not given.
    """
    from nr_workbench.provenance.whence import Freshness, check_inputs

    if not reason.strip():
        raise CurationRefused("A reason is required: it is the part worth keeping.")
    index, entry, state = _look_up(layout, fit_id)
    resolved = str(entry["fit_id"])
    if entry.get("status") != "ok":
        raise CurationRefused(
            f"{resolved} has status '{entry.get('status')}'. Only a successful fit "
            "can be promoted."
        )
    if state.discarded:
        raise CurationRefused(
            f"{resolved} is discarded: restore it before finalizing it."
        )
    directory = find_fit_dir(layout, entry)
    if directory is None:
        raise CurationRefused(f"Fit directory for {resolved} is missing.")
    _, freshness = check_inputs(directory, layout.root)
    changed = freshness not in (Freshness.FRESH, Freshness.UNKNOWN)
    if changed and not force:
        # The caller says what can be done: --force, or a button.
        raise InputsChanged(
            f"{resolved} is {freshness.value.upper()}: its inputs have changed "
            "since it ran."
        )
    previous = index.current_label(label, sample=entry.get("sample"))
    supersedes = (
        str(previous.get("fit_id"))
        if previous and previous.get("fit_id") != resolved
        else None
    )
    now = format_timestamp(utc_now())
    if supersedes:
        # Superseding is recorded, never erased: what was once considered
        # final is part of the story.
        index.append(
            {
                "fit_id": supersedes,
                "sample": previous.get("sample") if previous else None,
                "label": label,
                "superseded_by": resolved,
                "at": now,
            },
            event=EVENT_SUPERSEDE,
        )
    promotion = {
        "fit_id": resolved,
        "sample": entry.get("sample"),
        "model": entry.get("model"),
        "label": label,
        "reason": reason.strip(),
        "who": current_user(),
        "at": now,
        "forced": bool(force) and changed,
        "supersedes": supersedes,
    }
    index.append(promotion, event=EVENT_PROMOTE)
    return promotion


def current_user() -> str:
    """Best-effort identity of whoever made the decision."""
    import getpass

    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001 - an identity is best-effort
        return "unknown"


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _look_up(
    layout: ProjectLayout, fit_id: str
) -> tuple[FitIndex, dict[str, Any], Curation]:
    """The index, the fit's entry, and what has been said about it."""
    index = FitIndex(layout.index_file)
    matches = index.resolve(fit_id)
    if not matches:
        raise NoSuchFit(f"No fit matching {fit_id!r}. See `nrw ls`.")
    if len(matches) > 1:
        raise NoSuchFit(
            f"{fit_id!r} matches {len(matches)} fits: "
            + ", ".join(str(m["fit_id"]) for m in matches[:5])
        )
    entry = matches[0]
    state = curation_of(index.entries()).get(str(entry["fit_id"]), NONE)
    return index, entry, state


def _append(index: FitIndex, entry: dict[str, Any], event: str, **more: Any) -> None:
    index.append(
        {
            "fit_id": entry["fit_id"],
            "sample": entry.get("sample"),
            "who": current_user(),
            "at": format_timestamp(utc_now()),
            **{k: v for k, v in more.items() if v is not None},
        },
        event=event,
    )


def _reason(reason: str) -> str:
    """A reason: said, and short enough to read beside the fit."""
    text = " ".join(str(reason or "").split())
    if not text:
        raise CurationRefused(
            "A reason is required: it is what makes the choice legible."
        )
    if len(text) > MAX_REASON:
        raise CurationRefused(
            f"The reason is {len(text)} characters; the limit is {MAX_REASON}."
        )
    return text
