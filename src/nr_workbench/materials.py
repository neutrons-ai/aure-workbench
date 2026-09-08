"""Scattering length densities, computed rather than tabulated.

``periodictable`` (a refl1d dependency) does the physics: given a chemical
formula and a mass density it returns the neutron SLD. What it does not have is
a mass density for anything but an element, or any notion of a common name --
``formula("D2O").density`` is ``None`` and ``formula("quartz")`` raises.

So this module is two small things on top of it: a name-to-formula table and a
density table, both deliberately limited to what this repository's own skills
and sample specs actually name. It is **not** a materials database, and should
not grow into one. Anything outside it takes an explicit density:

    sld("Cu")                  # element: periodictable knows the density
    sld("Cu2O", density=6.0)   # compound: say what density you mean
    sld("C8H8", density=1.05)  # polystyrene, from formula and density

This replaces ``aure.database.materials``, which AuRE retired: the SLDs in a
fitted model come from its intake LLM, not from a table, so the table had no
consumer there. The arithmetic is short enough to own -- the same reasoning
that keeps AuRE's boundary-hit and BIC helpers reimplemented in
:mod:`nr_workbench.fitting.assess` rather than imported.
"""

from __future__ import annotations

#: Mass densities in g/cm3 for compounds periodictable has none for. Solvents
#: first, because contrast matching needs both ends of the H/D pair.
DENSITIES: dict[str, float] = {
    "H2O": 1.000,
    "D2O": 1.107,
    "C4H8O": 0.889,  # THF
    "C4D8O": 0.985,
    "C7H8": 0.867,  # toluene
    "C7D8": 0.943,
    "C8H8": 1.050,  # polystyrene
    "C8D8": 1.120,
    "C5H8O2": 1.180,  # PMMA
    "C5D8O2": 1.250,
    "SiO2": 2.200,
    "Al2O3": 3.980,
}

#: Common names for the materials above, lowercased.
ALIASES: dict[str, str] = {
    "water": "H2O",
    "light water": "H2O",
    "heavy water": "D2O",
    "silicon": "Si",
    "silicon wafer": "Si",
    "si": "Si",
    "quartz": "SiO2",
    "fused silica": "SiO2",
    "silica": "SiO2",
    "native oxide": "SiO2",
    "sapphire": "Al2O3",
    "alumina": "Al2O3",
    "copper": "Cu",
    "gold": "Au",
    "titanium": "Ti",
    "thf": "C4H8O",
    "dthf": "C4D8O",
    "d-thf": "C4D8O",
    "toluene": "C7H8",
    "d-toluene": "C7D8",
    "polystyrene": "C8H8",
    "ps": "C8H8",
    "dps": "C8D8",
    "d-ps": "C8D8",
    "pmma": "C5H8O2",
    "dpmma": "C5D8O2",
}


def resolve_formula(name: str) -> str:
    """Map a common name to a chemical formula, or return it unchanged."""
    return ALIASES.get(name.strip().lower(), name.strip())


def sld(name_or_formula: str, density: float | None = None) -> float:
    """Neutron SLD in 1e-6 per square angstrom.

    Args:
        name_or_formula: A common name (``"D2O"``, ``"quartz"``) or a chemical
            formula.
        density: Mass density in g/cm3. Required for any compound not in
            :data:`DENSITIES`; periodictable supplies it for elements.

    Returns:
        The real part of the SLD. The imaginary and incoherent parts are
        discarded -- refl1d models here are non-absorbing.

    Raises:
        ValueError: If the formula cannot be parsed, or no density is known
            and none was given.
    """
    from periodictable import formula as pt_formula
    from periodictable import neutron_sld

    formula = resolve_formula(name_or_formula)
    try:
        parsed = pt_formula(formula)
    except Exception as exc:
        raise ValueError(f"Cannot parse {name_or_formula!r} as a formula") from exc

    rho = density if density is not None else DENSITIES.get(formula)
    if rho is None:
        rho = getattr(parsed, "density", None)
    if rho is None:
        raise ValueError(
            f"No density known for {name_or_formula!r}; pass density=<g/cm3>. "
            f"periodictable has densities for elements only, and this module's "
            f"table covers {', '.join(sorted(DENSITIES))}."
        )

    real, _imaginary, _incoherent = neutron_sld(parsed, density=float(rho))
    return float(real)


def contrast_match_ratio(
    target_sld: float,
    *,
    protiated: str = "H2O",
    deuterated: str = "D2O",
) -> float:
    """Deuterated volume fraction whose mixture SLD matches *target_sld*.

    A two-solvent mixture interpolates linearly in SLD, so this inverts that
    line and clamps to the physically reachable range.

    Args:
        target_sld: The SLD to match, in 1e-6 per square angstrom.
        protiated: Protiated solvent, by name or formula.
        deuterated: Deuterated solvent, by name or formula.

    Returns:
        The deuterated volume fraction, between 0 and 1. A target outside the
        span the pair can reach clamps to the nearer end rather than raising --
        the caller asked which mixture is closest.
    """
    low, high = sld(protiated), sld(deuterated)
    if high == low:
        raise ValueError(
            f"{protiated!r} and {deuterated!r} have the same SLD; no mixture "
            f"can be matched against them."
        )
    return max(0.0, min(1.0, (target_sld - low) / (high - low)))


def mixture_sld(
    fraction_deuterated: float,
    *,
    protiated: str = "H2O",
    deuterated: str = "D2O",
) -> float:
    """SLD of a two-solvent mixture. The inverse of contrast_match_ratio."""
    low, high = sld(protiated), sld(deuterated)
    return low + float(fraction_deuterated) * (high - low)
