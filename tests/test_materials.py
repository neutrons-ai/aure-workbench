"""SLDs computed from periodictable, replacing AuRE's retired table.

The values asserted here are the ones five SKILL.md files quote to agents, so
they are a contract rather than a sample: if `sld("Cu")` stops being 6.55, the
skills are wrong and nothing else notices.
"""

from __future__ import annotations

import pytest

from nr_workbench.materials import contrast_match_ratio, mixture_sld, sld


@pytest.mark.parametrize(
    "query,kwargs,expected",
    [
        ("Cu", {}, 6.55),  # element: periodictable has the density
        ("D2O", {}, 6.37),  # compound: from this module's table
        ("H2O", {}, -0.56),
        ("quartz", {}, 3.47),  # alias -> SiO2
        ("silicon", {}, 2.07),  # alias -> Si
        ("heavy water", {}, 6.37),
        ("Cu2O", {"density": 6.0}, 5.36),  # explicit density
        ("C8H8", {"density": 1.05}, 1.41),  # polystyrene from formula
        ("C4H8O", {"density": 0.889}, 0.18),  # THF
    ],
)
def test_documented_slds(query, kwargs, expected) -> None:
    assert sld(query, **kwargs) == pytest.approx(expected, abs=0.01)


def test_a_compound_without_a_density_says_so() -> None:
    """periodictable returns None rather than raising, which would otherwise
    surface as a confusing TypeError deep inside neutron_sld."""
    with pytest.raises(ValueError, match="No density known"):
        sld("Cu2O")


def test_an_unparseable_formula_says_so() -> None:
    with pytest.raises(ValueError, match="Cannot parse"):
        sld("not a material")


def test_contrast_match_is_the_documented_ratio() -> None:
    """`contrast_match_ratio(2.07)  # 0.38 -> 38% D2O matches silicon`, from
    solvent-contrast-matching/SKILL.md."""
    assert contrast_match_ratio(2.07) == pytest.approx(0.38, abs=0.01)


def test_contrast_match_clamps_outside_the_reachable_span() -> None:
    """A target no mixture can reach returns the nearer end, not an error --
    the caller asked which mixture is closest."""
    assert contrast_match_ratio(-5.0) == 0.0
    assert contrast_match_ratio(9.9) == 1.0


def test_mixture_and_match_are_inverses() -> None:
    for fraction in (0.0, 0.25, 0.5, 0.75, 1.0):
        assert contrast_match_ratio(mixture_sld(fraction)) == pytest.approx(
            fraction, abs=1e-9
        )


def test_the_adapter_still_exports_the_agent_facing_names() -> None:
    """Five SKILL.md files say `from nr_workbench.aure_adapter import sld`."""
    from nr_workbench import aure_adapter

    assert aure_adapter.sld is sld
    assert aure_adapter.contrast_match_ratio is contrast_match_ratio


def test_materials_does_not_import_aure() -> None:
    """The point of the move: this arithmetic no longer costs an AuRE import."""
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from nr_workbench.materials import sld; sld('D2O'); "
            "assert 'aure' not in sys.modules, 'materials imported aure'",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
