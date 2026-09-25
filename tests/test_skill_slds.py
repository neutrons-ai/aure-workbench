"""Every SLD a SKILL.md quotes must be the number the physics gives.

**Tolerance follows the number's job, because the jobs differ.**

Most SLDs in these skills only *seed* a fitted parameter. AuRE retired its own
materials table on exactly that reasoning: with the formula known, +/-1 in SLD
needs the density to about 15% for a deuterated species and is essentially
unconstrained for anything protiated, so chasing the second decimal of a
starting value is effort spent where it cannot matter.

A few SLDs do something else: they *distinguish* two hypotheses. CuO at 6.46
against copper at 6.55 is what says cupric oxide is nearly invisible to this
technique; a table with CuO at 5.0 said the opposite, and that version reached a
published conclusion before it was caught. Those values have to be right to
better than the gap they are being read across.

So two tolerances, and each row declares which it is. Six values had drifted
when this guard was written -- d4-PEO by 0.72, d8-PMMA by 0.39, d6-ethanol and
d4-PE by 0.2, d8-PS by 0.07, d12-cyclohexane by 0.02 -- and the loose tolerance
still catches the four that were real errors rather than rounding.

Computed from `periodictable` directly, so this keeps working now that
`nr_workbench.materials` is retired.

**Adding an SLD to a skill means adding a row here.**
"""

from __future__ import annotations

import pytest
from periodictable import formula as pt_formula
from periodictable import neutron_sld

#: A value read across a gap to tell two things apart. Must be right to better
#: than that gap; the copper oxides are separated by 0.1 from copper itself.
DISTINGUISHES = 0.05

#: A value that seeds a fitted parameter. Wrong by less than this changes no
#: decision, and the fit moves it anyway.
SEEDS = 0.15

#: (skill, label, formula, density g/cm3, value quoted in the SKILL.md, tolerance)
QUOTED: list[tuple[str, str, str, float, float, float]] = [
    # metal-oxide-interfaces -- the copper system is read across small gaps.
    ("metal-oxide-interfaces", "Cu", "Cu", 8.96, 6.55, DISTINGUISHES),
    ("metal-oxide-interfaces", "CuO", "CuO", 6.31, 6.46, DISTINGUISHES),
    ("metal-oxide-interfaces", "Cu2O", "Cu2O", 6.00, 5.36, DISTINGUISHES),
    ("metal-oxide-interfaces", "Cu(OH)2", "CuH2O2", 3.37, 2.46, SEEDS),
    ("metal-oxide-interfaces", "Ti", "Ti", 4.506, -1.91, SEEDS),
    ("metal-oxide-interfaces", "TiO2", "TiO2", 4.23, 2.63, SEEDS),
    ("metal-oxide-interfaces", "Cr", "Cr", 7.19, 3.03, SEEDS),
    ("metal-oxide-interfaces", "Au", "Au", 19.3, 4.66, SEEDS),
    ("metal-oxide-interfaces", "SiO2", "SiO2", 2.196, 3.47, SEEDS),
    ("metal-oxide-interfaces", "Si", "Si", 2.329, 2.07, DISTINGUISHES),
    # polymer-films -- starting points, quoted to one decimal.
    ("polymer-films", "hPS", "C8H8", 1.050, 1.4, SEEDS),
    ("polymer-films", "d8-PS", "C8D8", 1.120, 6.4, SEEDS),
    ("polymer-films", "PMMA", "C5H8O2", 1.180, 1.1, SEEDS),
    ("polymer-films", "d8-PMMA", "C5D8O2", 1.250, 6.8, SEEDS),
    ("polymer-films", "PEO", "C2H4O", 1.130, 0.6, SEEDS),
    ("polymer-films", "d4-PEO", "C2D4O", 1.233, 7.1, SEEDS),
    ("polymer-films", "PE", "C2H4", 0.940, -0.3, SEEDS),
    ("polymer-films", "d4-PE", "C2D4", 1.075, 8.1, SEEDS),
    ("polymer-films", "PDMS", "C2H6OSi", 0.970, 0.1, SEEDS),
    # solvent-contrast-matching -- H2O and D2O anchor the contrast arithmetic.
    ("solvent-contrast-matching", "H2O", "H2O", 1.000, -0.56, DISTINGUISHES),
    ("solvent-contrast-matching", "D2O", "D2O", 1.107, 6.36, DISTINGUISHES),
    ("solvent-contrast-matching", "THF", "C4H8O", 0.889, 0.18, SEEDS),
    ("solvent-contrast-matching", "d8-THF", "C4D8O", 0.985, 6.35, SEEDS),
    ("solvent-contrast-matching", "toluene", "C7H8", 0.867, 0.94, SEEDS),
    ("solvent-contrast-matching", "d8-toluene", "C7D8", 0.943, 5.66, SEEDS),
    ("solvent-contrast-matching", "cyclohexane", "C6H12", 0.779, -0.28, SEEDS),
    ("solvent-contrast-matching", "d12-cyclohexane", "C6D12", 0.890, 6.68, SEEDS),
    ("solvent-contrast-matching", "ethanol", "C2H6O", 0.789, -0.34, SEEDS),
    ("solvent-contrast-matching", "d6-ethanol", "C2D6O", 0.878, 6.00, SEEDS),
    ("solvent-contrast-matching", "methanol", "CH4O", 0.792, -0.37, SEEDS),
    ("solvent-contrast-matching", "d4-methanol", "CD4O", 0.888, 5.80, SEEDS),
]


@pytest.mark.parametrize(
    "skill,label,formula,density,quoted,tolerance",
    QUOTED,
    ids=[f"{s}:{lbl}" for s, lbl, _f, _d, _q, _t in QUOTED],
)
def test_quoted_sld_matches_the_physics(
    skill: str,
    label: str,
    formula: str,
    density: float,
    quoted: float,
    tolerance: float,
) -> None:
    computed, _imag, _incoh = neutron_sld(pt_formula(formula), density=density)
    assert computed == pytest.approx(quoted, abs=tolerance), (
        f"{skill} quotes {label} = {quoted}, but {formula} at {density} g/cm3 "
        f"gives {computed:.3f}. Fix the skill, or the density if that is what "
        f"is wrong."
    )


def test_the_deuteration_shift_rule_reproduces_the_table() -> None:
    """polymer-films states a rule, not just numbers; the rule must hold.

    Deuterating adds (hydrogen number density) x (b_c(D) - b_c(H)) to the SLD.
    That is what makes protiation, not density, the thing worth checking twice.
    """
    avogadro = 6.02214076e23
    delta_b = 10.409e-5  # Angstrom
    cases = [("C8H8", 1.050, "C8D8", 1.120, 8), ("C2H4", 0.940, "C2D4", 1.075, 4)]
    for h_formula, h_density, d_formula, d_density, hydrogens in cases:
        h_sld = neutron_sld(pt_formula(h_formula), density=h_density)[0]
        d_sld = neutron_sld(pt_formula(d_formula), density=d_density)[0]
        mass = pt_formula(h_formula).mass
        n_hydrogen = hydrogens * h_density * avogadro / mass / 1e24
        predicted = n_hydrogen * delta_b * 1e6
        assert predicted == pytest.approx(d_sld - h_sld, abs=0.15), h_formula


def test_the_register_covers_every_skill_that_tabulates_slds() -> None:
    """Guards the guard: a register that lost a skill would pass vacuously."""
    assert {row[0] for row in QUOTED} == {
        "metal-oxide-interfaces",
        "polymer-films",
        "solvent-contrast-matching",
    }


def test_the_check_needs_nothing_from_this_package() -> None:
    """`nr_workbench.materials` is retired; this must not have followed it."""
    computed, _i, _n = neutron_sld(pt_formula("D2O"), density=1.107)
    assert computed == pytest.approx(6.37, abs=DISTINGUISHES)
