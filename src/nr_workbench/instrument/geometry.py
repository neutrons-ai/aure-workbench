"""BL-4B geometry, as a function of when the measurement was taken.

The dated table below traces back to ``lr_reduction/settings.json``.

The detector has not always been in the same place. It moved in on
2024-08-26 and back out on 2025-01-01, which changes the sample-detector and
source-detector distances by roughly half a metre. Anything computing an angle
or a resolution from pixel positions needs the values that were true on the day,
not today's -- and a run reduced with the wrong ones is wrong in a way that
looks like a real sample.

Only the dated table lives here. The NeXus event processing and peak fitting
that would surround it are deliberately **not** included: they need raw event
files this package never sees (``data/raw/`` is gitignored). If you need a
theta offset, compute it separately and record the number.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, datetime

#: Instrument settings, each a list of ``(effective_from, value)`` in date
#: order. Distances are metres as recorded upstream; :class:`Geometry`
#: exposes millimetres, which is what pixel arithmetic wants.
_SETTINGS: dict[str, list[tuple[str, float]]] = {
    "sample_detector_distance_m": [
        ("2014-10-10", 1.83),
        ("2024-08-26", 1.355),
        ("2025-01-01", 1.83),
    ],
    "source_detector_distance_m": [
        ("2014-10-10", 15.75),
        ("2024-08-26", 15.282),
        ("2025-01-01", 15.75),
    ],
    "s1_sample_distance_m": [("2014-10-10", 1.485)],
    "pixel_width_mm": [("2014-10-10", 0.70)],
    "xi_reference_mm": [("2014-10-10", 445.0)],
    "num_y_pixels": [("2014-10-10", 304.0)],
}

#: Before this, there is no recorded geometry.
EARLIEST = date(2014, 10, 10)

#: The standard REF_L angle settings for a full-Q measurement here, and the
#: single angle used for time-resolved runs. Not a physical constant -- a
#: convention this group follows -- but it is what `nrw model new` scaffolds.
STEADY_THETAS = (0.45, 1.2, 3.5)
TNR_THETA = 0.6


class GeometryError(Exception):
    """Raised when geometry is requested for a date with no recorded values."""


@dataclass(frozen=True)
class Geometry:
    """BL-4B geometry valid on one date.

    Attributes:
        on: The date these values apply to.
        sample_detector_mm: Sample-to-detector distance.
        source_detector_mm: Source-to-detector distance.
        s1_sample_mm: Slit 1 to sample distance.
        pixel_width_mm: Detector pixel pitch.
        xi_reference_mm: Reference xi motor position.
        num_y_pixels: Detector pixels in y.
    """

    on: date
    sample_detector_mm: float
    source_detector_mm: float
    s1_sample_mm: float
    pixel_width_mm: float
    xi_reference_mm: float
    num_y_pixels: int


def geometry_on(when: date | datetime | str) -> Geometry:
    """Return the geometry in force on a given date.

    Args:
        when: A date, datetime, or ISO-8601 string. A NeXus ``entry/start_time``
            can be passed straight in.

    Returns:
        The geometry valid on that date.

    Raises:
        GeometryError: If the date is unparsable or precedes :data:`EARLIEST`.
    """
    day = _as_date(when)
    if day < EARLIEST:
        raise GeometryError(
            f"No recorded BL-4B geometry for {day.isoformat()}; "
            f"the table starts at {EARLIEST.isoformat()}."
        )

    values = {key: _value_on(key, day) for key in _SETTINGS}
    return Geometry(
        on=day,
        sample_detector_mm=values["sample_detector_distance_m"] * 1000.0,
        source_detector_mm=values["source_detector_distance_m"] * 1000.0,
        s1_sample_mm=values["s1_sample_distance_m"] * 1000.0,
        pixel_width_mm=values["pixel_width_mm"],
        xi_reference_mm=values["xi_reference_mm"],
        num_y_pixels=int(values["num_y_pixels"]),
    )


def geometry_changes() -> list[date]:
    """Every date on which some geometry value changed.

    Useful for warning that a set of runs spans a configuration change, which
    is a thing that should never be co-refined without saying so.

    Returns:
        Sorted, de-duplicated change dates.
    """
    dates = {
        date.fromisoformat(stamp)
        for entries in _SETTINGS.values()
        for stamp, _ in entries
    }
    return sorted(dates)


def spans_a_change(first: date | datetime | str, last: date | datetime | str) -> bool:
    """Report whether two dates sit either side of a geometry change.

    Args:
        first: The earlier date.
        last: The later date.

    Returns:
        Whether the detector moved between them.
    """
    start, end = sorted((_as_date(first), _as_date(last)))
    return any(start < change <= end for change in geometry_changes())


def _value_on(key: str, day: date) -> float:
    """Look up the value of one setting on a date."""
    entries = _SETTINGS[key]
    stamps = [date.fromisoformat(stamp) for stamp, _ in entries]
    index = bisect_right(stamps, day) - 1
    if index < 0:
        raise GeometryError(
            f"No value for {key} on {day.isoformat()}; "
            f"the earliest recorded is {stamps[0].isoformat()}."
        )
    return entries[index][1]


def _as_date(when: date | datetime | str) -> date:
    """Coerce a date, datetime, or ISO-8601 string to a date."""
    if isinstance(when, datetime):
        return when.date()
    if isinstance(when, date):
        return when
    text = str(when).strip()
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        pass
    # NeXus start_time carries a zone; fromisoformat handles most, not all.
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError as exc:
        raise GeometryError(f"Cannot read {when!r} as a date: {exc}") from exc
