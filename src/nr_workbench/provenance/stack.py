"""The layer stack of a fit, as one short string.

``THF|Cu|Ti|Si`` is how a reflectometrist writes a structure on a whiteboard,
and it is the one line that tells two fits apart at a glance. A listing that
shows ``film`` and a chi-squared has named the *script*; a listing that shows
``THF|Cu|Ti|Si`` has named the *model*, which is the thing being argued about.

Two sources, because a fit needs an answer whether or not it was generated from
a spec:

**At fit time**, :func:`describe` reads the assembled refl1d ``Stack`` off the
problem that is about to be fitted. That is the authoritative answer --- it is
the object the optimizer sees, not a guess about what the script builds --- and
it costs nothing, since the problem is already loaded.

**Afterwards**, :func:`from_fit_dir` reconstructs it from the files frozen in
the result directory: bumps' own ``*-expt.json`` export first, then the frozen
``spec.yaml``. This is what gives an answer for every fit recorded before the
stack was written into the index, which is all of them in any project that
predates this module.

Deliberately not parsed out of ``model.py``. The stack expression is ordinary
Python --- built in a loop, assembled in a helper, conditional on a flag --- and
a regex over it is wrong quietly, which for a structure label is the worst
failure available: a plausible stack that is not the one that was fitted.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

#: Between layers, in beam order. The convention every REF_L script already
#: uses, and what `nrw diff` and the notes in the reference experiment write.
SEPARATOR = "|"

#: Distinct stacks in one co-refinement, when a problem genuinely fits more
#: than one structure. Rare --- a spec declares one stack and instantiates it
#: per state --- but a real co-refinement of two structures must not silently
#: report only the first.
BETWEEN_MODELS = " / "

#: Longest stack shown in full. A 21-layer multilayer in a table cell wraps
#: into three lines and stops being a glance.
MAX_LAYERS = 8

#: Layers kept from each end when a stack is elided. The ambient and the
#: substrate are the anchors --- they say what the sample is *in* and *on* ---
#: so the middle is what goes.
_HEAD, _TAIL = 4, 2


def format_layers(names: list[str]) -> str:
    """Join layer names into the shorthand, eliding a very long stack.

    Args:
        names: Layer names in beam order, ambient first.

    Returns:
        ``THF|Cu|Ti|Si``, or ``""`` if nothing is named.
    """
    kept = [name.strip() for name in names if name and name.strip()]
    if not kept:
        return ""
    if len(kept) > MAX_LAYERS:
        hidden = len(kept) - _HEAD - _TAIL
        kept = [*kept[:_HEAD], f"+{hidden}", *kept[-_TAIL:]]
    return SEPARATOR.join(kept)


def describe(problem: Any) -> str:
    """The stack of a loaded bumps problem, ready to record.

    Args:
        problem: A bumps ``FitProblem``.

    Returns:
        The shorthand, or ``""`` if the problem exposes no readable sample.
        Never raises: a label that could not be built must not fail a fit that
        otherwise ran.
    """
    try:
        models = list(problem.models)
    except Exception:  # noqa: BLE001 - a label must never cost a fit
        return ""

    seen: dict[str, None] = {}
    for model in models:
        text = format_layers(_layer_names(getattr(model, "sample", None)))
        if text:
            seen.setdefault(text, None)
    return BETWEEN_MODELS.join(seen)


def _layer_names(sample: Any) -> list[str]:
    """Layer names off a refl1d ``Stack``, ambient first."""
    if sample is None:
        return []
    try:
        layers = list(sample)
    except TypeError:
        return []

    names = []
    for layer in layers:
        # A Slab carries the name; a Repeat or a mixture may only name its
        # material. Either is a better label than a positional index.
        name = getattr(layer, "name", None) or getattr(
            getattr(layer, "material", None), "name", None
        )
        names.append(str(name) if name else "")
    return names


def from_fit_dir(directory: Path) -> str:
    """Reconstruct the stack from the files frozen in a result directory.

    Args:
        directory: The fit directory.

    Returns:
        The shorthand, or ``""`` if neither source is readable.
    """
    return _cached(str(Path(directory).resolve()))


@lru_cache(maxsize=512)
def _cached(resolved: str) -> str:
    """Memoised on the path alone.

    Safe because a fit directory is write-once: :meth:`FitDirectory.create`
    refuses to reuse one, so the files this reads cannot change under the
    cache. The fits page asks for every row on every request, and the bumps
    export is tens of kilobytes of parameter references around the handful of
    names we want.
    """
    directory = Path(resolved)
    return _from_export(directory) or _from_spec(directory)


def _from_export(directory: Path) -> str:
    """The stack out of bumps' serialised experiments."""
    seen: dict[str, None] = {}
    for path in sorted((directory / "fit").glob("*-expt.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        layers = _dig(payload, "object", "sample", "layers")
        if not isinstance(layers, list):
            continue
        text = format_layers(
            [
                str(layer.get("name") or _dig(layer, "material", "name") or "")
                for layer in layers
                if isinstance(layer, dict)
            ]
        )
        if text:
            seen.setdefault(text, None)
    return BETWEEN_MODELS.join(seen)


def _from_spec(directory: Path) -> str:
    """The stack out of the frozen spec.

    The fallback's fallback, and the only one available for a fit that failed
    before bumps exported anything --- which is exactly the fit somebody is
    trying to tell apart from the one before it.
    """
    path = directory / "spec.yaml"
    if not path.is_file():
        return ""
    try:
        import yaml

        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - a spec we cannot read is not an error here
        return ""
    if not isinstance(payload, dict):
        return ""
    stack = payload.get("stack")
    if not isinstance(stack, list):
        return ""
    return format_layers(
        [
            str(layer.get("name") or layer.get("material") or "")
            for layer in stack
            if isinstance(layer, dict)
        ]
    )


def _dig(payload: Any, *keys: str) -> Any:
    """Walk nested mappings, returning ``None`` at the first missing key."""
    for key in keys:
        if not isinstance(payload, dict):
            return None
        payload = payload.get(key)
    return payload
