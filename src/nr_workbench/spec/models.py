"""The ``nrw-model/1`` schema.

Three ideas carry the whole design.

**One stack, many states.** The hand-written scripts call ``create_sample()``
once per experiment and then spend two hundred lines re-linking parameters.
Inverted here: the layer stack is declared once and states instantiate it.

**Parameter identity is the primitive, not parameter value.** Every bug and
every line of boilerplate in those scripts is about *which Parameter object* a
given ``(state, measurement, attribute)`` slot points at. So the spec declares
that directly, with ``per``:

===================  ====================================================
``per: model``       one Parameter for the entire FitProblem
``per: state``       one per state or series (a whole series counts as one)
``per: measurement`` one per angle segment, or per time slice
===================  ====================================================

Within a state, angle segments alias segment 0 **automatically**. The user
never writes it, and that single default removes ~180 lines of the reference
script.

**A series is a state with a time axis.** AuRE's ``states:`` are N unordered
named conditions. Adding ``t`` and a ``constraints:`` block is what makes
time-resolved data first-class rather than "nine more states".

Validation is pydantic v2 rather than the TypedDicts AuRE uses or the
prose-only schema nr-analyzer has: an agent writing these files needs to be
told precisely where it went wrong, and the JSON Schema this emits drives live
validation in the editor.
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = "nrw-model/1"

#: Attributes addressable on a layer. ``roughness`` is the friendly alias for
#: refl1d's ``interface``, which is what the generated code actually sets.
LAYER_ATTRS = ("thickness", "roughness", "rho", "irho")

#: Attributes addressable on the probe.
PROBE_ATTRS = ("intensity", "background", "theta_offset", "sample_broadening")

#: Extra help for probe settings people reasonably try to fit and cannot. Being
#: told a name is invalid, when the name exists and is settable elsewhere in the
#: same block, is the kind of error that reads as a typo.
_WHY_NOT_FITTABLE: dict[str, str] = {
    "dq_is_fwhm": (
        "\n`dq_is_fwhm` is a property of the reduction, not a quantity to "
        "estimate: the file's column titles say which convention it wrote. Set "
        "it under `probe:`, or let `nrw model new` read it."
    ),
    "dq_scale": (
        "\n`dq_scale` is a fixed correction to a reduction whose dQ is wrong, so "
        "set it under `probe:` rather than fitting it. refl1d has no fittable dQ "
        "scale -- `Probe.dQ` is derived from a fixed array plus the broadening. "
        "To *fit* a resolution, use `probe.sample_broadening` with "
        "`per: measurement`: dtheta = f * tan(theta) is a constant dQ/Q, so one "
        "broadening per angle is a fitted relative resolution."
    ),
    "back_reflection": (
        "\n`back_reflection` is the measurement geometry -- which side the beam "
        "entered. Set it under `probe:`; it is not something a fit can discover."
    ),
    "resolution": (
        "\n`resolution` names the convention, not a number. Set it under `probe:`."
    ),
}

#: How a parameter is shared. See the module docstring.
Grouping = Literal["model", "state", "measurement"]

#: Where the data for a state comes from.
StateKind = Literal["partials", "combined"]

_PATH_RE = re.compile(
    r"^(?P<owner>[A-Za-z_][\w-]*)\.(?P<attr>[a-z_]+)(?:@(?P<state>[\w-]+))?$"
)

#: Identifiers become Python names and filenames in the generated script.
_NAME_RE = re.compile(r"^[A-Za-z_][\w-]*$")


class SpecError(ValueError):
    """Raised when a spec is structurally invalid."""


class _Base(BaseModel):
    """Shared config: reject unknown keys so a typo is an error, not a silence."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class ParameterPath(_Base):
    """A parsed ``Layer.attr`` or ``probe.attr`` reference.

    Attributes:
        owner: Layer name, or the literal ``probe``.
        attr: The attribute on that owner.
        state: Optional explicit state, for disambiguation.
    """

    owner: str
    attr: str
    state: str | None = None

    @classmethod
    def parse(cls, text: str) -> ParameterPath:
        """Parse a path string.

        Args:
            text: Something like ``Cu.thickness`` or ``probe.intensity@ocv1``.

        Returns:
            The parsed path.

        Raises:
            SpecError: If the syntax or the attribute name is wrong.
        """
        match = _PATH_RE.match(text.strip())
        if match is None:
            raise SpecError(
                f"Cannot parse parameter path {text!r}. Expected 'Layer.attr' or "
                "'probe.attr', optionally suffixed with '@state'."
            )
        owner, attr, state = (
            match.group("owner"),
            match.group("attr"),
            match.group("state"),
        )

        allowed = PROBE_ATTRS if owner == "probe" else LAYER_ATTRS
        if attr not in allowed:
            raise SpecError(
                f"{text!r}: '{attr}' is not valid for {owner}. "
                f"Allowed: {', '.join(allowed)}." + _WHY_NOT_FITTABLE.get(attr, "")
            )
        return cls(owner=owner, attr=attr, state=state)

    @property
    def is_probe(self) -> bool:
        """Whether this addresses the probe rather than a layer."""
        return self.owner == "probe"

    def render(self) -> str:
        """Return the canonical string form."""
        return f"{self.owner}.{self.attr}" + (f"@{self.state}" if self.state else "")


class Material(_Base):
    """A material's scattering length density.

    Attributes:
        rho: Real SLD in 1e-6 A^-2.
        irho: Imaginary SLD (absorption).
        formula: Chemical formula, for SLD lookup when ``rho`` is omitted.
        density: Mass density in g/cm^3, used with ``formula``.
    """

    rho: float | None = None
    irho: float = 0.0
    formula: str | None = None
    density: float | None = None

    @model_validator(mode="after")
    def _needs_rho_or_formula(self) -> Material:
        if self.rho is None and self.formula is None:
            raise SpecError("a material needs either `rho` or `formula`")
        return self


class Layer(_Base):
    """One slab in the stack.

    Attributes:
        name: Layer name; how it is addressed in paths, and how the generated
            script indexes the refl1d sample.
        material: Key into ``materials``. Defaults to the layer name.
        thickness: Starting thickness in Angstroms. Ignored for the ambient
            and substrate, which are semi-infinite.
        roughness: Starting interfacial roughness in Angstroms.
    """

    name: str
    material: str | None = None
    thickness: float = 0.0
    roughness: float = 0.0

    @field_validator("name")
    @classmethod
    def _usable_name(cls, value: str) -> str:
        if not _NAME_RE.match(value):
            raise SpecError(
                f"layer name {value!r} must start with a letter and be name-like"
            )
        return value

    @property
    def material_key(self) -> str:
        """The material this layer refers to."""
        return self.material or self.name


class Probe(_Base):
    """How the probe is constructed from the data.

    Attributes:
        resolution: Only ``angular_only``: dT is derived from dQ at the known
            incident angle and dL is zero. See the class body for why the
            moderator variant was dropped.
        dq_is_fwhm: Whether the 4th data column is FWHM. Read it off the file's
            column titles rather than assuming; treating FWHM as sigma scales
            every resolution by 2.355.
        dq_scale: Constant factor applied to the dQ column, for a reduction whose
            resolution estimate is demonstrably wrong. Fixed rather than fitted;
            see the note in the class body.
        back_reflection: Neutrons enter through the substrate.
    """

    # Some hand-written REF_L scripts instead computed dL from the SNS
    # moderator emission-time polynomial as `delta_wl_over_wl(wl) * q` --
    # multiplied by q rather than by wl, which is dimensionally wrong. That
    # variant is deliberately not supported: carrying two resolution
    # conventions means two sets of results that cannot be compared, and only
    # one of them is right.
    #
    # A spec that asks for it fails loudly rather than silently changing the
    # physics, which is what a bare Literal error would amount to.
    # `dq_scale` is FIXED, not fittable, and that is a refl1d constraint rather
    # than a preference. `Probe.parameters()` exposes exactly five knobs --
    # intensity, background, back_absorption, theta_offset, sample_broadening --
    # and `Probe.dQ` is a property derived from a plain `dQo` array plus the
    # broadening. There is no fittable dQ scale to bind to, and inventing one
    # would mean a Probe subclass overriding the resolution path, which the
    # generated script could no longer claim to be ordinary refl1d.
    #
    # The fittable route already exists: a constant *relative* resolution is
    # `probe.sample_broadening` scoped `per: measurement`, because dtheta = f *
    # tan(theta) gives a constant dQ/Q. Use this field for the different case of
    # a reduction whose dQ column is wrong by a known factor -- a correction to
    # the data, which belongs beside `dq_is_fwhm` and not in the fit.
    resolution: Literal["angular_only"] = "angular_only"
    dq_is_fwhm: bool = True
    dq_scale: float = 1.0
    back_reflection: bool = False

    @field_validator("dq_scale")
    @classmethod
    def _positive_scale(cls, value: float) -> float:
        if not value > 0:
            raise SpecError(
                f"probe.dq_scale must be positive, got {value}. It multiplies the "
                "resolution; zero would make every point infinitely sharp and a "
                "negative value is not a width."
            )
        return value

    @model_validator(mode="before")
    @classmethod
    def _reject_retired_resolutions(cls, data: Any) -> Any:
        if isinstance(data, dict) and data.get("resolution") == "moderator":
            raise SpecError(
                "probe.resolution: 'moderator' is no longer supported. It computed "
                "the wavelength spread as `delta_wl_over_wl(wl) * q` -- multiplied "
                "by q rather than wl -- and BL-4B has standardised on the "
                "angular-only convention (dL = 0). Use `resolution: angular_only`.\n"
                "Note that fits made under the moderator convention are not "
                "numerically comparable with angular-only ones, so re-run rather "
                "than compare."
            )
        return data


class Trim(_Base):
    """A range of the data to keep, for one measurement or all of them.

    Some data is wrong in a way no parameter can absorb. A segment whose required
    scale varies *across* its own wavelength band --- typically at the short-λ
    edge, where the direct-beam spectrum is weakest --- cannot be fixed by
    ``probe.intensity``, which is one number per segment. Left in, the fit spends
    a thickness or a roughness on it. The honest options are to re-reduce or to
    cut the band, and until now cutting meant ``nrw model fork``, which takes the
    model out of the spec entirely for the sake of two numbers.

    Cuts are declared here so they stay in the spec, get hashed with it, and
    appear in the generated script's own docstring. Dropping data must never be
    invisible.

    **λ and Q are not interchangeable across segments.** A band-edge problem is a
    property of the wavelength, and one λ cut maps to a different Q in every
    segment: at 0.37° λ = 3.5 Å is Q = 0.023, at 3.5° it is Q = 0.219. State it
    in λ and it is right everywhere; state it in Q and it is right once.

    Attributes:
        in_: Measurements this applies to, as state names or ``state#index``
            keys. ``None`` means every measurement.
        q_min: Drop points below this Q.
        q_max: Drop points above this Q.
        lambda_min: Drop points below this wavelength, in angstroms.
        lambda_max: Drop points above this wavelength.
        reason: Why the data is being cut. Required: a silent cut is
            indistinguishable from a mistake six months later.
    """

    in_: list[str] | None = Field(default=None, alias="in")
    q_min: float | None = None
    q_max: float | None = None
    lambda_min: float | None = None
    lambda_max: float | None = None
    reason: str

    @model_validator(mode="after")
    def _coherent_range(self) -> Trim:
        if not (self.reason or "").strip():
            raise SpecError(
                "trim: `reason` is required. Cutting data changes what a fit is "
                "fitted to, and a cut nobody explained reads as an error later."
            )
        for low, high, name in (
            (self.q_min, self.q_max, "q"),
            (self.lambda_min, self.lambda_max, "lambda"),
        ):
            if low is not None and high is not None and low >= high:
                raise SpecError(
                    f"trim: {name}_min ({low}) must be below {name}_max ({high}); "
                    "as written this keeps nothing."
                )
        if all(
            bound is None
            for bound in (self.q_min, self.q_max, self.lambda_min, self.lambda_max)
        ):
            raise SpecError(
                "trim: give at least one of q_min, q_max, lambda_min, lambda_max. "
                "An entry with no bounds cuts nothing and reads as though it does."
            )
        return self

    def as_kwargs(self) -> dict[str, float]:
        """The bounds as keyword arguments for the generated ``create_probe``."""
        return {
            name: value
            for name, value in (
                ("q_min", self.q_min),
                ("q_max", self.q_max),
                ("lambda_min", self.lambda_min),
                ("lambda_max", self.lambda_max),
            )
            if value is not None
        }


class Segment(_Base):
    """One angle segment of a steady-state measurement.

    Attributes:
        file: Path to the reduced data, relative to the project root.
        theta: Incident angle in degrees -- theta, not two-theta.
    """

    file: str
    theta: float


class State(_Base):
    """A steady-state measurement of the sample under one condition.

    Attributes:
        name: Identifier used in paths and ``in`` lists.
        condition: Free text, e.g. "OCV before EIS". Carried into the record.
        run: Run number, used to resolve ``segments: auto``.
        kind: ``partials`` (one file per angle) or ``combined``.
        segments: Explicit segment list, or ``auto`` to resolve from ``run``.
        thetas: Angles for ``segments: auto``.
        data_dir: Directory holding the files, relative to the project root.
    """

    name: str
    condition: str = ""
    run: int | None = None
    kind: StateKind = "partials"
    segments: list[Segment] | Literal["auto"] = "auto"
    thetas: list[float] = Field(default_factory=lambda: [0.45, 1.2, 3.5])
    data_dir: str | None = None

    @field_validator("name")
    @classmethod
    def _usable_name(cls, value: str) -> str:
        if not _NAME_RE.match(value):
            raise SpecError(
                f"state name {value!r} must start with a letter and be name-like"
            )
        return value

    @model_validator(mode="after")
    def _auto_needs_a_run(self) -> State:
        if self.segments == "auto" and self.run is None:
            raise SpecError(
                f"state {self.name!r}: `segments: auto` needs a `run` number"
            )
        return self


class SeriesSelect(_Base):
    """Which slices of a time-resolved run to include.

    Attributes:
        t_start: First slice time in seconds.
        t_stop: Last slice time in seconds, inclusive.
        t_step: Spacing in seconds.
        labels: Explicit interval labels or fnmatch globs, instead of a time range.
        interval_types: Restrict to these interval types, e.g. ``[hold]``.
    """

    t_start: float | None = None
    t_stop: float | None = None
    t_step: float | None = None
    labels: list[str] | None = None
    interval_types: list[str] | None = None

    @model_validator(mode="after")
    def _one_selection_mode(self) -> SeriesSelect:
        by_time = self.t_step is not None
        if by_time and self.labels:
            raise SpecError("select: use a time range or `labels`, not both")
        if by_time and (self.t_start is None or self.t_stop is None):
            raise SpecError("select: `t_step` needs both `t_start` and `t_stop`")
        return self


class Series(_Base):
    """A time-resolved measurement: a state with a time axis.

    Attributes:
        name: Identifier used in paths, ``in`` lists and ``constraints.series``.
        condition: Free text, e.g. "during EIS".
        run: Run number.
        reduced_dir: Directory of reduced slices, relative to the project root.
        theta: Incident angle in degrees. tNR is a single angle.
        select: Which slices to include.
        time_from: Where slice times come from. ``filename`` parses
            ``r<run>_t<seconds>.txt``; ``reduction_json`` reads the sidecar,
            which is the only source that also carries interval types.
    """

    name: str
    condition: str = ""
    run: int | None = None
    reduced_dir: str
    theta: float = 0.6
    select: SeriesSelect = Field(default_factory=SeriesSelect)
    time_from: Literal["filename", "reduction_json"] = "filename"

    @field_validator("name")
    @classmethod
    def _usable_name(cls, value: str) -> str:
        if not _NAME_RE.match(value):
            raise SpecError(
                f"series name {value!r} must start with a letter and be name-like"
            )
        return value


class ParameterSpec(_Base):
    """One free or fixed parameter.

    Attributes:
        path: What it addresses, e.g. ``Cu.thickness`` or ``probe.intensity``.
        per: How it is shared. See the module docstring.
        in_: Restrict to these states or series. ``None`` means all of them.
        range: Bounds as ``[min, max]``.
        value: Starting value. Defaults to the stack's.
        pm: Symmetric bounds around ``value``, as an alternative to ``range``.
        fixed: Pin to ``value`` and do not fit it.
        init: ``stack`` takes the starting value from the layer definition.
        name: Override the generated parameter name.
    """

    path: str
    per: Grouping = "state"
    in_: list[str] | None = Field(default=None, alias="in")
    range: tuple[float, float] | None = None
    value: float | None = None
    pm: float | None = None
    fixed: float | bool | None = None
    init: Literal["stack"] | None = None
    name: str | None = None

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    @model_validator(mode="after")
    def _bounds_are_coherent(self) -> ParameterSpec:
        ParameterPath.parse(self.path)

        if self.range is not None and self.pm is not None:
            raise SpecError(f"{self.path}: use `range` or `pm`, not both")
        if self.range is not None and self.range[0] >= self.range[1]:
            raise SpecError(f"{self.path}: range {self.range} is empty or inverted")
        if self.pm is not None and self.pm <= 0:
            raise SpecError(f"{self.path}: `pm` must be positive")

        if self.fixed not in (None, False):
            if self.range is not None or self.pm is not None:
                raise SpecError(
                    f"{self.path}: a fixed parameter cannot also have bounds"
                )
        elif self.range is None and self.pm is None:
            raise SpecError(
                f"{self.path}: needs `range`, `pm`, or `fixed`. A parameter with no "
                "bounds would be silently frozen at its starting value."
            )
        return self

    @property
    def parsed(self) -> ParameterPath:
        """The parsed path."""
        return ParameterPath.parse(self.path)

    @property
    def is_fixed(self) -> bool:
        """Whether this parameter is pinned rather than fitted."""
        return self.fixed not in (None, False)


class Constraint(_Base):
    """A functional form tying a series' parameters to its endpoints.

    This is what the hand-written ``linear_constraints()`` does, declared
    instead of coded: each slice's value becomes an expression in the endpoint
    parameters, so only the endpoints are free.

    Attributes:
        series: The series this applies to.
        form: The functional form. See :mod:`nr_workbench.spec.constraints`.
        paths: Parameter paths to constrain. Globs allowed, e.g. ``*.roughness``.
        from_: State supplying the starting value, for interpolating forms.
        to: State supplying the final value.
        knots: Number of free knots, for ``piecewise_linear``.
        endpoint_range: Bounds for an endpoint written as ``free``. Needed
            only when the path has no other declaration to borrow from -- a
            series with no bracketing steady states.
        tau: Bounds for the time constant, for ``exponential``.
        t_half: Bounds for the midpoint, for ``logistic``.
        width: Bounds for the transition width, for ``logistic``.
    """

    series: str
    form: str
    paths: list[str]
    from_: str | None = Field(default=None, alias="from")
    to: str | None = None
    knots: int = 3
    endpoint_range: tuple[float, float] | None = None
    tau: tuple[float, float] | None = None
    t_half: tuple[float, float] | None = None
    width: tuple[float, float] | None = None

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    @model_validator(mode="after")
    def _form_is_known_and_supplied(self) -> Constraint:
        from nr_workbench.spec.constraints import FORMS

        if self.form not in FORMS:
            raise SpecError(
                f"unknown constraint form {self.form!r}. Available: {', '.join(sorted(FORMS))}."
            )
        FORMS[self.form].validate_constraint(self)
        return self


class FitSettings(_Base):
    """Default fit settings recorded with the model.

    Attributes:
        method: Bumps fitter name.
        steps: Maximum optimizer steps.
        samples: DREAM sample count.
        burn: DREAM burn-in.
        seed: Random seed.
    """

    method: str = "amoeba"
    steps: int | None = None
    samples: int | None = None
    burn: int | None = None
    seed: int | None = None


class ModelSpec(_Base):
    """A complete ``nrw-model/1`` document.

    Attributes:
        schema_: The schema identifier; must be ``nrw-model/1``.
        name: Model name. Becomes the generated script's basename.
        sample: Sample identifier this model belongs to.
        description: Free prose. Carried into the generated script and the record.
        materials: Material definitions by name.
        stack: Layers, ambient first and substrate last.
        probe: Probe construction settings.
        states: Steady-state measurements.
        series: Time-resolved measurements.
        parameters: Free and fixed parameters.
        constraints: Functional forms across a series.
        trim: Ranges of the data to keep. Later entries win field by field,
            so a global cut can be narrowed for one measurement.
        fit: Default fit settings.
        post_build: Verbatim Python appended to the generated script. An escape
            hatch, hash-tracked and flagged -- every use is a schema bug report.
    """

    schema_: Literal["nrw-model/1"] = Field(alias="schema")
    name: str
    sample: str | None = None
    description: str = ""
    materials: dict[str, Material] = Field(default_factory=dict)
    stack: list[Layer]
    probe: Probe = Field(default_factory=Probe)
    states: list[State] = Field(default_factory=list)
    series: list[Series] = Field(default_factory=list)
    parameters: list[ParameterSpec] = Field(default_factory=list)
    constraints: list[Constraint] = Field(default_factory=list)
    trim: list[Trim] = Field(default_factory=list)
    fit: FitSettings = Field(default_factory=FitSettings)
    post_build: str | None = None

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    @model_validator(mode="after")
    def _structurally_coherent(self) -> ModelSpec:
        if len(self.stack) < 2:
            raise SpecError("a stack needs at least an ambient and a substrate")

        names = [layer.name for layer in self.stack]
        if len(set(names)) != len(names):
            duplicates = sorted({n for n in names if names.count(n) > 1})
            raise SpecError(f"duplicate layer name(s) in the stack: {duplicates}")

        groups = [*(s.name for s in self.states), *(s.name for s in self.series)]
        if len(set(groups)) != len(groups):
            duplicates = sorted({n for n in groups if groups.count(n) > 1})
            raise SpecError(f"duplicate state/series name(s): {duplicates}")
        if not groups:
            raise SpecError("a model needs at least one state or series")

        return self

    @property
    def group_names(self) -> list[str]:
        """Every state and series name, in declaration order."""
        return [*(s.name for s in self.states), *(s.name for s in self.series)]

    @property
    def layer_names(self) -> list[str]:
        """Every layer name, ambient first."""
        return [layer.name for layer in self.stack]

    def layer(self, name: str) -> Layer | None:
        """Look up a layer by name.

        Args:
            name: The layer name.

        Returns:
            The layer, or ``None`` if absent.
        """
        return next((layer for layer in self.stack if layer.name == name), None)

    def series_by_name(self, name: str) -> Series | None:
        """Look up a series by name.

        Args:
            name: The series name.

        Returns:
            The series, or ``None`` if absent.
        """
        return next((s for s in self.series if s.name == name), None)

    def to_yaml_dict(self) -> dict[str, Any]:
        """Return the round-trippable mapping form."""
        return self.model_dump(by_alias=True, exclude_none=True, mode="json")


def load_spec(path: Any) -> ModelSpec:
    """Load and validate a spec from a YAML file.

    Args:
        path: Path to the YAML document.

    Returns:
        The validated spec.

    Raises:
        SpecError: If the file cannot be read, parsed, or validated.
    """
    from pathlib import Path

    import yaml
    from pydantic import ValidationError

    target = Path(path)
    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SpecError(f"Cannot read {target}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise SpecError(f"{target} is not valid YAML: {exc}") from exc

    if not isinstance(raw, dict):
        raise SpecError(f"{target} must contain a mapping at the top level")

    try:
        return ModelSpec.model_validate(raw)
    except ValidationError as exc:
        raise SpecError(
            f"{target} is not a valid {SCHEMA_VERSION} document:\n{exc}"
        ) from exc


AnyModel = Annotated[ModelSpec, Field(description="An nrw-model/1 document")]
