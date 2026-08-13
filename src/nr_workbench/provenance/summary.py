"""One-line descriptions of a fit, and of how it differs from the one before.

A fit id is content-identifying but not memorable. After a dozen runs the
listing is a wall of hashes, and the question a person actually has --- *which
one was the run where I freed the oxide?* --- has no answer short of opening
each manifest.

`nrw diff` already answers that for a chosen pair. What was missing is the
same answer applied automatically down the list: each fit against the previous
fit of the same model. That framing matters. Comparing against the row above
would usually mean comparing across models, which is noise; comparing against
the last time you ran *this* model is the edit you made.

Everything here works from index entries alone. The index is scanned by every
`ls` and by every page of the web UI, so a description that needed to open a
manifest per row would make the listing quadratic in the thing it is trying to
make usable.
"""

from __future__ import annotations

from typing import Any

# Settings that say nothing about the science and would only add noise to a
# change line: bookkeeping, and knobs that affect speed rather than the answer.
_UNINTERESTING_SETTINGS = frozenset({"replicate_of", "parallel"})

#: Longest note we will inline before truncating.
_NOTE_LIMIT = 72

#: What :func:`compare` says when a model has no predecessor. Named because it
#: is the one change line that carries no information --- a reader looking at
#: the first fit of a model can see that from the list --- so a surface with
#: something better to show in that space needs to recognise it rather than
#: match the literal in two places.
FIRST_RUN = "first run of this model"

#: How much worse chi-squared has to get before the move is called a
#: regression rather than reported as a number. Reduced chi-squared moves by a
#: few percent between equivalent fits; half again as large is a different
#: model, not a different run of the same one.
REGRESSION_FACTOR = 1.5


def describe(entry: dict[str, Any]) -> str:
    """Return a short human description of one fit.

    The user's own note wins when there is one --- nothing generated competes
    with "first co-refinement of the full sequence". Otherwise the fit is
    described by how it was run, which is at least enough to tell a quick
    amoeba pass from an overnight DREAM.

    Args:
        entry: A fit entry from the index.

    Returns:
        A single line, never empty.
    """
    note = _first_line(entry.get("note"))
    if note:
        return note
    return _settings_phrase(entry) or str(entry.get("model") or "fit")


def compare(entry: dict[str, Any], previous: dict[str, Any] | None) -> str:
    """Say what changed between a fit and the previous run of the same model.

    Reports every dimension that moved rather than only the most important
    one, because they are independent facts: a run can change both the data
    and the settings, and being told only about the data would mislead.

    Args:
        entry: The later fit.
        previous: The earlier fit of the same model, or ``None`` if this is
            the first.

    Returns:
        A single line describing the difference.
    """
    if previous is None:
        return FIRST_RUN

    parts: list[str] = []
    if _differs(entry, previous, "data_digest"):
        parts.append("data changed")
    elif not _both_have(entry, previous, "data_digest") and _differs(
        entry, previous, "inputs_digest"
    ):
        # Fits recorded before `data_digest` existed can only say that *some*
        # input moved, and their `inputs_digest` counts the script as one. Say
        # the weaker true thing rather than the stronger false one.
        parts.append("inputs changed")
    if _differs(entry, previous, "script_sha256"):
        parts.append("model changed")
    parts.extend(_settings_changes(entry, previous))

    if not parts:
        # Nothing we record differs. Either it is a genuine replicate, or the
        # difference is in a field this fit is too old to have recorded --- and
        # the run key, which covers settings and environment too, can tell the
        # two apart even when the details are unavailable.
        if _differs(entry, previous, "run_key"):
            parts.append("settings or environment changed")
        else:
            parts.append("replicate")

    trend = _chisq_trend(entry, previous)
    if trend:
        parts.append(trend)
    return "; ".join(parts)


def annotate(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach a description and a change line to each fit, newest first.

    Args:
        entries: Fit entries, newest first, as :meth:`FitIndex.fits` returns
            them.

    Returns:
        Copies of the entries, each with ``description``, ``change`` and
        ``compared_to`` added. ``compared_to`` is the fit id the change line
        is relative to, or ``None`` for the first run of a model.
    """
    # Walk oldest-first so "the previous fit of this model" is simply the last
    # one seen, then restore the caller's ordering.
    previous_by_model: dict[tuple[Any, Any], dict[str, Any]] = {}
    annotated: list[dict[str, Any]] = []
    for entry in reversed(entries):
        key = (entry.get("sample"), entry.get("model"))
        previous = previous_by_model.get(key)
        row = dict(entry)
        row["description"] = describe(entry)
        row["change"] = compare(entry, previous)
        row["compared_to"] = previous.get("fit_id") if previous else None
        annotated.append(row)
        previous_by_model[key] = entry
    annotated.reverse()
    return annotated


def annotation_for(entries: list[dict[str, Any]], fit_id: str) -> dict[str, Any] | None:
    """Return one fit's annotated row, or ``None`` if it is not in the index.

    Args:
        entries: Fit entries, newest first.
        fit_id: The fit to describe.

    Returns:
        The annotated row, as :func:`annotate` produces it.
    """
    return next(
        (row for row in annotate(entries) if str(row.get("fit_id")) == fit_id), None
    )


def _differs(a: dict[str, Any], b: dict[str, Any], key: str) -> bool:
    """Whether a field differs, treating a missing field as no evidence.

    Fits recorded before a field existed must not read as "changed" against
    fits that have it --- that would make the whole history look churned.
    """
    left, right = a.get(key), b.get(key)
    if left is None or right is None:
        return False
    return left != right


def _both_have(a: dict[str, Any], b: dict[str, Any], key: str) -> bool:
    """Whether a field is present on both sides, so a comparison means something."""
    return a.get(key) is not None and b.get(key) is not None


def _settings_changes(a: dict[str, Any], b: dict[str, Any]) -> list[str]:
    """Name the fit settings that moved, most informative first."""
    now = _settings_of(a)
    was = _settings_of(b)
    if not now or not was:
        # One of them predates settings being indexed; `method` has always
        # been there, so fall back to the one thing we can still compare.
        if _differs(a, b, "method"):
            return [f"method {b.get('method')} -> {a.get('method')}"]
        return []

    # A method change brings its own vocabulary -- `steps` for amoeba,
    # `samples` and `burn` for dream -- so reporting the knobs that merely
    # stopped applying ("steps 2000 -> None") buries the one fact that matters.
    method_changed = now.get("method") != was.get("method")

    changes = []
    for key in sorted(set(now) | set(was), key=_settings_order):
        before, after = was.get(key), now.get(key)
        if before == after:
            continue
        if before is None or after is None:
            if method_changed:
                continue
            changes.append(
                f"{key} {_number(after)} added" if before is None else f"{key} dropped"
            )
            continue
        changes.append(f"{key} {_number(before)} -> {_number(after)}")

    if len(changes) > 3:
        extra = len(changes) - 3
        return [*changes[:3], f"and {extra} more setting{'' if extra == 1 else 's'}"]
    return changes


def _settings_of(entry: dict[str, Any]) -> dict[str, Any]:
    """The comparable fit settings, with bookkeeping and ``None`` removed."""
    settings = entry.get("settings")
    if not isinstance(settings, dict):
        return {}
    return {
        key: value
        for key, value in settings.items()
        if key not in _UNINTERESTING_SETTINGS and value is not None
    }


def _settings_order(key: str) -> tuple[int, str]:
    """Sort settings so the ones that change the answer come first."""
    priority = {"method": 0, "samples": 1, "steps": 1, "burn": 2, "pop": 3, "seed": 4}
    return (priority.get(key, 5), key)


def _chisq_trend(a: dict[str, Any], b: dict[str, Any]) -> str:
    """Describe the chi-squared move, or return an empty string.

    A regression is named as one. `chisq 1.306 -> 16.58` reads as a neutral fact
    and was read as one: a real session saw that line, kept the model edit that
    caused it, and spent eleven more fits changing optimisers. The number was
    never the problem -- how unremarkable it looked was.
    """
    now, was = a.get("chisq"), b.get("chisq")
    if not isinstance(now, int | float) or not isinstance(was, int | float):
        return ""
    if now == was:
        return "chisq unchanged"
    line = f"chisq {was:.4g} -> {now:.4g}"
    if was > 0 and now > was * REGRESSION_FACTOR:
        line += f" ({now / was:.3g}x WORSE)"
    return line


def _settings_phrase(entry: dict[str, Any]) -> str:
    """Describe a fit by how it was run, for fits with no note."""
    settings = _settings_of(entry)
    method = str(settings.get("method") or entry.get("method") or "").strip()
    pieces = [method] if method else []
    for key in ("samples", "steps", "burn"):
        value = settings.get(key)
        if isinstance(value, int | float) and value:
            pieces.append(f"{_number(value)} {key}")
    free = entry.get("n_free")
    if isinstance(free, int) and free:
        pieces.append(f"{free} free")
    return ", ".join(pieces)


def _number(value: Any) -> str:
    """Format a setting compactly, so 100000 reads as 100k."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return str(value)
    if isinstance(value, int) and abs(value) >= 10_000:
        thousands = value / 1000
        return (
            f"{thousands:.0f}k" if thousands == int(thousands) else f"{thousands:.1f}k"
        )
    if isinstance(value, float):
        return f"{value:.4g}"
    return str(value)


def _first_line(note: Any) -> str:
    """The first line of a note, trimmed to something a table can hold."""
    if not isinstance(note, str):
        return ""
    line = note.strip().splitlines()[0].strip() if note.strip() else ""
    if len(line) > _NOTE_LIMIT:
        return line[: _NOTE_LIMIT - 1].rstrip() + "…"
    return line
