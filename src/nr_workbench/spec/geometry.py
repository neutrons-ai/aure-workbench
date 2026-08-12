"""Which side the beam enters from, and whether the stack says so.

refl1d has exactly one mechanism for this and it is the **order of the stack**:
the last entry is the medium the neutron is incident from. Verified rather than
assumed --- ``D2O | Si`` totally reflects below the Si/D2O critical edge
(Qc = 0.0147) and ``Si | D2O`` does not reflect at all.

That single fact was expensive to rediscover. Two samples in the reference
beamtime each burned their first two fits on an inverted stack --- chi-squared
105 and 168 on one, 139 and 162 on the other --- because the spec described the
stack as "ambient first, substrate last". In a solid/liquid cell the *ambient*
end is the backing and the *substrate* is where the beam comes in, so that
wording says the opposite of the physics for exactly the geometry this beamline
mostly runs.

``probe.back_reflection`` made it worse by looking like the fix. It set nothing:
codegen emitted a comment and no behaviour. An agent could declare the geometry,
believe it was handled, and still be fitting the stack upside down.

So the rule here is: **ordering is the mechanism, and ``back_reflection`` is an
assertion about it.** Declaring it does not change the model; it makes a
contradiction between what you said and how you ordered the stack fail before
the fit rather than after two of them.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Materials a beam is normally incident *through* --- a cell window or wafer.
#: Lower-cased, matched against both the layer name and its material key.
SUBSTRATE_LIKE = frozenset(
    {
        "si",
        "silicon",
        "sio2",
        "quartz",
        "glass",
        "sapphire",
        "al2o3",
        "alumina",
        "ge",
        "germanium",
        "mica",
    }
)

#: Media a sample sits *in*. A solvent at the incident end means the beam
#: entered from the liquid side, which is a front-reflection measurement.
FLUID_LIKE = frozenset(
    {
        "air",
        "vacuum",
        "d2o",
        "h2o",
        "water",
        "buffer",
        "thf",
        "dthf",
        "hthf",
        "toluene",
        "ethanol",
        "methanol",
        "acetone",
        "cyclohexane",
        "electrolyte",
    }
)


@dataclass(frozen=True)
class Geometry:
    """What the stack order says about where the beam enters.

    Attributes:
        incident: Name of the layer the neutron is incident from --- the last
            stack entry, because that is refl1d's convention.
        backing: Name of the far side, the first stack entry.
        back_reflection: True when the incident end looks like a substrate, so
            the beam is entering through the wafer or window. ``None`` when
            neither end is recognised and the stack cannot say.
    """

    incident: str
    backing: str
    back_reflection: bool | None


def _kind(layer: Any) -> str:
    """Classify one end of the stack as substrate-like, fluid-like or unknown."""
    for token in (getattr(layer, "material", None), getattr(layer, "name", "")):
        lowered = str(token or "").lower()
        if lowered in SUBSTRATE_LIKE:
            return "substrate"
        if lowered in FLUID_LIKE:
            return "fluid"
    return "unknown"


def read_geometry(spec: Any) -> Geometry | None:
    """Infer the measurement geometry from the stack's own order.

    Args:
        spec: A parsed ``ModelSpec``.

    Returns:
        The geometry, or ``None`` for a stack too short to have two ends.
    """
    layers = list(getattr(spec, "stack", None) or [])
    if len(layers) < 2:
        return None

    backing, incident = layers[0], layers[-1]
    first, last = _kind(backing), _kind(incident)

    # Substrate-like at the incident end, or fluid-like at the backing end, both
    # mean the beam came in through the wafer. Either signal alone is enough;
    # requiring both would give up on a stack that names only one end usefully.
    if (last == "substrate" and first != "substrate") or (
        first == "fluid" and last != "fluid"
    ):
        implied: bool | None = True
    elif (first == "substrate" and last != "substrate") or (
        last == "fluid" and first != "fluid"
    ):
        implied = False
    else:
        implied = None

    return Geometry(
        incident=str(getattr(incident, "name", "")),
        backing=str(getattr(backing, "name", "")),
        back_reflection=implied,
    )


def critical_edge_side(spec: Any, table: Any, root: Path) -> str | None:
    """Which ordering the measured critical edge supports, if it can tell.

    Total reflection happens only when the beam goes from low SLD to high, so
    the presence of a plateau at low Q is itself a statement about which medium
    the neutron started in. One ordering predicts an edge and the other predicts
    none; the data says which happened. This is the check the reference analysis
    did by hand --- "Qc = 0.01515 implies a backing SLD of 6.6, i.e. the Cu" ---
    after the two wasted fits rather than before them.

    Deliberately reads only the first data file with numpy, so validation gains
    no dependency on AuRE's feature extraction for a question this simple.

    Args:
        spec: A parsed ``ModelSpec``.
        table: The resolved parameter table, for a measurement to read.
        root: Project root.

    Returns:
        ``"as ordered"``, ``"reversed"``, or ``None`` when the data cannot say
        --- no plateau either way, an unreadable file, or SLDs too close to
        distinguish.
    """
    import numpy as np

    geometry = read_geometry(spec)
    if geometry is None:
        return None

    rho = {}
    for layer in getattr(spec, "stack", None) or []:
        material = (getattr(spec, "materials", None) or {}).get(
            getattr(layer, "material_key", "")
        )
        value = getattr(material, "rho", None)
        if value is not None:
            rho[str(getattr(layer, "name", ""))] = float(value)

    # Both orderings usually predict *a* plateau -- in a solid/liquid cell the
    # metal outranks Si and D2O alike -- so presence cannot discriminate. The
    # position can, and by a wide margin: Si -> Cu puts the edge at Qc = 0.0150
    # while D2O -> Cu puts it at 0.0048, a factor of three.
    def predicted_qc(incident_layer: str) -> float | None:
        incident = rho.get(incident_layer)
        if incident is None:
            return None
        beyond = [v for k, v in rho.items() if k != incident_layer]
        if not beyond or max(beyond) <= incident:
            return None  # nothing to totally reflect from
        return float(np.sqrt(16 * np.pi * (max(beyond) - incident) * 1e-6))

    forward_qc = predicted_qc(geometry.incident)
    reverse_qc = predicted_qc(geometry.backing)

    measurement = next(
        (m for group in table.measurements.values() for m in group), None
    )
    if measurement is None:
        return None
    try:
        data = np.loadtxt(Path(root) / measurement.file, ndmin=2)
        q, r = data[:, 0], data[:, 1]
    except (OSError, ValueError, IndexError):
        return None
    order = np.argsort(q)
    q, r = q[order], r[order]
    if len(q) < 20 or r[0] <= 0:
        return None

    # Whether an edge would be *visible* is itself most of the answer. A
    # D2O -> Cu contrast of 0.19 puts Qc at 0.0031, below where REF_L starts, so
    # that ordering predicts no plateau at all -- while Si -> Cu predicts one at
    # 0.0150, right in the middle of the lowest-angle segment. When the two
    # orderings differ on whether there is a plateau, the data settles it
    # without needing to measure where the edge sits.
    def visible(qc: float | None) -> bool:
        return qc is not None and q[0] <= qc <= q[-1]

    forward_visible, reverse_visible = visible(forward_qc), visible(reverse_qc)

    # A plateau is a flat, high stretch at low Q well above the falloff. Judged
    # against the lowest-Q level rather than 1, so an unnormalised segment
    # sitting at 0.8 -- the usual state of the lowest-angle segment -- works.
    low, high = r[q < np.quantile(q, 0.12)], r[q > np.quantile(q, 0.55)]
    if low.size < 5 or high.size < 5 or np.median(low) <= 0:
        return None
    has_edge = bool(
        np.median(low) > 10 * np.median(high)
        and np.std(low) < 0.4 * np.median(low)
        and np.min(r) < 0.5 * np.median(low)
    )

    # Only a plateau that is *there* counts. Its absence has too many other
    # causes -- a segment whose Q starts above any edge, a mis-normalised
    # curve, a stack that is wrong in some other way -- and "reverse your
    # stack" is too strong a thing to say on the strength of not seeing
    # something. A fit of the 1.2 and 3.5 degree segments alone shows no
    # plateau and never should.
    if not has_edge:
        return None

    if forward_visible != reverse_visible:
        return "as ordered" if forward_visible else "reversed"

    if not (forward_visible and reverse_visible):
        return None  # neither ordering explains the edge that is there

    # Both predict a visible edge: fall back to comparing where it actually is,
    # in log space because these differ by factors rather than by amounts.
    plateau = float(np.median(low))
    below = np.flatnonzero(r < 0.5 * plateau)
    if below.size == 0:
        return None
    measured = float(q[below[0]])
    forward_gap = abs(np.log(measured / float(forward_qc)))
    reverse_gap = abs(np.log(measured / float(reverse_qc)))
    if abs(forward_gap - reverse_gap) < 0.4:
        return None
    return "as ordered" if forward_gap < reverse_gap else "reversed"
