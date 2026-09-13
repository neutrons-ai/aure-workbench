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

Nothing here imports ``aure``; a ``final_state.json`` is just JSON.
"""

from __future__ import annotations

import json
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


def untranslatable(model: dict[str, Any]) -> list[str]:
    """Return anything in the model this translation cannot carry across.

    AuRE's ``constraints`` are free-text expressions over its own parameter
    names; an nrw ``constraint`` is a functional form over a *series*. They are
    different concepts, so there is nothing to map -- but dropping them without
    a word would leave an unconstrained spec claiming the chi-squared of a
    constrained fit.

    Args:
        model: A ModelDefinition.

    Returns:
        Human-readable descriptions, empty when everything was carried over.
    """
    notes: list[str] = []
    for expression in model.get("constraints") or []:
        notes.append(f"constraint not carried over: {expression}")
    for layer in model.get("layers") or []:
        if layer.get("roughness_tie"):
            notes.append(
                f"{layer.get('name', '?')}: roughness was tied to its own "
                "thickness in the fit; imported as a fixed value"
            )
    return notes


def reported_chisq(final_state: dict[str, Any]) -> float | None:
    """Return the chi-squared of the reported model, if the run recorded one."""
    value = final_state.get("final_chi2")
    if value is None:
        value = (final_state.get("state") or {}).get("best_chi2")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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
    fitted = setup_files(output_dir)
    if not fitted:
        return None

    matches = set()
    for run, measurement in (getattr(scan, "steady", {}) or {}).items():
        known = {Path(p).name for p in (measurement.partials or {}).values()}
        if measurement.combined:
            known.add(Path(measurement.combined).name)
        if fitted & known:
            matches.add(run)

    # Ambiguity is not a thing to resolve by picking one.
    return matches.pop() if len(matches) == 1 else None


def _unique_names(stack: list[dict[str, Any]]) -> list[str]:
    """Return stack names, de-duplicated in place.

    A stack legitimately repeats a material -- D2O above and below a membrane,
    the same oxide twice -- but an nrw layer name is a key, so a repeat would
    silently collapse two layers into one. Suffixing is the conservative fix;
    losing a layer is not recoverable from the written file.
    """
    seen: dict[str, int] = {}
    names: list[str] = []
    for entry in stack:
        base = entry["name"]
        count = seen.get(base, 0)
        seen[base] = count + 1
        names.append(base if count == 0 else f"{base}{count + 1}")
    return names


def _free_parameters(names: list[str], model: dict[str, Any]) -> list[dict[str, Any]]:
    """Turn AuRE's per-layer bounds into nrw parameter declarations.

    Only bounds AuRE actually recorded become free parameters. Inventing a
    range around a fitted value would turn its answer into our assumption, and
    a range nobody chose is exactly the kind of number that gets quoted later
    as though it meant something.
    """
    layers = list(model.get("layers") or [])
    back = bool(model.get("back_reflection"))

    # Walk the stack back to the layer it came from, so a bound lands on the
    # right name after the reversal and the de-duplication above.
    order = list(reversed(range(len(layers)))) if back else list(range(len(layers)))
    offset = 1  # both geometries put a medium first

    parameters: list[dict[str, Any]] = []
    for position, layer_index in enumerate(order):
        layer = layers[layer_index]
        name = names[position + offset]
        # A tied roughness is a derived parameter upstream (sigma = fraction x
        # thickness), not a range, so it cannot be expressed as one. Leave the
        # interface fixed rather than inventing a range around it; the caller
        # reports the layer so the omission is visible.
        tied = bool(layer.get("roughness_tie"))
        for attribute, low, high in (
            ("thickness", "thickness_min", "thickness_max"),
            ("rho", "sld_min", "sld_max"),
            ("roughness", "roughness_min", "roughness_max"),
        ):
            if attribute == "roughness" and tied:
                continue
            lo, hi = layer.get(low), layer.get(high)
            if lo is None or hi is None:
                continue
            parameters.append(
                {
                    "path": f"{name}.{attribute}",
                    "range": [float(lo), float(hi)],
                    "per": "model",
                }
            )

    # Intensity is per state because each reduction used its own direct beam.
    intensity = model.get("intensity") or {}
    if not intensity.get("fixed", False):
        parameters.append(
            {
                "path": "probe.intensity",
                "value": float(intensity.get("value", 1.0)),
                "pm": 0.1,
                "per": "state",
            }
        )
    return parameters


def to_spec(
    *,
    model: dict[str, Any],
    sample: str,
    name: str,
    states: list[dict[str, Any]],
    chisq: float | None = None,
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

    Returns:
        The spec mapping, ready for ``_emit_spec``.

    Raises:
        ImportError_: If ``states`` is empty.
    """
    if not states:
        raise ImportError_(
            "A model spec needs at least one state. Pass the state block for "
            "the run AuRE fitted -- `commands.model.state_for_run` builds it "
            "from the files on disk, with the angles read from their headers."
        )
    stack = ordered_stack(model)
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
    document["parameters"] = _free_parameters(names, model)
    document["fit"] = {"method": "amoeba", "steps": 1000}
    return document
