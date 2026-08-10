"""Semantic checks beyond what the schema can express.

pydantic catches shape errors. These catch the errors that are *well-formed*
but wrong -- the ones that produce a script which runs, fits, and reports a
number that means nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from nr_workbench.spec.models import Layer, ModelSpec
from nr_workbench.spec.resolve import ParameterTable

#: Warn when free parameters outnumber data points by more than this. A fit
#: with more freedom than information will converge and mean nothing.
DOF_WARN_RATIO = 0.1

#: Prefix of the "nobody chose this value" line. A constant because the agent
#: session filters `report.info` for exactly this finding, and matching it on
#: punctuation (an `=` in the text) silently drops the case where a held
#: attribute has no resolvable value --- which is the case that swallowed the
#: oxide in the reference experiment.
HELD_AT_DEFAULTS = "held at their starting values: "


@dataclass
class ValidationReport:
    """The outcome of validating a spec.

    Attributes:
        errors: Problems that make the spec unusable.
        warnings: Problems worth seeing that do not block generation.
        info: Facts worth stating, such as the parameter count.
    """

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    info: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Whether the spec can be generated from."""
        return not self.errors

    def as_dict(self) -> dict[str, list[str]]:
        """Return the JSON-serialisable form."""
        return {"errors": self.errors, "warnings": self.warnings, "info": self.info}


def validate_spec(spec: ModelSpec, root: Path) -> ValidationReport:
    """Validate a spec against the project on disk.

    Args:
        spec: The parsed spec.
        root: Project root, for resolving data paths.

    Returns:
        The report. Check ``ok`` before generating.
    """
    from nr_workbench.spec.models import SpecError
    from nr_workbench.spec.resolve import build_table, discover_measurements

    report = ValidationReport()

    try:
        measurements = discover_measurements(spec, root)
    except SpecError as exc:
        report.errors.append(str(exc))
        return report

    try:
        table = build_table(spec, measurements)
    except SpecError as exc:
        report.errors.append(str(exc))
        return report

    report.warnings.extend(table.warnings)
    _check_materials(spec, report)
    _check_files_exist(table, root, report)
    _check_degrees_of_freedom(table, root, report)
    _check_series_times(table, report)
    _check_unfitted(spec, table, report)
    _check_angle_nuisance(spec, report)
    _note_escape_hatches(spec, report)

    report.info.append(
        f"{table.n_experiments} experiment(s), {table.n_free} free parameter(s), "
        f"{len(table.expressions)} constrained value(s)"
    )
    return report


def _check_materials(spec: ModelSpec, report: ValidationReport) -> None:
    """Every layer must name a material that exists."""
    for layer in spec.stack:
        if layer.material_key not in spec.materials:
            report.errors.append(
                f"layer {layer.name!r} uses material {layer.material_key!r}, which is "
                f"not defined. Known: {', '.join(sorted(spec.materials)) or '(none)'}."
            )


def _check_files_exist(
    table: ParameterTable, root: Path, report: ValidationReport
) -> None:
    """Every referenced data file must be readable.

    This is the check that replaces the reference script's
    ``# DON'T FORGET TO UPDATE THE DATA DIRECTORIES ABOVE``.
    """
    for path in table.all_files:
        target = root / path
        if not target.is_file():
            report.errors.append(f"data file not found: {path}")
        elif target.stat().st_size == 0:
            report.errors.append(f"data file is empty: {path}")


def _check_degrees_of_freedom(
    table: ParameterTable, root: Path, report: ValidationReport
) -> None:
    """Warn when the fit has more freedom than the data can support."""
    points = 0
    for path in table.all_files:
        target = root / path
        if not target.is_file():
            return
        try:
            with target.open(encoding="utf-8", errors="ignore") as handle:
                points += sum(
                    1
                    for line in handle
                    if line.strip() and not line.lstrip().startswith("#")
                )
        except OSError:
            return

    if points == 0:
        report.errors.append("the referenced data files contain no usable points")
        return

    report.info.append(
        f"{points} data point(s) across {table.n_experiments} experiment(s)"
    )
    if table.n_free > DOF_WARN_RATIO * points:
        report.warnings.append(
            f"{table.n_free} free parameters against {points} data points "
            f"({table.n_free / points:.1%}). A fit with this much freedom will "
            "converge on almost anything."
        )


def _check_series_times(table: ParameterTable, report: ValidationReport) -> None:
    """A series' time axis must be strictly increasing."""
    for series in table.spec.series:
        measurements = table.measurements.get(series.name, [])
        times = [m.time for m in measurements if m.time is not None]
        if len(times) < 2:
            continue
        if any(b <= a for a, b in zip(times, times[1:], strict=False)):
            report.errors.append(
                f"series {series.name!r}: times are not strictly increasing. "
                "Slices are ordered by the reduction JSON or the filename time, "
                "and a repeat or a reversal means the selection is wrong."
            )

        spacings = [b - a for a, b in zip(times, times[1:], strict=False)]
        if spacings and max(spacings) > 3 * min(spacings):
            uses_index = any(
                c.form == "linear_in_index"
                for c in table.spec.constraints
                if c.series == series.name
            )
            if uses_index:
                report.warnings.append(
                    f"series {series.name!r}: slice spacing varies by "
                    f"{max(spacings) / min(spacings):.1f}x, but a constraint uses "
                    "`linear_in_index`. Index and time diverge here -- "
                    "`linear_in_time` is almost certainly what you want."
                )


def _check_unfitted(
    spec: ModelSpec, table: ParameterTable, report: ValidationReport
) -> None:
    """Note the attributes left at their starting values.

    Per attribute, not per layer. Testing whole layers is what let the real
    failure through: `cu-thf-coref-ocv1-ocv2.yaml` declared `dTHF.rho` and not
    `dTHF.roughness`, so the layer counted as touched and the roughness stayed
    at the 20 A the scaffold wrote. *"Nobody chose it."* That one unexamined
    number swallowed the oxide in the state below it, and every fit downstream
    imported the result as a constant.

    Reported at info, with the value, because fixing an attribute is normal
    and deliberate --- what is not normal is not knowing you did.
    """
    touched = {(slot.path.owner, slot.path.attr) for slot in table.slots}

    held: list[str] = []
    for index, layer in enumerate(spec.stack):
        # The substrate is conventionally fixed; the ambient has no thickness.
        if index == len(spec.stack) - 1:
            continue
        attributes = (
            ["roughness", "rho"] if index == 0 else ["thickness", "roughness", "rho"]
        )
        for attr in attributes:
            if (layer.name, attr) in touched:
                continue
            value = _starting_value(spec, layer, attr)
            held.append(
                f"{layer.name}.{attr}={value:g}"
                if value is not None
                else f"{layer.name}.{attr}"
            )

    if held:
        report.info.append(HELD_AT_DEFAULTS + ", ".join(held))


def _starting_value(spec: ModelSpec, layer: Layer, attr: str) -> float | None:
    """What an unfitted attribute will actually be during the fit."""
    if attr in {"thickness", "roughness"}:
        return float(getattr(layer, attr, 0.0) or 0.0)
    material = spec.materials.get(layer.material_key)
    rho = getattr(material, "rho", None) if material else None
    return float(rho) if rho is not None else None


def _note_escape_hatches(spec: ModelSpec, report: ValidationReport) -> None:
    """Flag `post_build`: every use is a schema gap worth recording."""
    if spec.post_build:
        report.warnings.append(
            "this spec uses `post_build`, so part of the model is verbatim Python "
            "the schema cannot express. That is supported, but each use is a "
            "report that the schema is missing something -- please say what."
        )


#: Probe parameters that only mean anything on an angle-based probe. Both
#: describe the *incident angle*, and a combined file has already been stitched
#: across several of them.
_PARTIALS_ONLY = ("theta_offset", "sample_broadening")


def _check_angle_nuisance(spec, report) -> None:
    """Reject theta_offset and sample_broadening on a combined state.

    A combined file is the reduction's stitch of every angle setting, so it has
    no single incident angle to offset or broaden. refl1d accepts the parameter
    and fits it to something meaningless, which is the worst of the three
    possible behaviours.

    AuRE draws the same line -- its ``_NUISANCE_KEYS`` are partials-only for
    this reason.
    """
    combined = {
        state.name for state in spec.states if getattr(state, "kind", "") == "combined"
    }
    if not combined:
        return

    for parameter in spec.parameters:
        attribute = parameter.path.split(".", 1)[-1]
        if not parameter.path.startswith("probe.") or attribute not in _PARTIALS_ONLY:
            continue
        targets = set(parameter.in_ or []) or combined
        offending = sorted({t.split("#", 1)[0] for t in targets} & combined)
        if offending:
            report.errors.append(
                f"probe.{attribute} is declared for {', '.join(offending)}, which "
                f"{'is a' if len(offending) == 1 else 'are'} combined state"
                f"{'' if len(offending) == 1 else 's'}. A combined file is the "
                "reduction's stitch of every angle setting, so it has no single "
                "incident angle to offset or broaden. Either reduce that run per "
                "angle (segments: auto) or scope this parameter to the states "
                "that have segments with `in:`."
            )
