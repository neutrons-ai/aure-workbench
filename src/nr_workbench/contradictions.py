"""Where a spec disagrees with what the data already said.

Four skills carry the same Red Flag in four wordings --- *"a form contradicting
`amplitude.trajectory`"*, *"a freed parameter contradicting
`template.implied_change`"*, *"freeing an SLD when `template.implied_change`
says `thickness`, or vice versa"*, *"choosing a constraint form that
contradicts `template.implied_change`"*. Both sides are machine-readable: the
assessment writes those fields, the spec declares the form and the paths. Until
now nothing compared them.

The same shape recurs elsewhere. Roughness larger than half the layer it bounds
is stated in four skills and checked in none. A fitted SLD outside the range of
anything the sample could be made of is stated in four and checked in none.
Counting the restatements, roughly a dozen distinct rules across the skill set
are the same handful of comparisons written out again for a different context.

Two design rules, both learned from the reconciler:

**Bound it, do not check it.** Roughness coherence is tested on the *declared
ranges*, not the fitted values. The supplementary material for the real
analysis puts it exactly: *"A range permitting sigma > t/4 guarantees the fit
can go there, and it will."* Catching it after the fit is catching it too late.

**Silence on absence.** A missing assessment, an unstated material, a
constraint on a path with no declared range --- none of these is a
contradiction. A checker that fires on incompleteness fires on every project
mid-beamtime, and then nobody reads it.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from typing import Any

#: Roughness may not exceed this fraction of the thinnest layer it bounds.
#: Half is the conventional rule and the one four skills state; the real
#: analysis derived a stricter sigma_top + sigma_bot <= t and found the
#: conventional form still admitted a layer that vanished into its own
#: interfaces, so both are checked and the pair is the stronger of the two.
ROUGHNESS_FRACTION = 0.5

#: How far outside a material's plausible SLD range a value may sit before it
#: is worth mentioning, in 1e-6 A^-2. Reduction and fitting both wander a
#: little; a real mismatch is a whole unit or more.
SLD_SLACK = 0.15

#: Which constraint forms assert what about a trajectory's shape.
_FORM_SHAPE = {
    "linear_in_time": "monotonic",
    "linear_in_index": "monotonic",
    "exponential": "monotonic",
    "logistic": "sigmoidal",
}

#: Which parameter attribute each implied change corresponds to.
_IMPLIED_ATTRIBUTE = {"thickness": "thickness", "sld_contrast": "rho"}


@dataclass
class Contradiction:
    """One disagreement between a spec and the evidence.

    Attributes:
        kind: A short slug.
        severity: ``info``, ``warn``, or ``blocker``.
        message: What disagrees, in a sentence.
        subject: The path, layer or form it concerns.
        evidence: Where the other side of the disagreement came from.
    """

    kind: str
    severity: str
    message: str
    subject: str | None = None
    evidence: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return the JSON form."""
        return {
            "kind": self.kind,
            "severity": self.severity,
            "subject": self.subject,
            "message": self.message,
            "evidence": self.evidence,
        }


@dataclass
class Report:
    """Everything the comparison found.

    Attributes:
        spec: The spec checked.
        contradictions: What disagrees.
    """

    spec: str
    contradictions: list[Contradiction] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """Return the JSON form."""
        return {
            "schema": "nrw-contradictions/1",
            "spec": self.spec,
            "contradictions": [c.as_dict() for c in self.contradictions],
        }

    @property
    def worst(self) -> str:
        """The highest severity present, or ``ok``."""
        for level in ("blocker", "warn"):
            if any(c.severity == level for c in self.contradictions):
                return level
        return "ok"


def check(
    spec: Any,
    *,
    name: str = "",
    tnr: dict[str, Any] | None = None,
    fitted: dict[str, float] | None = None,
) -> Report:
    """Compare a spec against the assessment and, when present, its results.

    Args:
        spec: A validated :class:`ModelSpec`.
        name: What to call it in the report.
        tnr: An ``nrw-tnr-assessment/1`` payload, if one exists.
        fitted: Best-fit values by refl1d parameter name, if the fit has run.

    Returns:
        The contradictions found.
    """
    report = Report(spec=name or getattr(spec, "name", "") or "spec")
    report.contradictions.extend(_form_against_trajectory(spec, tnr))
    report.contradictions.extend(_paths_against_implied_change(spec, tnr))
    report.contradictions.extend(_constraint_on_a_flat_run(spec, tnr))
    report.contradictions.extend(_roughness_coherence(spec))
    report.contradictions.extend(_sld_within_material(spec, fitted))
    return report


def _constraints(spec: Any) -> list[Any]:
    """The spec's constraints, or an empty list."""
    return list(getattr(spec, "constraints", None) or [])


def _amplitude(tnr: dict[str, Any] | None) -> dict[str, Any]:
    """The assessment's amplitude block, or an empty mapping."""
    return (tnr or {}).get("amplitude") or {}


def _form_against_trajectory(
    spec: Any, tnr: dict[str, Any] | None
) -> list[Contradiction]:
    """A constraint form that asserts a shape the data does not have."""
    trajectory = _amplitude(tnr).get("trajectory")
    if trajectory not in {"monotonic", "sigmoidal", "non-monotonic"}:
        return []

    found = []
    for constraint in _constraints(spec):
        form = getattr(constraint, "form", None)
        shape = _FORM_SHAPE.get(str(form))
        if shape is None:
            continue
        if trajectory == "non-monotonic":
            found.append(
                Contradiction(
                    kind="form-contradicts-trajectory",
                    severity="warn",
                    subject=str(form),
                    message=(
                        f"The constraint uses {form}, which forces a "
                        f"{shape} trajectory, but the assessment read a(t) as "
                        "non-monotonic. A single monotonic form cannot "
                        "describe a change that reverses."
                    ),
                    evidence="amplitude.trajectory = non-monotonic",
                )
            )
        elif shape != trajectory:
            found.append(
                Contradiction(
                    kind="form-contradicts-trajectory",
                    severity="warn",
                    subject=str(form),
                    message=(
                        f"The constraint uses {form}, which asserts a {shape} "
                        f"trajectory, but the assessment read a(t) as "
                        f"{trajectory}."
                    ),
                    evidence=f"amplitude.trajectory = {trajectory}",
                )
            )
    return found


def _paths_against_implied_change(
    spec: Any, tnr: dict[str, Any] | None
) -> list[Contradiction]:
    """Varying an SLD when the template says thickness, or the reverse.

    Judged over the whole spec, not per constraint. A spec routinely carries
    one constraint per quantity --- the real promoted tNR fit has
    ``linear_in_time`` on ``Cu.thickness`` and another on ``CuOx.rho`` --- and
    checking them separately flags the rho one for ignoring a template that
    the thickness one honours. The question is whether the implied change is
    varied *anywhere*, not whether every constraint varies it.
    """
    template = (tnr or {}).get("template") or {}
    implied = template.get("implied_change")
    wanted = _IMPLIED_ATTRIBUTE.get(str(implied))
    if wanted is None:
        return []

    constraints = _constraints(spec)
    if not constraints:
        return []

    attributes = {
        str(path).rsplit(".", 1)[-1]
        for constraint in constraints
        for path in (getattr(constraint, "paths", None) or [])
        if "." in str(path)
    }
    if wanted in attributes or not attributes:
        return []

    other = "rho" if wanted == "thickness" else "thickness"
    if other not in attributes:
        return []

    return [
        Contradiction(
            kind="freed-parameter-contradicts-template",
            severity="warn",
            subject=", ".join(sorted(attributes)),
            message=(
                f"No constraint varies a {wanted}, but the assessment's "
                f"template implies a {implied} change. "
                + (
                    "An oscillatory template has nodes, and a uniform SLD "
                    "change cannot produce them."
                    if wanted == "thickness"
                    else "A one-sign template is an overall reflectivity "
                    "change, which a thickness alone does not give."
                )
            ),
            evidence=f"template.implied_change = {implied}",
        )
    ]


def _constraint_on_a_flat_run(
    spec: Any, tnr: dict[str, Any] | None
) -> list[Contradiction]:
    """A trajectory fitted through a series that did not detectably change."""
    variogram = (tnr or {}).get("variogram") or {}
    if not variogram.get("flat"):
        return []
    if not _constraints(spec):
        return []
    return [
        Contradiction(
            kind="constraint-on-a-flat-run",
            severity="warn",
            message=(
                "The assessment found the variogram flat -- no detectable "
                "change -- yet the spec constrains a trajectory through the "
                "series. Fitting a trend to noise produces one."
            ),
            evidence="variogram.flat = true",
        )
    ]


def _roughness_coherence(spec: Any) -> list[Contradiction]:
    """Declared ranges that let a layer vanish into its own interfaces.

    Checked on the ranges rather than the fitted values, because a range
    permitting it guarantees the fit will go there. The real failure was an
    oxide whose two interfaces summed to 1.55x its own thickness, so its
    nominal SLD was never attained anywhere in the profile --- and the
    roughness responsible had never been declared as a parameter at all, so it
    sat at whatever the scaffold wrote.
    """
    layers = list(getattr(spec, "stack", None) or [])
    if len(layers) < 2:
        return []

    declared: dict[str, tuple[float, float]] = {}
    for parameter in getattr(spec, "parameters", None) or []:
        bounds = _bounds_of(parameter)
        if bounds is not None:
            declared[str(getattr(parameter, "path", ""))] = bounds

    found = []
    for index, layer in enumerate(layers):
        name = getattr(layer, "name", "")
        # The ambient (first) and substrate (last) have no thickness.
        if index == 0 or index == len(layers) - 1:
            continue
        thickness = _low(
            declared.get(f"{name}.thickness"), getattr(layer, "thickness", 0.0)
        )
        if not thickness:
            continue

        top = _high(declared.get(f"{name}.roughness"), getattr(layer, "roughness", 0.0))
        below = layers[index + 1]
        bottom = _high(
            declared.get(f"{getattr(below, 'name', '')}.roughness"),
            getattr(below, "roughness", 0.0),
        )

        if top + bottom > thickness:
            found.append(
                Contradiction(
                    kind="roughness-swallows-layer",
                    severity="warn",
                    subject=name,
                    message=(
                        f"{name}'s interfaces may sum to {top + bottom:g} A "
                        f"against a minimum thickness of {thickness:g} A, so "
                        "the fit is free to erase the layer into its own "
                        "boundaries -- its nominal SLD would then be attained "
                        "nowhere in the profile. Bound the roughnesses so "
                        "they cannot."
                    ),
                    evidence=(
                        f"max sigma_top {top:g} + max sigma_bottom {bottom:g} "
                        f"> min thickness {thickness:g}"
                    ),
                )
            )
        elif top > ROUGHNESS_FRACTION * thickness:
            found.append(
                Contradiction(
                    kind="roughness-exceeds-half-thickness",
                    severity="info",
                    subject=f"{name}.roughness",
                    message=(
                        f"{name}'s roughness may reach {top:g} A against a "
                        f"minimum thickness of {thickness:g} A. Past half, the "
                        "model is describing a gradient with the wrong tool."
                    ),
                    evidence=f"{top:g} > {ROUGHNESS_FRACTION} x {thickness:g}",
                )
            )
    return found


def _sld_within_material(
    spec: Any, fitted: dict[str, float] | None
) -> list[Contradiction]:
    """A fitted SLD outside anything the layer's material could be.

    Only checked against the range the spec itself declares for that path, so
    it never invents a materials opinion: if nobody said what the layer is
    made of, nothing is claimed about where its SLD may sit.
    """
    if not fitted:
        return []

    ranges: dict[str, tuple[float, float]] = {}
    for parameter in getattr(spec, "parameters", None) or []:
        path = str(getattr(parameter, "path", ""))
        if not path.endswith(".rho"):
            continue
        bounds = _bounds_of(parameter)
        if bounds is not None:
            ranges[path.rsplit(".", 1)[0]] = bounds

    found = []
    for name, value in sorted(fitted.items()):
        layer = _layer_of(name)
        if layer is None or not name.endswith("rho"):
            continue
        bounds = ranges.get(layer)
        if bounds is None:
            continue
        low, high = bounds
        if low - SLD_SLACK <= value <= high + SLD_SLACK:
            continue
        found.append(
            Contradiction(
                kind="sld-outside-material-range",
                severity="warn",
                subject=name,
                message=(
                    f"{name} fitted to {value:.4g}, outside the "
                    f"[{low:g}, {high:g}] the spec declares for {layer}. "
                    "Either the range is wrong or the layer is not the "
                    "material it is named after."
                ),
                evidence=f"declared range [{low:g}, {high:g}]",
            )
        )
    return found


def _bounds_of(parameter: Any) -> tuple[float, float] | None:
    """A parameter's declared bounds, from ``range`` or ``value`` plus ``pm``."""
    limits = getattr(parameter, "range", None)
    if limits and len(limits) == 2:
        return float(limits[0]), float(limits[1])
    value, pm = getattr(parameter, "value", None), getattr(parameter, "pm", None)
    if value is not None and pm:
        return float(value) - float(pm), float(value) + float(pm)
    return None


def _low(bounds: tuple[float, float] | None, fallback: float) -> float:
    """The smallest a quantity may be."""
    return float(bounds[0]) if bounds else float(fallback or 0.0)


def _high(bounds: tuple[float, float] | None, fallback: float) -> float:
    """The largest a quantity may be."""
    return float(bounds[1]) if bounds else float(fallback or 0.0)


def _layer_of(parameter_name: str) -> str | None:
    """The layer a refl1d parameter name refers to.

    Names look like ``run218386 CuOx rho`` or ``CuOx rho``; the layer is the
    token before the attribute.
    """
    tokens = re.split(r"\s+", parameter_name.strip())
    return tokens[-2] if len(tokens) >= 2 else None


def matches(path: str, patterns: list[str]) -> bool:
    """Whether a parameter path is covered by any constraint pattern.

    Args:
        path: A path such as ``CuOx.thickness``.
        patterns: Constraint paths, globs allowed.

    Returns:
        True if any pattern matches.
    """
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)
