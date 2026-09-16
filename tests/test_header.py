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


# --------------------------------------------------------------------------
# The `new_reduction` dialect
# --------------------------------------------------------------------------

# REF_L's `new_reduction` pipeline writes `_autoreduction.dat`, whose header
# shares nothing with `# Meta:` and whose fourth column is **sigma** where the
# old one was FWHM. Before this was parsed, `read_header` returned
# `source="none"` with every field None -- indistinguishable from a file with
# no header at all -- and `nrw model new` went on to write `dq_is_fwhm: true`
# and the nominal angles under a comment claiming it had read them from the
# file.

# Trimmed from run 234277. The details that matter are preserved exactly:
# `Angles` has FOUR entries for THREE segments (segment 2 measured twice),
# `Config` is JSON (`null`, `false`) while `DB` and `NR_runs` are Python
# (`'single'`, `None`), and the angles are negative.
AUTORED = (
    "# NR_runs = [None, None, 234279]\n"
    '# Run Title: {"title": ["S1_air-234277-1.", "S1_air-234277-2.", '
    '"S1_air-234277-2.", "S1_air-234277-3."]}\n'
    "# DB = ['A1_Si.txt', 'A2_Si.txt', 'A3_Si.txt']\n"
    "# Normalize = False\n"
    '# Scaling factors = {"scale_factor": [1.0, 1.0, 1.0]}\n'
    "# Lambda Range = 2.65Å to 9.45Å\n"
    '# Angles: {"THS": [-0.45, -1.251, -1.251, -3.5], '
    '"THI": [-0.0, -0.0, -0.0, -0.0], '
    '"ThCen": [-0.45, -1.251, -1.251, -3.5]}\n'
    '# Config: {"experiment_id": "IPTS-37740", "RBnum": [null, null, 234279], '
    '"Normalize": false, "ThetaShift": [0, 0, 0], "qmin": 0.001, "qmax": 0.5, '
    '"dqbin": 0.015, "LambdaMinUse": 2.65, "LambdaMaxUse": 9.45}\n'
    "# columns = Q, R, dR, dQ (sigma)\n"
)


def write_autored(directory: Path, segment: int, subrun: int) -> Path:
    """Write one segment of run 234277 in the new dialect."""
    path = directory / f"REFL_234277_{segment}_{subrun}_autoreduction.dat"
    return write(path, AUTORED)


def test_autoreduction_dq_column_is_read_as_sigma(tmp_path: Path) -> None:
    """The whole point. `(sigma)` in parentheses, not `[FWHM]` in brackets."""
    header = read_header(write_autored(tmp_path, 1, 234277))

    assert header.dq_convention == "sigma"
    assert header.dq_is_fwhm is False


@pytest.mark.parametrize(
    ("segment", "subrun", "expected"),
    [(1, 234277, 0.45), (2, 234278, 1.251), (3, 234279, 3.5)],
)
def test_autoreduction_angle_is_matched_by_title_not_position(
    tmp_path: Path, segment: int, subrun: int, expected: float
) -> None:
    """Segment 3 is the one that catches positional indexing.

    `THS` has four entries because segment 2 was measured twice, so `THS[2]` is
    1.251 rather than 3.5 -- a factor of ~2.8 in Q, which fits cleanly to a
    wrong thickness instead of failing.
    """
    header = read_header(write_autored(tmp_path, segment, subrun))

    assert header.theta == pytest.approx(expected)


def test_autoreduction_angle_magnitude_is_taken(tmp_path: Path) -> None:
    """Stored negative on a back-reflection run; the probe wants the magnitude."""
    header = read_header(write_autored(tmp_path, 1, 234277))

    assert header.theta is not None and header.theta > 0


def test_autoreduction_does_not_round_to_the_nominal_angle(tmp_path: Path) -> None:
    """1.251 is the measurement. The nominal 1.2 is not in the file."""
    header = read_header(write_autored(tmp_path, 2, 234278))

    assert header.theta != pytest.approx(1.2)


def test_autoreduction_reports_its_own_source(tmp_path: Path) -> None:
    """`none` would mean "no header", which is what this used to report."""
    header = read_header(write_autored(tmp_path, 1, 234277))

    assert header.source == "autoreduction"


def test_autoreduction_reads_json_and_python_notation_on_different_lines(
    tmp_path: Path,
) -> None:
    """`Config` is JSON; `DB` is a Python list of single-quoted strings.

    Parsing with only one of them half-works, which is the dangerous outcome:
    `Angles` is valid under both, so the angle looks right while every field
    under `Config` silently degrades to None.
    """
    header = read_header(write_autored(tmp_path, 2, 234278))

    assert header.experiment == "IPTS-37740"  # JSON line
    assert header.norm_source == "A2_Si.txt"  # Python line
    assert header.theta == pytest.approx(1.251)  # valid as either


def test_autoreduction_per_segment_fields_track_the_segment(tmp_path: Path) -> None:
    first = read_header(write_autored(tmp_path, 1, 234277))
    third = read_header(write_autored(tmp_path, 3, 234279))

    assert (first.norm_source, third.norm_source) == ("A1_Si.txt", "A3_Si.txt")
    assert first.sequence_number == 1
    assert third.sequence_number == 3
    assert first.sequence_id == third.sequence_id == 234277


def test_autoreduction_does_not_report_requested_limits_as_measurements(
    tmp_path: Path,
) -> None:
    """`Config.qmin/qmax/dqbin` are what the reduction was asked for.

    Run 234277 requested qmax 0.5 and produced 0.278; dqbin is 0.015 against a
    median dQ/Q of 0.011. Surfacing either as `q_range` or `dq_over_q` would
    report a request as a measurement.
    """
    header = read_header(write_autored(tmp_path, 1, 234277))

    assert header.q_range == (None, None)
    assert header.dq_over_q is None


def test_a_malformed_autoreduction_header_still_yields_what_it_can(
    tmp_path: Path,
) -> None:
    """A new dialect will drop keys; that must not make the file unreadable."""
    path = tmp_path / "REFL_234277_1_234277_autoreduction.dat"
    write(path, '# Angles: {"THS": [-0.45]}\n# columns = Q, R, dR, dQ (sigma)\n')

    header = read_header(path)

    assert header.source == "autoreduction"
    assert header.dq_convention == "sigma"
    assert header.norm_source is None
    assert header.experiment is None


def test_an_unknown_dq_label_still_raises_in_the_new_dialect(tmp_path: Path) -> None:
    """The refusal to guess has to survive into the new syntax."""
    path = tmp_path / "REFL_234277_1_234277_autoreduction.dat"
    write(path, '# Angles: {"THS": [-0.45]}\n# columns = Q, R, dR, dQ (halfwidth)\n')

    with pytest.raises(HeaderError, match="halfwidth"):
        read_header(path)
