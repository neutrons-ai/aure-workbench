"""Functional forms tying a series' parameters to its endpoints.

This is what the hand-written ``linear_constraints()`` did, declared instead of
coded. Each form knows two things: which extra free parameters it introduces,
and what Python expression to emit for slice ``i``.

The expressions are built from bumps ``Parameter`` arithmetic, so they are real
constraints -- only the endpoints (and any parameters the form introduces) are
fitted, and the per-slice values follow. Verified against bumps 1.0.4:
``bumps.parameter.pmath`` provides the transcendental functions, and problems
containing such expressions serialize and round-trip.

Adding a form means adding one class here and nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from nr_workbench.spec.models import Constraint


class ConstraintError(ValueError):
    """Raised when a constraint is misconfigured for its form."""


@dataclass
class ExtraParameter:
    """A free parameter a form introduces beyond the endpoints.

    Attributes:
        suffix: Appended to the constraint's key to name it.
        default: Starting value.
        bounds: ``(min, max)``.
        description: What it means, for the generated comment.
    """

    suffix: str
    default: float
    bounds: tuple[float, float]
    description: str = ""


@dataclass
class FormContext:
    """What a form needs to emit an expression.

    Attributes:
        start_key: Key of the parameter holding the starting value.
        end_key: Key of the parameter holding the final value.
        extra_keys: Keys of the parameters this form introduced.
        times: Slice times in seconds.
        n: Number of slices.
    """

    start_key: str | None
    end_key: str | None
    extra_keys: dict[str, str] = field(default_factory=dict)
    times: list[float] = field(default_factory=list)
    n: int = 0

    def fraction_by_index(self, i: int) -> float:
        """Position of slice ``i`` in ``[0, 1]`` by index."""
        return 0.0 if self.n <= 1 else i / (self.n - 1)

    def fraction_by_time(self, i: int) -> float:
        """Position of slice ``i`` in ``[0, 1]`` by elapsed time."""
        if self.n <= 1 or not self.times:
            return 0.0
        span = self.times[-1] - self.times[0]
        if span <= 0:
            return self.fraction_by_index(i)
        return (self.times[i] - self.times[0]) / span


class ConstraintForm:
    """Base class for a functional form."""

    #: The name used in ``constraints.form``.
    name: ClassVar[str] = ""

    #: One line for `nrw model forms` and the docs.
    summary: ClassVar[str] = ""

    #: Whether the form interpolates between two endpoint states.
    needs_endpoints: ClassVar[bool] = True

    @classmethod
    def validate_constraint(cls, constraint: Constraint) -> None:
        """Check the constraint supplies what this form needs.

        Args:
            constraint: The constraint to check.

        Raises:
            ConstraintError: If required fields are missing or incoherent.
        """
        if cls.needs_endpoints and not (constraint.from_ and constraint.to):
            raise ConstraintError(
                f"form {cls.name!r} interpolates between two states, so it needs "
                "both `from` and `to`."
            )

    @classmethod
    def extra_parameters(cls, constraint: Constraint) -> list[ExtraParameter]:
        """Extra free parameters this form introduces.

        Args:
            constraint: The constraint being resolved.

        Returns:
            The parameters to create, which may be empty.
        """
        return []

    @classmethod
    def expression(cls, i: int, context: FormContext) -> str:
        """Python source for slice ``i``'s value.

        Args:
            i: Zero-based slice index.
            context: Keys and times to build the expression from.

        Returns:
            An expression in terms of ``P[...]``.
        """
        raise NotImplementedError


class LinearInIndex(ConstraintForm):
    """Straight line in slice index.

    What the hand-written scripts do:
    ``p_start + (p_end - p_start) * i / (n - 1)``. Correct when slices are
    evenly spaced in time, and wrong when they are not -- which is common,
    since eis intervals run ~3x longer than holds.
    """

    name = "linear_in_index"
    summary = "Straight line in slice index; endpoints are the two states."

    @classmethod
    def expression(cls, i: int, context: FormContext) -> str:
        f = context.fraction_by_index(i)
        return _interpolate(context.start_key, context.end_key, f)


class LinearInTime(ConstraintForm):
    """Straight line in elapsed time.

    Not cosmetically different from ``linear_in_index``: ``r*_eis_reduction``
    intervals are unequal in length, so index and time genuinely diverge.
    Prefer this whenever slice spacing is uneven.
    """

    name = "linear_in_time"
    summary = "Straight line in elapsed time; use when slice spacing is uneven."

    @classmethod
    def expression(cls, i: int, context: FormContext) -> str:
        f = context.fraction_by_time(i)
        return _interpolate(context.start_key, context.end_key, f)


class PiecewiseLinear(ConstraintForm):
    """Linear interpolation between K free knots.

    For a trajectory with structure that no closed form captures, without
    going all the way to one free parameter per slice.
    """

    name = "piecewise_linear"
    summary = "Linear interpolation between K free knots (`knots:`)."
    needs_endpoints = False

    @classmethod
    def validate_constraint(cls, constraint: Constraint) -> None:
        if constraint.knots < 2:
            raise ConstraintError("piecewise_linear needs `knots` >= 2")

    @classmethod
    def extra_parameters(cls, constraint: Constraint) -> list[ExtraParameter]:
        # Bounds are filled in by the resolver from the anchor parameter's own
        # range, which is why they are left wide here.
        return [
            ExtraParameter(
                f"knot{k}", 0.0, (-1e30, 1e30), f"knot {k} of {constraint.knots}"
            )
            for k in range(constraint.knots)
        ]

    @classmethod
    def expression(cls, i: int, context: FormContext) -> str:
        knots = [
            context.extra_keys[k] for k in sorted(context.extra_keys) if "knot" in k
        ]
        if len(knots) < 2:
            raise ConstraintError("piecewise_linear resolved fewer than 2 knots")

        position = context.fraction_by_time(i) * (len(knots) - 1)
        left = min(int(position), len(knots) - 2)
        weight = position - left
        if weight == 0.0:
            return f"P[{knots[left]!r}]"
        return f"P[{knots[left]!r}] + (P[{knots[left + 1]!r}] - P[{knots[left]!r}]) * {weight!r}"


class Exponential(ConstraintForm):
    """Exponential approach to the final value.

    ``p_end + (p_start - p_end) * exp(-(t - t0) / tau)``

    The natural form for a diffusive or first-order process, and ``tau``
    becomes a fitted physical quantity rather than something read off a plot.
    """

    name = "exponential"
    summary = "Exponential approach with a fitted time constant `tau`."

    @classmethod
    def extra_parameters(cls, constraint: Constraint) -> list[ExtraParameter]:
        low, high = constraint.tau or (1.0, 1.0e5)
        return [
            ExtraParameter(
                "tau", (low * high) ** 0.5, (low, high), "time constant, seconds"
            )
        ]

    @classmethod
    def expression(cls, i: int, context: FormContext) -> str:
        tau = context.extra_keys["tau"]
        elapsed = (context.times[i] - context.times[0]) if context.times else float(i)
        return (
            f"P[{context.end_key!r}] + (P[{context.start_key!r}] - P[{context.end_key!r}]) "
            f"* pmath.exp(-{elapsed!r} / P[{tau!r}])"
        )


class Logistic(ConstraintForm):
    """Sigmoidal transition with a fitted midpoint and width.

    ``p_start + (p_end - p_start) / (1 + exp(-(t - t_half) / w))``

    This is the direct payoff of the amplitude analysis: ``tnr-amplitude.md``
    reports that run 218389's a(t) is sigmoidal with an induction period, and
    this turns ``t_half`` and ``w`` into fitted parameters with uncertainties
    instead of features eyeballed off a plot.
    """

    name = "logistic"
    summary = "Sigmoid with fitted midpoint `t_half` and width `width`."

    @classmethod
    def extra_parameters(cls, constraint: Constraint) -> list[ExtraParameter]:
        t_low, t_high = constraint.t_half or (0.0, 1.0e5)
        w_low, w_high = constraint.width or (1.0, 1.0e4)
        return [
            ExtraParameter(
                "t_half",
                (t_low + t_high) / 2.0,
                (t_low, t_high),
                "transition midpoint, seconds",
            ),
            ExtraParameter(
                "width",
                (w_low * w_high) ** 0.5,
                (w_low, w_high),
                "transition width, seconds",
            ),
        ]

    @classmethod
    def expression(cls, i: int, context: FormContext) -> str:
        t_half = context.extra_keys["t_half"]
        width = context.extra_keys["width"]
        t = context.times[i] if context.times else float(i)
        return (
            f"P[{context.start_key!r}] + (P[{context.end_key!r}] - P[{context.start_key!r}]) "
            f"/ (1 + pmath.exp(-({t!r} - P[{t_half!r}]) / P[{width!r}]))"
        )


class Free(ConstraintForm):
    """One independent free parameter per slice.

    The most flexible and the most expensive: N slices cost N parameters, and
    the fit will happily use them to absorb noise. Justify it before using it.
    """

    name = "free"
    summary = "Independent per slice; costs one parameter per slice."
    needs_endpoints = False

    @classmethod
    def expression(cls, i: int, context: FormContext) -> str:
        raise ConstraintError(
            "`free` introduces a parameter per slice rather than an expression; "
            "the resolver handles it directly."
        )


class Fixed(ConstraintForm):
    """Pinned to a state's value throughout the series."""

    name = "fixed"
    summary = "Pinned to the `from` state's value; costs nothing."
    needs_endpoints = False

    @classmethod
    def validate_constraint(cls, constraint: Constraint) -> None:
        if not constraint.from_:
            raise ConstraintError(
                "form 'fixed' needs `from` to say which state to pin to"
            )

    @classmethod
    def expression(cls, i: int, context: FormContext) -> str:
        return f"P[{context.start_key!r}]"


#: Every available form, by name.
FORMS: dict[str, type[ConstraintForm]] = {
    form.name: form
    for form in (
        LinearInIndex,
        LinearInTime,
        PiecewiseLinear,
        Exponential,
        Logistic,
        Free,
        Fixed,
    )
}


def _interpolate(start_key: str | None, end_key: str | None, fraction: float) -> str:
    """Emit a linear interpolation, collapsing the trivial endpoints.

    Emitting ``p + (q - p) * 0.0`` at the endpoints would be correct but
    needlessly obscures a generated script a human has to read.
    """
    if fraction == 0.0:
        return f"P[{start_key!r}]"
    if fraction == 1.0:
        return f"P[{end_key!r}]"
    return f"P[{start_key!r}] + (P[{end_key!r}] - P[{start_key!r}]) * {fraction!r}"


def describe_forms() -> list[tuple[str, str]]:
    """Return ``(name, summary)`` for every form, for help output.

    Returns:
        Pairs sorted by name.
    """
    return sorted((name, form.summary) for name, form in FORMS.items())
