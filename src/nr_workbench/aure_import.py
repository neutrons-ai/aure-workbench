"""Translate a finished AuRE run into an nrw model spec.

AuRE's answer is a ``ModelDefinition``: a substrate, an ordered list of layers,
an ambient medium, and a geometry flag. An nrw spec is materials plus an
ordered ``stack`` plus free ``parameters``. The translation is mechanical, and
doing it in code rather than by asking a model to retype the numbers is the
point -- a transcription error in a thickness is invisible and survives into
everything downstream.

**Layer order is the whole of the difficulty**, so it is stated once here and
tested rather than commented at each use.

``ModelDefinition.layers`` runs **substrate first**: ``layers[0]`` is the layer
touching the substrate, ``layers[-1]`` the one touching the ambient. refl1d
takes the **last** entry of a stack as the medium the neutron arrives from, and
both packages build from that same fact:

* front reflection -- ``[substrate, L0, ..., Ln, ambient]``
* back reflection  -- ``[ambient, Ln, ..., L0, substrate]``

So the nrw ``stack`` is AuRE's assembly order verbatim. Getting it backwards
does not raise: the fit converges, reports a chi-squared in the hundreds, and
names no cause -- which is why `spec/validate.py` checks the ordering against
the measured critical edge, and why this module never reverses a list without
a test naming the geometry.

**A co-refinement of several states is imported one state at a time**: the
state holding the run being imported. :func:`state_model` gives the model as
that state was built -- its own ``layers`` / ``substrate`` replace the
template's, its ``ambient`` merges in -- and :func:`state_fit` the fit as that
state sees it: AuRE names a parameter one state owns ``<state> <layer>
<attr>`` and one several states share ``<layer> <attr>``.

Nothing here imports ``aure``; a ``final_state.json`` is just JSON.
"""

from __future__ import annotations

import copy
import json
import keyword
import re
import unicodedata
from pathlib import Path
from typing import Any

#: Which model in a finished run is *the* answer. AuRE's ``finalize`` node
#: writes the selected iteration's values into ``current_model``, and an
#: adopted final MCMC polish overwrites it again -- upstream's own rule is that
#: this key "must track the model actually reported". ``best_model`` is the
#: fallback for a run that never reached finalize.
_MODEL_KEYS = ("current_model", "best_model")


class ImportError_(Exception):
    """Raised when a run directory does not hold a usable fitted model."""


def read_final_state(output_dir: Path) -> dict[str, Any]:
    """Load ``final_state.json`` from an AuRE output directory.

    Args:
        output_dir: The directory passed to ``aure analyze -o``.

    Returns:
        The parsed document.

    Raises:
        ImportError_: If the file is absent or unreadable. An absent file
            usually means the run was interrupted before ``finalize``; the
            checkpoints are still there, and ``aure resume`` is the way back.
    """
    path = Path(output_dir) / "final_state.json"
    if not path.is_file():
        raise ImportError_(
            f"{path} does not exist. An AuRE run writes it when it finishes, so "
            "this run was interrupted or failed. Check "
            f"{Path(output_dir) / 'checkpoints'} and resume it, or run it again."
        )
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ImportError_(f"{path} could not be read: {exc}") from exc


def fitted_model(final_state: dict[str, Any]) -> dict[str, Any]:
    """Return the ModelDefinition the run actually reported.

    Args:
        final_state: A parsed ``final_state.json``.

    Returns:
        The model definition.

    Raises:
        ImportError_: If the run reported no model, or errored.
    """
    state = final_state.get("state") or {}
    if final_state.get("error"):
        raise ImportError_(
            f"The AuRE run failed: {final_state['error']}. There is no model to import."
        )

    for key in _MODEL_KEYS:
        model = state.get(key)
        # A legacy run could store a model as a Python script string; only the
        # structured form can be translated.
        if isinstance(model, dict) and model.get("layers") is not None:
            return _checked(model)

    raise ImportError_(
        "The run holds no structured model -- neither `current_model` nor "
        "`best_model` is a layer definition. Nothing to translate."
    )


#: More layers than any reflectometry stack has. A larger number means the file
#: is not what we think it is, and refusing beats emitting a spec nobody can read.
MAX_LAYERS = 64


def _checked(model: dict[str, Any]) -> dict[str, Any]:
    """Reject a model we would have to guess at, naming what is wrong.

    Every default this avoids would be a *plausible* number -- an SLD of 0.0 is
    vacuum, and it is legal, so neither the schema nor a reader would question
    it. On a back-reflection sample the ambient is the incident medium, so a
    D2O ambient that failed to parse becomes vacuum and every later fit refines
    a model that is wrong at the boundary. That is the silent-wrong-answer case
    this project ranks above all others, so nothing here is defaulted.

    Raises:
        ImportError_: If a required part is absent or the wrong shape.
    """
    layers = model.get("layers")
    if not isinstance(layers, list) or not layers:
        raise ImportError_("The model declares no layers.")
    if len(layers) > MAX_LAYERS:
        raise ImportError_(
            f"The model declares {len(layers)} layers, more than the {MAX_LAYERS} "
            "this can be is a real stack. Check the run directory is what you think."
        )

    for label, part in (
        ("substrate", model.get("substrate")),
        ("ambient", model.get("ambient")),
    ):
        if not isinstance(part, dict):
            raise ImportError_(f"The model's `{label}` is missing or not a mapping.")
        if part.get("sld") is None:
            raise ImportError_(
                f"The model's {label} ({part.get('name', '?')}) has no SLD. It "
                "cannot be defaulted -- an SLD of 0 is vacuum, which is a legal "
                "value nothing downstream would question."
            )

    # In front reflection the substrate is the first slab and carries its own
    # roughness; in back reflection it is semi-infinite and does not. Required
    # either way, because which geometry we are in is the model's to say and a
    # missing value would only fail in one of them.
    if model.get("substrate", {}).get("roughness") is None:
        raise ImportError_("The model's substrate has no roughness.")

    for index, layer in enumerate(layers):
        if not isinstance(layer, dict):
            raise ImportError_(f"Layer {index} is not a mapping.")
        for field_name in ("sld", "thickness", "roughness"):
            if layer.get(field_name) is None:
                raise ImportError_(
                    f"Layer {layer.get('name', index)!r} has no {field_name}. "
                    "A fitted model always records one, so this run's shape is "
                    "not what this expects -- translating it would invent a number."
                )

    # `interfaces` OVERRIDES the positional roughness map that `ordered_stack`
    # reproduces (aure/nodes/model_builder.py, `_apply_interface_declarations`).
    # Honouring the positional map anyway would put every buried interface one
    # place off, and the spec would still validate and fit.
    if model.get("interfaces"):
        raise ImportError_(
            "This AuRE run declared an `interfaces` block, which names "
            "boundaries by the materials they separate and overrides the "
            "per-layer roughness map this translation relies on. Importing it "
            "positionally would move every buried interface one place and still "
            "produce a spec that validates. Write the stack by hand instead: "
            "`nrw model new <sample> --name <name> --print-prompt`."
        )

    return model


# --------------------------------------------------------------------------
# One state of a co-refinement
# --------------------------------------------------------------------------

#: AuRE's name in a fit for each block key that can be held or fitted.
_FITTED_AS = {"thickness": "thickness", "sld": "rho", "roughness": "interface"}

#: The AuRE commit that names a co-refinement's state-only layers and writes
#: their fitted values back, as ``git describe`` counts it: v1.0.2 plus 31.
#: A multi-state run from before it cannot be imported state by state -- a
#: layer absent from the first state was neither tied nor renamed, so its
#: states' parameters shared one name, and its fitted values never reached
#: the states' own stacks.
STATE_NAMING_SINCE = ((1, 0, 2), 31)

#: That commit, in full, as an install records it.
STATE_NAMING_COMMIT = "94710c8e6667c53febc582e5fc87a895c7338c54"

#: What `nrw aure run` writes into the output directory: the AuRE commit the
#: run used and the one nrw pins. AuRE itself records ``git describe`` only
#: when it runs from a checkout, and an install from the pin reports version
#: 1.0.2, which both sides of 94710c8 report.
AURE_COMMIT_FILE = "nrw-aure.json"

_DESCRIBE_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)(?:-(\d+)-g[0-9a-f]+)?(?:-dirty)?$")
_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)")


def aure_states(model: dict[str, Any]) -> list[dict[str, Any]]:
    """The states a model carries, as AuRE recorded them on it."""
    return [s for s in model.get("states") or [] if isinstance(s, dict)]


def state_holding(model: dict[str, Any], files: set[str]) -> str | None:
    """The AuRE state that was fitted to *files*.

    Args:
        model: A ModelDefinition.
        files: Basenames of the run being imported.

    Returns:
        The state's name, or ``None`` for a model with fewer than two states,
        which has no state to choose.

    Raises:
        ImportError_: If several states are co-refined and none, or more than
            one, holds the run's files.
    """
    states = aure_states(model)
    if len(states) < 2:
        return None
    holding = [
        str(state.get("name"))
        for state in states
        if files
        & {
            Path(str(entry.get("file", ""))).name
            for entry in state.get("data_files") or []
            if isinstance(entry, dict)
        }
    ]
    if len(holding) == 1:
        return holding[0]
    names = ", ".join(repr(str(state.get("name"))) for state in states)
    if not holding:
        raise ImportError_(
            f"AuRE co-refined {len(states)} states ({names}), and none of them "
            "holds this run's files. Pass the --run of one of its states."
        )
    raise ImportError_(
        f"States {', '.join(map(repr, holding))} of this AuRE run all hold this "
        "run's files, so which one to import is not decidable. Import a run "
        "only one of them holds."
    )


def state_model(
    model: dict[str, Any], state: str | None, fit: dict[str, Any] | None = None
) -> dict[str, Any]:
    """The model as one of its states was built, with no states of its own.

    What AuRE's builder does for a state: its own ``layers`` replace the
    template's, its ``substrate`` the template's, its ``ambient`` merges into
    the template's, and its ``back_reflection``, ``intensity`` and nuisance
    blocks replace the model's.

    A parameter AuRE ties across states and holds in any one of them it holds
    in all, at that value -- so a value this state inherits held from another
    is marked held here too, at the holder's value.

    Args:
        model: A ModelDefinition.
        state: The state's name; ``None`` gives the model as it is.
        fit: The reported fit (:func:`reported_fit`), whose state-prefixed
            names say which of the state's parameters were its own.

    Returns:
        A ModelDefinition for that state alone.

    Raises:
        ImportError_: If the state's own structure is incomplete.
    """
    if state is None:
        return model
    states = aure_states(model)
    own = next((s for s in states if s.get("name") == state), None)
    if own is None:
        raise ImportError_(f"The model has no state named {state!r}.")

    view = _effective(model, own)
    view.pop("states", None)

    fitted = (fit or {}).get("parameters") or {}
    others = [_effective(model, s) for s in states if s is not own]
    for block, kind in [
        *((layer, "layer") for layer in view.get("layers") or []),
        (view.get("substrate") or {}, "substrate"),
    ]:
        name = block.get("name")
        for key, fitted_as in _FITTED_AS.items():
            if kind == "substrate" and key != "roughness":
                continue
            if is_held(block, key) or f"{state} {name} {fitted_as}" in fitted:
                continue
            if not _tied(model, name, key, substrate=kind == "substrate"):
                continue
            holder = next(
                (
                    other_block
                    for other in others
                    for other_block in _blocks(other, kind)
                    if other_block.get("name") == name and is_held(other_block, key)
                ),
                None,
            )
            if holder is not None and holder.get(key) is not None:
                block[key] = holder[key]
                block[f"{key}_fixed"] = True
    return _checked(view)


def _effective(model: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    """*model* with *state*'s overrides applied, as AuRE's builder applies them."""
    view = copy.deepcopy(model)
    if state.get("ambient"):
        merged = dict(view.get("ambient") or {})
        override = dict(state["ambient"])
        if "rho" in override and "sld" not in override:
            override["sld"] = override.pop("rho")
        merged.update(override)
        view["ambient"] = merged
    if "back_reflection" in state:
        view["back_reflection"] = bool(state["back_reflection"])
    if state.get("layers") is not None:
        view["layers"] = copy.deepcopy(state["layers"])
    for key in (
        "substrate",
        "intensity",
        "sample_broadening",
        "theta_offset",
        "background",
    ):
        if state.get(key):
            view[key] = copy.deepcopy(state[key])
    return view


def _blocks(model: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    """A model's layer blocks, or its substrate as the only one."""
    if kind == "substrate":
        return [model.get("substrate") or {}]
    return [layer for layer in model.get("layers") or [] if isinstance(layer, dict)]


def is_held(block: dict[str, Any], key: str) -> bool:
    """Whether AuRE holds *key* (``sld``, ``thickness``, ``roughness``) of a block.

    ``<key>_fixed: true`` says so, and so does a window that is one point,
    ``<key>_min == <key>_max``. Either way AuRE sets the value and does not fit
    it.
    """
    if block.get(f"{key}_fixed"):
        return True
    low, high = block.get(f"{key}_min"), block.get(f"{key}_max")
    return low is not None and high is not None and low == high


#: AuRE's attribute for each block key in a tie spec, and its other spellings.
_TIE_ATTR = {"thickness": "thickness", "sld": "material.rho", "roughness": "interface"}
_TIE_ALIASES = {"rho": "material.rho", "sld": "material.rho"}


def _tied(model: dict[str, Any], name: str, key: str, *, substrate: bool) -> bool:
    """Whether AuRE ties *key* of the layer *name* across states.

    By default every layer's thickness, SLD and interface is tied, and the
    substrate's interface. ``shared_parameters`` replaces that set;
    ``unshared_parameters`` takes from it.
    """
    attr = _TIE_ATTR[key]
    sub_name = (model.get("substrate") or {}).get("name")
    amb_name = (model.get("ambient") or {}).get("name")

    def pairs(specs: list[Any]) -> set[tuple[str, str]]:
        found = set()
        for spec in specs:
            layer, _, spelled = str(spec).partition(".")
            if layer.lower() == "substrate":
                layer = sub_name or layer
            elif layer.lower() == "ambient" and amb_name:
                layer = amb_name
            found.add((layer, _TIE_ALIASES.get(spelled, spelled)))
        return found

    shared = model.get("shared_parameters") or []
    if shared:
        return (name, attr) in pairs(shared)
    default = attr == "interface" if substrate else True
    unshared = model.get("unshared_parameters") or []
    return default and (name, attr) not in pairs(unshared)


def state_fit(
    fit: dict[str, dict[str, Any]], model: dict[str, Any], state: str | None
) -> dict[str, dict[str, Any]]:
    """The reported fit as one state of a co-refinement sees it.

    Each name the import reads is looked up exactly, as AuRE's finalize does:
    ``<state> <tail>`` -- a parameter the state owns -- and then ``<tail>``, one
    the states share. The state's own value comes first, under the tail's
    name. Nothing is matched by prefix, so a state whose name begins another's
    (``ocv``, ``ocv 2``), or one named like a layer, reads only its own.

    What remains reads as a single state's fit: ``<layer> <attr>``,
    ``intensity <label>``, ``sample_broadening``. With one state every
    unprefixed name is that state's, and is kept; a name a run stored before
    AuRE stopped prefixing a single state's (``state0 Cu thickness``) reads
    as today's.

    Args:
        fit: The reported fit (:func:`reported_fit`).
        model: The ModelDefinition, for the states' structure and files.
        state: The state imported; ``None`` returns the fit unchanged.

    Returns:
        ``{"parameters": ..., "bounds": ...}`` as :func:`reported_fit` gives.
    """
    if state is None:
        return fit
    states = aure_states(model)
    own = next((s for s in states if s.get("name") == state), {})
    tails = _names_read(_effective(model, own), own)
    single = len(states) < 2

    def scoped(values: dict[str, Any]) -> dict[str, Any]:
        found = dict(values) if single else {}
        for key, tail in tails:
            mine = f"{state} {tail}"
            if mine in values:
                found.pop(mine, None)
                found[key] = values[mine]
            elif tail in values and key not in found:
                found[key] = values[tail]
        return found

    return {
        "parameters": scoped(fit.get("parameters") or {}),
        "bounds": scoped(fit.get("bounds") or {}),
    }


def _names_read(view: dict[str, Any], state: dict[str, Any]) -> list[tuple[str, str]]:
    """Every fitted name the import reads for one state, as ``(key, tail)``.

    *key* is the name as one state spells it; *tail* is what follows the
    state's name in a co-refinement. They differ only for a per-file
    intensity, which a co-refinement spells ``<state> <label> intensity`` and
    one state ``intensity <label>`` -- the label being the file's own, or its
    file name without the extension.
    """
    blocks = [
        *(view.get("layers") or []),
        view.get("substrate") or {},
        view.get("ambient") or {},
    ]
    found = [
        (f"{block['name']} {fitted_as}", f"{block['name']} {fitted_as}")
        for block in blocks
        if isinstance(block, dict) and block.get("name")
        for fitted_as in _FITTED_AS.values()
    ]
    labels = dict.fromkeys(
        str(label)
        for entry in state.get("data_files") or []
        if isinstance(entry, dict)
        for label in (entry.get("label"), Path(str(entry.get("file", ""))).stem)
        if label
    )
    for label in labels:
        found.append((f"intensity {label}", f"{label} intensity"))
        found.append((f"intensity {label}", f"intensity {label}"))
    found.append(("intensity", "intensity"))
    found += [(attribute, attribute) for attribute in _NUISANCES]
    return found


def _run_info(output_dir: Path) -> dict[str, Any]:
    """The run's ``run_info.json``, or an empty mapping."""
    try:
        info = json.loads((Path(output_dir) / "run_info.json").read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    return info if isinstance(info, dict) else {}


def state_naming(output_dir: Path) -> tuple[bool | None, str | None]:
    """Whether the AuRE a run used is 94710c8 or later, as far as the run says.

    Read, in order, from:

    1. :data:`AURE_COMMIT_FILE`: a run whose AuRE commit is 94710c8, or the
       commit nrw pinned when it ran, is at or after it. An nrw that writes
       the file pins 94710c8 or later.
    2. ``run_info.json``'s ``git describe``, which places any commit of a
       checkout.
    3. ``run_info.json``'s package version: below 1.0.2 is before, above it
       after; 1.0.2 itself cannot be placed.

    Args:
        output_dir: The AuRE output directory.

    Returns:
        ``(True, recorded)`` at or after 94710c8, ``(False, recorded)`` before
        it, and ``(None, recorded)`` when the run does not say; *recorded* is
        the commit or version it does record, or ``None``.
    """
    try:
        ran = json.loads((Path(output_dir) / AURE_COMMIT_FILE).read_text("utf-8"))
    except (OSError, ValueError):
        ran = {}
    ran = ran if isinstance(ran, dict) else {}
    commit = ran.get("aure_commit")
    if isinstance(commit, str) and commit in {
        STATE_NAMING_COMMIT,
        ran.get("pinned_commit"),
    }:
        return True, commit

    info = _run_info(output_dir)
    since, count = STATE_NAMING_SINCE
    described = info.get("git_describe")
    if isinstance(described, str):
        match = _DESCRIBE_RE.match(described.strip())
        if match is not None:
            release = tuple(int(part) for part in match.group(1, 2, 3))
            after = int(match.group(4) or 0)
            return (release, after) >= (since, count), described.strip()
    version = info.get("aure_version")
    if isinstance(version, str):
        match = _VERSION_RE.match(version.strip())
        if match is not None:
            release = tuple(int(part) for part in match.groups())
            if release != since:
                return release > since, version.strip()
    recorded = commit if isinstance(commit, str) else None
    return None, recorded or (version.strip() if isinstance(version, str) else None)


def untranslatable(
    model: dict[str, Any],
    fit: dict[str, dict[str, Any]] | None = None,
    *,
    state: str | None = None,
    others: list[str] | tuple[str, ...] = (),
) -> list[str]:
    """Return anything in the model this translation cannot carry across.

    AuRE's ``constraints`` are free-text expressions over its own parameter
    names; an nrw ``constraint`` is a functional form over a *series*. They are
    different concepts, so there is nothing to map -- but dropping them without
    a word would leave an unconstrained spec claiming the chi-squared of a
    constrained fit.

    Args:
        model: A ModelDefinition: one state's, from :func:`state_model`, for a
            co-refinement of several.
        fit: The fit behind it (:func:`reported_fit`, through
            :func:`state_fit`), when there is one.
        state: The AuRE state imported, for a co-refinement of several.
        others: The co-refinement's other states, none of which this spec
            carries.

    Returns:
        Human-readable descriptions, empty when everything was carried over.
    """
    notes: list[str] = [
        f"state {other!r} not carried over: AuRE co-refined it with {state!r}, "
        f"and this spec holds {state!r} alone"
        for other in others
    ]
    for expression in model.get("constraints") or []:
        notes.append(f"constraint not carried over: {expression}")
    for layer in model.get("layers") or []:
        if layer.get("roughness_tie"):
            notes.append(
                f"{layer.get('name', '?')}: roughness was tied to its own "
                "thickness in the fit; imported as a fixed value"
            )
    fitted, per_file = _fitted_intensities(fit or {})
    if per_file:
        values = (fit or {}).get("parameters") or {}
        shown = ", ".join(f"{values[name]:.4g}" for name in fitted)
        notes.append(
            "intensity: one per angle segment, as AuRE fitted it, but a spec "
            f"starts them all at one value; AuRE's were {shown}"
        )
    return notes


def reported_fit(final_state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The fit behind the reported model: its ``parameters`` and their ``bounds``.

    AuRE's ``finalize`` writes the selected fit's layer values into
    ``current_model`` but has no field there for the probe's: each segment's
    intensity, the sample broadening, a theta offset or a background -- "N
    fitted parameter(s) had no ModelDefinition field", its log says. Nor does
    ``current_model`` hold the ranges AuRE filled in from its own defaults,
    such as an interface's lower bound. The fit result records both, under
    AuRE's parameter names (``Cu interface``, ``intensity <file>``,
    ``sample_broadening``).

    Which fit that is follows AuRE's own rule (``aure.state.reported_fit``),
    kept here because this module reads the file without importing AuRE. The
    final MCMC polish, when it ran, was adopted, and is still the run's last
    fit -- a restarted run can append fits after it, and the polish then
    describes an earlier answer. Otherwise the fit ``final_selection``
    selected, or the last fit when it selected none.

    Args:
        final_state: A parsed ``final_state.json``.

    Returns:
        ``{"parameters": {name: value}, "bounds": {name: [low, high]}}``, empty
        mappings when the run recorded no fit.
    """
    state = final_state.get("state") or {}
    fits = [f for f in state.get("fit_results") or [] if isinstance(f, dict)]
    fit: dict[str, Any] = {}
    if fits:
        polish = state.get("final_fit")
        polish = polish if isinstance(polish, dict) else {}
        selection = state.get("final_selection")
        selection = selection if isinstance(selection, dict) else {}
        index = selection.get("index") if selection.get("selected") else None
        # A record written before AuRE stamped its polish is taken at its word.
        stamped = any(f.get("final_fit") for f in fits)
        polish_is_last = not stamped or bool(fits[-1].get("final_fit"))
        if polish.get("ran") and polish.get("adopted") and polish_is_last:
            fit = fits[-1]
        elif _is_index(index, len(fits)):
            fit = fits[index]
        else:
            fit = fits[-1]
    values = fit.get("parameters") if isinstance(fit.get("parameters"), dict) else {}
    bounds = fit.get("bounds") if isinstance(fit.get("bounds"), dict) else {}
    return {
        "parameters": {str(k): float(v) for k, v in values.items() if _is_number(v)},
        "bounds": {
            str(k): [float(v[0]), float(v[1])]
            for k, v in bounds.items()
            if isinstance(v, list | tuple)
            and len(v) == 2
            and _is_number(v[0])
            and _is_number(v[1])
            and float(v[0]) < float(v[1])
        },
    }


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _is_index(value: Any, length: int) -> bool:
    return (
        isinstance(value, int) and not isinstance(value, bool) and 0 <= value < length
    )


def reported_chisq(final_state: dict[str, Any]) -> float | None:
    """Return the chi-squared of the reported model, if the run recorded one."""
    value = final_state.get("final_chi2")
    if value is None:
        value = (final_state.get("state") or {}).get("best_chi2")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


#: What the header of a spec `nrw aure import` wrote says, first thing.
PROPOSED_MARKER = "was PROPOSED by AuRE"


def is_unedited_proposal(text: str) -> bool:
    """Whether a spec is one AuRE proposed, as the import wrote it.

    Such a spec is derived data -- the AuRE run it came from can write it again
    -- so a new quick fit of the model may replace it. One edited by hand, even
    in a comment, may not: its self-hash no longer matches.

    Args:
        text: The spec's contents.

    Returns:
        Whether it carries the import's header and its self-hash still holds.
    """
    from nr_workbench.codegen.generator import verify_self_hash

    return PROPOSED_MARKER in text[:4000] and verify_self_hash(text)


def ordered_stack(model: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the layers in refl1d order, incident medium last.

    See the module docstring: AuRE's ``layers`` run substrate-first, and the
    geometry decides which end the beam arrives at.

    Args:
        model: A ModelDefinition.

    Returns:
        Entries of ``{"name", "sld", "thickness", "roughness"}`` in stack
        order. The last entry is the semi-infinite incident medium and carries
        no thickness.
    """
    layers = list(model.get("layers") or [])
    substrate = dict(model.get("substrate") or {})
    ambient = dict(model.get("ambient") or {})
    back = bool(model.get("back_reflection"))

    def entry(source: dict[str, Any], fallback: str) -> dict[str, Any]:
        return {
            "name": str(source.get("name") or fallback),
            "sld": source.get("sld"),
            "thickness": source.get("thickness"),
            "roughness": source.get("roughness"),
        }

    body = [entry(layer, f"layer{i}") for i, layer in enumerate(layers)]
    substrate_entry = entry(substrate, "substrate")
    ambient_entry = entry(ambient, "ambient")

    if back:
        # The ambient slab is first and, upstream, borrows the outermost
        # layer's roughness -- the outer surface has none of its own.
        ambient_entry["roughness"] = (
            layers[-1].get("roughness") if layers else substrate.get("roughness")
        )
        ambient_entry["thickness"] = 0
        return [ambient_entry, *reversed(body), substrate_entry]

    substrate_entry["thickness"] = 0
    return [substrate_entry, *body, ambient_entry]


def setup_files(output_dir: Path) -> set[str]:
    """Return the basenames of the data files the run's setup named.

    Args:
        output_dir: The AuRE output directory.

    Returns:
        Basenames, or an empty set when there is no readable setup beside it.
    """
    setup_path = Path(output_dir).parent / "setup.yaml"
    if not setup_path.is_file():
        return set()

    try:
        import yaml

        document = yaml.safe_load(setup_path.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        return set()

    return {
        Path(str(entry.get("file", ""))).name
        for state in document.get("states") or []
        for entry in state.get("data_files") or []
        if entry.get("file")
    }


def runs_of(output_dir: Path, scan: Any) -> set[int]:
    """Every steady run of the scan that the run's setup named a file of.

    Args:
        output_dir: The AuRE output directory.
        scan: A :class:`~nr_workbench.project.scan.ScanResult`.

    Returns:
        The runs, empty when there is no setup to read or it names none of
        them.
    """
    fitted = setup_files(output_dir)
    if not fitted:
        return set()

    matches = set()
    for run, measurement in (getattr(scan, "steady", {}) or {}).items():
        if fitted & run_files(measurement):
            matches.add(run)
    return matches


def run_files(measurement: Any) -> set[str]:
    """The basenames of one steady run's files, as a scan found them."""
    known = {Path(p).name for p in (measurement.partials or {}).values()}
    if measurement.combined:
        known.add(Path(measurement.combined).name)
    return known


def run_of(output_dir: Path, scan: Any) -> int | None:
    """Recover which steady run a finished AuRE run was fitted to.

    ``nrw aure run`` leaves ``setup.yaml`` one level above its output, and that
    file names the exact data files AuRE was given. Matching them back against
    the scan is stronger than asking the user again: the model's layer
    thicknesses are only meaningful next to the angles of the files that
    produced them, and pairing them with a *different* run's angles would
    validate, fit, and be wrong with nothing saying so.

    Args:
        output_dir: The AuRE output directory.
        scan: A :class:`~nr_workbench.project.scan.ScanResult`.

    Returns:
        The run number, or ``None`` when there is no setup to read or its
        files match no single run -- in which case the caller should ask.
    """
    matches = runs_of(output_dir, scan)
    # Ambiguity is not a thing to resolve by picking one.
    return matches.pop() if len(matches) == 1 else None


#: Names a layer may not take in a spec: the probe's own, in parameter paths
#: (``probe.intensity``), and the one the generated script's stack builds with.
_TAKEN_NAMES = frozenset({"probe", "SLD"})


def spec_layer_name(text: str, fallback: str) -> str:
    """AuRE's name for a layer, as a layer name a spec takes.

    AuRE names layers the way a person would -- ``silicon oxide``, ``water-
    based solvent (unspecified contrast)`` -- and an nrw layer name is a key, a
    parameter path (``silicon_oxide.rho``) and a variable in the generated
    script. So accents are dropped and every run of anything but letters and
    digits becomes one underscore. A name that would start with a digit is
    prefixed with its position's name, one left empty *is* that name, and a
    Python keyword, or a name the spec or the script uses, is suffixed.

    Args:
        text: AuRE's name.
        fallback: The position's own name (``layer2``), for a name that leaves
            nothing usable.

    Returns:
        A Python identifier the spec's name rule accepts.
    """
    plain = (
        unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    )
    name = "_".join(re.findall(r"[A-Za-z0-9]+", plain))
    if not name:
        return fallback
    if name[0].isdigit():
        name = f"{fallback}_{name}"
    if keyword.iskeyword(name) or name in _TAKEN_NAMES:
        name = f"{name}_layer"
    return name


def _unique_names(stack: list[dict[str, Any]]) -> list[str]:
    """Return stack names as a spec takes them, de-duplicated in place.

    Each is made a spec's name first (:func:`spec_layer_name`). A stack
    legitimately repeats a material -- D2O above and below a membrane, the same
    oxide twice -- but an nrw layer name is a key, so a repeat would silently
    collapse two layers into one. Suffixing is the conservative fix; losing a
    layer is not recoverable from the written file.
    """
    used: set[str] = set()
    names: list[str] = []
    for position, entry in enumerate(stack):
        base = spec_layer_name(entry["name"], f"layer{position}")
        name, count = base, 1
        while name in used:  # "a" twice beside an "a2" of its own is "a3"
            count += 1
            name = f"{base}{count}"
        used.add(name)
        names.append(name)
    return names


def layer_names(model: dict[str, Any]) -> list[tuple[str, str]]:
    """Each layer's name as AuRE reported it, and as the spec names it.

    Args:
        model: A ModelDefinition.

    Returns:
        ``(aure_name, spec_name)`` in stack order, incident medium last.
    """
    stack = ordered_stack(model)
    return list(
        zip((entry["name"] for entry in stack), _unique_names(stack), strict=True)
    )


#: A spec attribute of a stack entry, the bounds ``ModelDefinition`` declares
#: for it, and AuRE's name for it in a fit (``Cu interface``).
_LAYER_BOUNDS = (
    ("thickness", "thickness_min", "thickness_max", "thickness"),
    ("rho", "sld_min", "sld_max", "rho"),
    ("roughness", "roughness_min", "roughness_max", "interface"),
)
#: Nuisance parameters AuRE ties across the probes of a state, by the probe
#: attribute each fits: one parameter per state, as AuRE fitted them.
_NUISANCES = ("sample_broadening", "theta_offset", "background")


def _free_parameters(
    names: list[str],
    model: dict[str, Any],
    fit: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Turn the bounds AuRE fitted with into nrw parameter declarations.

    Only bounds AuRE actually recorded become free parameters. Inventing a
    range around a fitted value would turn its answer into our assumption, and
    a range nobody chose is exactly the kind of number that gets quoted later
    as though it meant something. The bounds ``ModelDefinition`` declares come
    first; the reported fit (:func:`reported_fit`) has the rest -- a range AuRE
    took from its own defaults, an interface it fitted on the substrate, and
    the probe's parameters.

    A value AuRE held (:func:`is_held`) is no free parameter: it stays at
    the stack's value, as AuRE fitted around it.

    Args:
        names: The spec's names for the stack, in stack order.
        model: A ModelDefinition.
        fit: The reported fit, when the run recorded one.
    """
    fit = fit or {}
    bounds = fit.get("bounds") or {}
    stack = ordered_stack(model)
    aure_names = [str(entry["name"]) for entry in stack]
    layers = list(model.get("layers") or [])
    back = bool(model.get("back_reflection"))

    # Each stack position back to the layer it came from, so a bound lands on
    # the right name after the reversal and the de-duplication. Both
    # geometries put a medium first and the incident medium last.
    order = (
        list(reversed(range(len(layers))))
        if model.get("back_reflection")
        else list(range(len(layers)))
    )
    layer_at = {position + 1: layers[index] for position, index in enumerate(order)}

    parameters: list[dict[str, Any]] = []
    for position, name in enumerate(names):
        layer = layer_at.get(position)
        incident = position == len(names) - 1
        # A fit names a parameter by its layer's name, so a name the stack
        # repeats does not say which layer it was.
        unique = aure_names.count(aure_names[position]) == 1
        for attribute, low, high, fitted_as in _LAYER_BOUNDS:
            # The incident medium is semi-infinite, and a medium's thickness is
            # no parameter: only their SLD and the first medium's interface.
            if attribute != "rho" and (
                incident or (attribute == "thickness" and not layer)
            ):
                continue
            # A tied roughness is a derived parameter upstream (sigma =
            # fraction x thickness), not a range, so it cannot be expressed as
            # one. Leave the interface fixed rather than inventing a range
            # around it; the caller reports the layer so the omission is visible.
            if attribute == "roughness" and layer and layer.get("roughness_tie"):
                continue
            if layer and _held_in_layer(model, layer, low.removesuffix("_min"), back):
                continue
            lo, hi = (layer.get(low), layer.get(high)) if layer else (None, None)
            recorded = (
                bounds.get(f"{aure_names[position]} {fitted_as}") if unique else None
            )
            if lo is not None and hi is not None:
                span = _declared_span(lo, hi)
                if span is None:
                    continue
            elif recorded is not None:
                span = list(recorded)
            else:
                continue
            parameters.append(
                {"path": f"{name}.{attribute}", "range": span, "per": "model"}
            )
    return parameters + _probe_parameters(model, fit)


def _held_in_layer(
    model: dict[str, Any], layer: dict[str, Any], key: str, back: bool
) -> bool:
    """Whether AuRE holds *key* of one of the model's layers.

    Its own ``<key>_fixed`` (or a one-point window) holds it. So, in back
    reflection, does the substrate's held roughness for the layer touching
    the substrate: that layer's interface is the one carrying the substrate
    boundary there, and AuRE holds it in its place. A medium needs no such
    check -- AuRE records no bounds for a value it held, and a medium's bounds
    come only from the fit.
    """
    if is_held(layer, key):
        return True
    layers = model.get("layers") or []
    return (
        back
        and key == "roughness"
        and bool(layers)
        and layer is layers[0]
        and is_held(model.get("substrate") or {}, "roughness")
    )


def _declared_span(lo: Any, hi: Any) -> list[float] | None:
    """A range a model declares, as AuRE builds it; ``None`` when it fits nothing.

    AuRE swaps an inverted pair, and fits a parameter whose bounds are equal
    nowhere but at that value: it is held there, as the stack has it, not a
    range a spec could fit in ("SiO2.rho: range (3.47, 3.47) is empty").
    """
    if lo is None or hi is None:
        return None
    span = sorted([float(lo), float(hi)])
    return span if span[0] < span[1] else None


def _fitted_intensities(fit: dict[str, dict[str, Any]]) -> tuple[list[str], bool]:
    """AuRE's names of the intensities a fit refined, and whether one is per file.

    ``intensity <file>`` in a single state's co-refinement, ``<state> <file>
    intensity`` across states: one per file. ``<state> intensity``: one per
    state. File labels and state names hold no spaces.
    """
    names = sorted(
        name
        for name in fit.get("parameters") or {}
        if name == "intensity"
        or name.startswith("intensity ")
        or name.endswith(" intensity")
    )
    per_file = [n for n in names if n.startswith("intensity ") or len(n.split()) >= 3]
    return names, len(per_file) > 1


def _fitted_as(fit: dict[str, dict[str, Any]], attribute: str) -> str | None:
    """AuRE's name, in a fit, of a probe attribute tied across a state's probes."""
    names = [
        name
        for name in fit.get("parameters") or {}
        if name == attribute or name.endswith(f" {attribute}")
    ]
    return names[0] if len(names) == 1 else None


def _probe_parameters(
    model: dict[str, Any], fit: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """The probe's free parameters, as AuRE fitted them.

    A state's angle segments, each reduced on its own, each get an intensity
    when AuRE fitted one per file: the scale between segments is a fitted
    quantity, and one shared value cannot express a 3.4 degree segment that
    needs 37% more than the 0.45 degree one. A spec gives a parameter one
    starting value, so the segments start at the median of AuRE's values,
    within the bounds AuRE fitted them in. Sample broadening, theta offset and
    background are one parameter per state, from AuRE's fitted value.
    """
    values = fit.get("parameters") or {}
    bounds = fit.get("bounds") or {}
    parameters: list[dict[str, Any]] = []

    intensity = model.get("intensity") or {}
    fitted, per_file = _fitted_intensities(fit)
    if fitted:
        spans = [bounds[name] for name in fitted if name in bounds]
        declared = _declared_span(intensity.get("min"), intensity.get("max"))
        middle = sorted(values[name] for name in fitted)[len(fitted) // 2]
        entry: dict[str, Any] = {
            "path": "probe.intensity",
            "value": middle,
            "per": "measurement" if per_file else "state",
        }
        if spans:
            entry["range"] = [min(s[0] for s in spans), max(s[1] for s in spans)]
        elif declared is not None:
            entry["range"] = declared
        else:
            entry["pm"] = 0.1
        parameters.append(entry)
    elif not intensity.get("fixed", False):
        # Intensity is per state because each reduction used its own direct beam.
        parameters.append(
            {
                "path": "probe.intensity",
                "value": float(intensity.get("value", 1.0)),
                "pm": 0.1,
                "per": "state",
            }
        )

    for attribute in _NUISANCES:
        fitted_name = _fitted_as(fit, attribute)
        block = model.get(attribute) if isinstance(model.get(attribute), dict) else {}
        span = bounds.get(fitted_name) if fitted_name else None
        # Enabled with no fit to say where it ended: the range AuRE declared.
        if span is None and block.get("enabled"):
            span = _declared_span(block.get("min"), block.get("max"))
        if span is None:
            continue
        entry = {"path": f"probe.{attribute}", "range": list(span), "per": "state"}
        if fitted_name:
            entry["value"] = values[fitted_name]
        parameters.append(entry)
    return parameters


def _with_fitted_values(
    stack: list[dict[str, Any]], fit: dict[str, dict[str, Any]] | None
) -> list[dict[str, Any]]:
    """The stack with each value the reported fit has for it.

    The fit names an entry's values ``<name> thickness``, ``<name> rho`` and
    ``<name> interface`` -- the interface being the entry's roughness, the
    boundary refl1d gives the slab. A value the fit does not name keeps the
    stored one. Neither medium has a thickness, and the incident medium no
    roughness; a name the stack repeats does not say which entry it was.

    Args:
        stack: Entries from :func:`ordered_stack`.
        fit: The reported fit as the imported state sees it (:func:`state_fit`).

    Returns:
        The entries, updated in place.
    """
    values = (fit or {}).get("parameters") or {}
    names = [str(entry["name"]) for entry in stack]
    for position, entry in enumerate(stack):
        if names.count(names[position]) != 1:
            continue
        incident = position == len(stack) - 1
        for key, fitted_as in _FITTED_AS.items():
            if key == "thickness" and (position == 0 or incident):
                continue
            if key == "roughness" and incident:
                continue
            value = values.get(f"{names[position]} {fitted_as}")
            if value is not None:
                entry[key] = float(value)
    return stack


def to_spec(
    *,
    model: dict[str, Any],
    sample: str,
    name: str,
    states: list[dict[str, Any]],
    chisq: float | None = None,
    fit: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build an nrw model spec from AuRE's fitted model.

    Args:
        model: The ModelDefinition the run reported.
        sample: Sample identifier.
        name: Model name; also the spec's filename.
        states: ``states`` blocks, as ``nrw model new`` writes them. Required:
            the schema rejects a spec with neither a state nor a series, and
            those blocks carry the measured incident angles, which AuRE's
            output does not report back in a form we could reuse.
        chisq: The chi-squared AuRE reported, for the description.
        fit: The fit behind the model (:func:`reported_fit`): what the model
            has no field for, so that the spec fits what AuRE fitted.

    Returns:
        The spec mapping, ready for ``_emit_spec``.

    Raises:
        ImportError_: If ``states`` is empty.
    """
    if not states:
        raise ImportError_(
            "A model spec needs at least one state. Pass the state block for "
            "the run AuRE fitted -- `commands.model.state_for_run` builds it "
            "from the files on disk, leaving each angle to its file's header."
        )
    stack = _with_fitted_values(ordered_stack(model), fit)
    names = _unique_names(stack)

    materials: dict[str, Any] = {}
    stack_entries: list[dict[str, Any]] = []
    for position, (entry, layer_name) in enumerate(zip(stack, names, strict=True)):
        materials[layer_name] = {"rho": float(entry["sld"])}
        block: dict[str, Any] = {"name": layer_name, "material": layer_name}
        # The last entry is the incident medium and is semi-infinite: refl1d
        # takes no thickness for it, and the generator emits it bare.
        if position < len(stack) - 1:
            block["thickness"] = float(entry["thickness"])
            block["roughness"] = float(entry["roughness"])
        stack_entries.append(block)

    geometry = (
        "through the substrate"
        if model.get("back_reflection")
        else "through the ambient medium"
    )
    quality = f" chi-squared {chisq:.3g}." if chisq is not None else ""
    description = (
        f"Proposed by AuRE for {sample}, measured {geometry}.{quality} "
        "Starting point, not a measurement -- check every layer and range.\n"
    )

    document: dict[str, Any] = {
        "schema": "nrw-model/1",
        "name": name,
        "sample": sample,
        "description": description,
        "materials": materials,
        "stack": stack_entries,
        "probe": {
            "resolution": "angular_only",
            "dq_is_fwhm": bool(model.get("dq_is_fwhm", True)),
            "back_reflection": bool(model.get("back_reflection", False)),
        },
    }
    document["states"] = states
    document["parameters"] = _free_parameters(names, model, fit)
    return document
