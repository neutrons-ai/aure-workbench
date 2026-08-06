"""BL-4B specifics: the dated geometry table and the reduction template."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest

from nr_workbench.instrument.geometry import (
    EARLIEST,
    GeometryError,
    geometry_changes,
    geometry_on,
    spans_a_change,
)
from nr_workbench.instrument.template import (
    TemplateError,
    direct_beams_for,
    find_template,
    parse_template,
)

TEMPLATE = """<?xml version="1.0"?>
<Reduction>
  <RefLData><data_sets>218386</data_sets><norm_dataset>218274</norm_dataset>
    <theta>0.45</theta></RefLData>
  <RefLData><data_sets>218387</data_sets><norm_dataset>218275</norm_dataset>
    <theta>1.2</theta></RefLData>
  <RefLData><data_sets>218388</data_sets><norm_dataset>218338</norm_dataset>
    <theta>3.5</theta></RefLData>
</Reduction>
"""


# --------------------------------------------------------------------------
# Geometry
# --------------------------------------------------------------------------


def test_the_detector_moved_in_august_2024() -> None:
    """The sample-detector distance is not a constant.

    It changed on 2024-08-26 and changed back on 2025-01-01. A run reduced
    with the wrong distance is wrong in a way that looks like a real sample,
    which is why this table exists at all.
    """
    before = geometry_on("2024-08-01")
    during = geometry_on("2024-09-01")
    after = geometry_on("2025-04-15")

    assert before.sample_detector_mm == pytest.approx(1830.0)
    assert during.sample_detector_mm == pytest.approx(1355.0)
    assert after.sample_detector_mm == pytest.approx(1830.0)


def test_the_apr2025_beamtime_used_the_restored_geometry() -> None:
    """The data this project is built around was taken after the move back."""
    geometry = geometry_on("2025-04-15")

    assert geometry.sample_detector_mm == pytest.approx(1830.0)
    assert geometry.source_detector_mm == pytest.approx(15750.0)


def test_a_change_takes_effect_on_its_own_date() -> None:
    """Boundaries are inclusive of the change date, not the day after."""
    assert geometry_on("2024-08-26").sample_detector_mm == pytest.approx(1355.0)
    assert geometry_on("2024-08-25").sample_detector_mm == pytest.approx(1830.0)


def test_geometry_before_the_table_is_an_error_not_a_guess() -> None:
    """Extrapolating backwards would invent a configuration."""
    with pytest.raises(GeometryError, match="table starts at"):
        geometry_on("2010-01-01")


@pytest.mark.parametrize(
    "value",
    [
        date(2025, 4, 15),
        datetime(2025, 4, 15, 12, 0),
        "2025-04-15",
        "2025-04-15T12:00:00",
        "2025-04-15T12:00:00Z",
    ],
)
def test_dates_are_accepted_in_the_forms_they_arrive_in(value: object) -> None:
    """A NeXus `entry/start_time` should go straight in."""
    assert geometry_on(value).sample_detector_mm == pytest.approx(1830.0)  # type: ignore[arg-type]


def test_an_unparsable_date_says_so() -> None:
    with pytest.raises(GeometryError, match="Cannot read"):
        geometry_on("last tuesday")


def test_spans_a_change_detects_a_straddled_move() -> None:
    """Co-refining across a detector move is a thing worth refusing to do quietly."""
    assert spans_a_change("2024-08-01", "2024-09-01")
    assert spans_a_change("2024-12-01", "2025-02-01")
    assert not spans_a_change("2025-04-01", "2025-04-20")
    assert not spans_a_change("2024-09-01", "2024-12-01")


def test_change_dates_are_sorted_and_start_at_the_earliest() -> None:
    changes = geometry_changes()

    assert changes == sorted(changes)
    assert changes[0] == EARLIEST


# --------------------------------------------------------------------------
# Reduction template
# --------------------------------------------------------------------------


def test_template_maps_each_segment_to_its_direct_beam(tmp_path: Path) -> None:
    """Which direct beam normalised a segment is recorded only here.

    It is absent from the reduced ASCII, so without the template there is no
    way to tell whether two segments were divided by the same reference.
    """
    path = tmp_path / "REF_L_218386_auto_template.xml"
    path.write_text(TEMPLATE, encoding="utf-8")

    entries = parse_template(path)

    assert [(e.run, e.direct_beam) for e in entries] == [
        (218386, 218274),
        (218387, 218275),
        (218388, 218338),
    ]
    assert entries[2].theta == pytest.approx(3.5)


def test_a_template_is_found_by_a_run_it_merely_mentions(tmp_path: Path) -> None:
    """Templates are named after the first run, not every run they cover.

    Searching for segment 218388's template by its own number finds nothing,
    so every template has to be read.
    """
    path = tmp_path / "REF_L_218386_auto_template.xml"
    path.write_text(TEMPLATE, encoding="utf-8")

    assert find_template(tmp_path, 218386) == path
    assert find_template(tmp_path, 218388) == path
    assert find_template(tmp_path, 999999) is None


def test_direct_beams_are_empty_rather_than_fatal_when_absent(tmp_path: Path) -> None:
    """Most projects will not have the template; that must not break a check."""
    assert direct_beams_for(tmp_path, 218386) == {}
    assert direct_beams_for(tmp_path / "nope", 218386) == {}


def test_a_malformed_template_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "REF_L_1_auto_template.xml"
    path.write_text("<Reduction><unclosed>", encoding="utf-8")

    with pytest.raises(TemplateError, match="Cannot read"):
        parse_template(path)


def test_a_template_with_no_entries_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "REF_L_1_auto_template.xml"
    path.write_text("<Reduction></Reduction>", encoding="utf-8")

    with pytest.raises(TemplateError, match="no RefLData"):
        parse_template(path)


@pytest.mark.integration
def test_the_real_apr2025_template_parses() -> None:
    """The shipped parser must handle the actual instrument output.

    Segments 1 and 2 were normalised against adjacent direct beams (218274,
    218275); segment 3 against 218338, measured much later. That is where to
    look first when `nrw data overlap` reports the 3.5 deg segment 27.6% out.
    """
    real = (
        Path.home()
        / "git/experiments-2025/jen-apr2025/data/steady"
        / "REF_L_218386_auto_template.xml"
    )
    if not real.is_file():
        pytest.skip("experiments-2025 not checked out here")

    beams = direct_beams_for(real.parent, 218386)

    assert beams == {218386: 218274, 218387: 218275, 218388: 218338}
