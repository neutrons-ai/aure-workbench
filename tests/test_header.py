"""Reading the metadata a reduced REF_L file records about itself.

The incident angle was previously assumed from a hardcoded list. It is a field
in a JSON object at the top of the file, and theta feeds the resolution through
`dT = dq/q * tan(theta)` -- so a wrong one is absorbed into roughness rather
than raising, which is why these tests are specific about it.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from nr_workbench.instrument.header import (
    HeaderError,
    read_header,
    theta_for_run,
    thetas_for,
)

META = (
    '# Meta:{"wl_min": 2.75, "wl_max": 9.50, "q_min": 0.0104, "q_max": 0.0359, '
    '"theta": 0.007853609528660925, "start_time": "2025-04-20T13:42:43.1", '
    '"experiment": "IPTS-34347", "run_number": "218386", '
    '"run_title": "CuPt_d8-THF-1.", "norm_run": 218274, "dq_over_q": 0.0272, '
    '"sequence_number": 1, "sequence_id": 218386, '
    '"scaling_factors": {"a": 4.0, "err_a": 0}, "theta_offset": 0}'
)

TABLE = (
    "# DataRun   NormRun   TwoTheta(deg)  LambdaMin(A)\n"
    "# 218386    218274    0.899957       2.74977\n"
)


def write(path: Path, header: str, rows: str = "0.01 1.0 0.1 0.001\n") -> Path:
    """Write a reduced file with the given header."""
    path.write_text(
        header + ("\n" if header and not header.endswith("\n") else "") + rows
    )
    return path


def test_theta_is_converted_from_radians(tmp_path: Path) -> None:
    """0.00785 rad is 0.45 deg. Reading it as degrees is a silent disaster."""
    path = write(tmp_path / "a.txt", META)

    header = read_header(path)

    assert header.theta == pytest.approx(0.45, abs=1e-4)
    assert header.theta == pytest.approx(math.degrees(0.007853609528660925))
    assert header.source == "meta"


def test_every_recorded_field_is_read(tmp_path: Path) -> None:
    """These are what the spec scaffolder and the overlap check consume."""
    header = read_header(write(tmp_path / "a.txt", META))

    assert header.run == 218386
    assert header.norm_run == 218274
    assert header.sequence_number == 1
    assert header.sequence_id == 218386
    assert header.dq_over_q == pytest.approx(0.0272)
    assert header.scaling_factor == pytest.approx(4.0)
    assert header.experiment == "IPTS-34347"
    assert header.start_time.startswith("2025-04-20")


def test_the_fixed_width_table_is_a_fallback(tmp_path: Path) -> None:
    """TwoTheta is in degrees and twice theta, so it recovers the angle.

    It agrees with the JSON block to seven digits on the real files -- 0.4499785
    from the table against 0.4499786 from `theta` -- which is a useful check
    that the radians conversion is right.
    """
    header = read_header(write(tmp_path / "a.txt", TABLE))

    assert header.theta == pytest.approx(0.4499785)
    assert header.theta == pytest.approx(
        math.degrees(0.007853609528660925), abs=1e-6
    ), "the table and the JSON block must agree"
    assert header.norm_run == 218274
    assert header.source == "table"


def test_a_file_with_no_header_reports_absence_not_a_default(tmp_path: Path) -> None:
    """tNR slices have no header. Inventing one would be worse than saying so."""
    header = read_header(write(tmp_path / "slice.txt", ""))

    assert header.theta is None
    assert header.has_theta is False
    assert header.source == "none"


def test_a_corrupt_meta_line_raises(tmp_path: Path) -> None:
    """Truncated JSON must not be silently skipped to a fallback."""
    path = write(tmp_path / "a.txt", '# Meta:{"theta": 0.0078,')

    with pytest.raises(HeaderError, match="not valid JSON"):
        read_header(path)


def test_thetas_for_keeps_position_when_one_file_is_headerless(tmp_path: Path) -> None:
    """A missing angle must not shift the others onto the wrong segments."""
    a = write(tmp_path / "a.txt", META)
    b = write(tmp_path / "b.txt", "")
    c = write(tmp_path / "c.txt", META)

    angles = thetas_for([a, b, c])

    assert angles[1] is None, "the headerless file must not borrow a neighbour's angle"
    assert angles[0] == pytest.approx(0.44998, abs=1e-4)
    assert angles[2] == pytest.approx(0.44998, abs=1e-4)


def test_a_series_angle_comes_from_its_summed_dataset(tmp_path: Path) -> None:
    """A tNR run is also reduced whole into data/steady, and that file has a header.

    So the angle of a series is on disk after all -- which is how run 218389
    turns out to be 0.5997 deg rather than the 0.6 anyone would assume.
    """
    steady = tmp_path / "steady"
    steady.mkdir()
    meta = META.replace('"theta": 0.007853609528660925', '"theta": 0.010466')
    write(steady / "REFL_218389_4_218389_partial.txt", meta)

    theta, source = theta_for_run(steady, 218389)

    assert theta == pytest.approx(0.5997, abs=1e-3)
    assert source == "REFL_218389_4_218389_partial.txt"


def test_theta_for_run_is_silent_when_there_is_no_such_run(tmp_path: Path) -> None:
    steady = tmp_path / "steady"
    steady.mkdir()

    assert theta_for_run(steady, 999999) == (None, None)
    assert theta_for_run(tmp_path / "nope", 218389) == (None, None)


@pytest.mark.integration
def test_the_real_apr2025_headers_give_the_expected_angles() -> None:
    """The shipped parser against the actual instrument output."""
    steady = Path.home() / "git/experiments-2025/apr2025/data/steady"
    if not steady.is_dir():
        pytest.skip("experiments-2025 not checked out here")

    angles = {
        h.sequence_number: round(h.theta, 4)
        for h in (
            read_header(p)
            for p in sorted(steady.glob("REFL_218386_[123]_*_partial.txt"))
        )
    }

    assert angles == {1: 0.45, 2: 1.201, 3: 3.5003}
    assert theta_for_run(steady, 218389)[0] == pytest.approx(0.5997, abs=1e-4)


# --------------------------------------------------------------------------
# The dQ width convention
#
# FWHM and sigma differ by 2.355. A resolution wrong by that factor does not
# raise: the fit absorbs it into roughness and reports a confident wrong
# interface width. Every reduction so far writes FWHM and there is an intention
# to move to sigma, so it is read per file and never defaulted.
# --------------------------------------------------------------------------

COLUMNS_FWHM = (
    "# Q [1/Angstrom]        R                     dR                    dQ [FWHM]\n"
)
COLUMNS_SIGMA = (
    "# Q [1/Angstrom]        R                     dR                    dQ [sigma]\n"
)


def test_fwhm_column_label_is_read(tmp_path: Path) -> None:
    header = read_header(write(tmp_path / "a.txt", META + "\n" + COLUMNS_FWHM))

    assert header.dq_convention == "fwhm"
    assert header.dq_is_fwhm is True
    assert header.dq_column_label == "FWHM"


def test_sigma_column_label_is_read(tmp_path: Path) -> None:
    """The change the reduction intends to make must be picked up, not assumed."""
    header = read_header(write(tmp_path / "a.txt", META + "\n" + COLUMNS_SIGMA))

    assert header.dq_convention == "sigma"
    assert header.dq_is_fwhm is False


@pytest.mark.parametrize("label", ["sigma", "1-sigma", "SIGMA", "std", "stdev"])
def test_sigma_spellings_are_all_recognised(tmp_path: Path, label: str) -> None:
    columns = f"# Q [1/A]  R  dR  dQ [{label}]\n"
    header = read_header(write(tmp_path / "a.txt", META + "\n" + columns))

    assert header.dq_convention == "sigma"


def test_the_column_line_is_read_even_though_it_sits_below_meta(
    tmp_path: Path,
) -> None:
    """Regression: the reader used to return as soon as it found `# Meta:`.

    The column titles are written *after* the JSON block, so an early return
    meant the convention was never seen on any real file.
    """
    header = read_header(write(tmp_path / "a.txt", META + "\n" + COLUMNS_FWHM))

    assert header.source == "meta"
    assert header.theta == pytest.approx(0.45, abs=1e-4)
    assert header.dq_convention == "fwhm"


def test_a_file_that_does_not_say_reports_none_rather_than_fwhm(
    tmp_path: Path,
) -> None:
    """A tNR slice has no header. `None` forces the caller to decide."""
    header = read_header(write(tmp_path / "a.txt", ""))

    assert header.dq_convention is None
    assert header.dq_is_fwhm is None


def test_an_unrecognised_width_convention_raises(tmp_path: Path) -> None:
    """Guessing would scale every resolution by up to 2.355, silently."""
    columns = "# Q [1/A]  R  dR  dQ [half width]\n"

    with pytest.raises(HeaderError, match="half width"):
        read_header(write(tmp_path / "a.txt", META + "\n" + columns))


def test_as_dict_carries_the_convention(tmp_path: Path) -> None:
    payload = read_header(
        write(tmp_path / "a.txt", META + "\n" + COLUMNS_SIGMA)
    ).as_dict()

    assert payload["dq_convention"] == "sigma"
    assert payload["dq_column_label"] == "sigma"


@pytest.mark.integration
def test_the_real_files_state_fwhm() -> None:
    """Today's reduction. When this fails, the convention has changed."""
    steady = Path.home() / "git/experiments-2025/apr2025/data/steady"
    if not steady.is_dir():
        pytest.skip("experiments-2025 not checked out here")

    conventions = {
        read_header(p).dq_convention
        for p in sorted(steady.glob("REFL_218386_[123]_*_partial.txt"))
    }

    assert conventions == {"fwhm"}
