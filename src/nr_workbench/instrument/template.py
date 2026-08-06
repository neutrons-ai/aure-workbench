"""Reading the REF_L auto-reduction template.

Every reduced segment was divided by a direct-beam measurement, and which one
is recorded nowhere in the reduced ASCII -- only in the
``REF_L_<run>_auto_template.xml`` the reduction was driven from. That mapping
is the first thing worth knowing when two angle segments disagree about the
same sample, because segments normalised against direct beams taken at
different times are exactly the ones that come out on different scales.

Parsing it is a few lines of ElementTree. Keeping it here rather than inside
the overlap check means the check reports a *measurement*, and this supplies
the *explanation*, and neither depends on the other being available.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

#: Filename the reduction writes beside the data.
TEMPLATE_GLOB = "REF_L_*_auto_template.xml"


class TemplateError(Exception):
    """Raised when a template cannot be read or holds no usable entries."""


@dataclass(frozen=True)
class SegmentNormalisation:
    """How one segment of a measurement was normalised.

    Attributes:
        run: The sample run for this angle segment.
        direct_beam: The direct-beam run it was divided by.
        theta: Incident angle in degrees, when the template records one.
    """

    run: int
    direct_beam: int | None
    theta: float | None = None


def parse_template(path: Path) -> list[SegmentNormalisation]:
    """Read the run-to-direct-beam mapping from a reduction template.

    Args:
        path: Path to a ``REF_L_<run>_auto_template.xml``.

    Returns:
        One entry per ``RefLData`` block, in file order.

    Raises:
        TemplateError: If the file is unparsable or has no entries.
    """
    path = Path(path)
    try:
        root = ET.parse(path).getroot()  # noqa: S314 - local instrument output
    except (OSError, ET.ParseError) as exc:
        raise TemplateError(f"Cannot read {path}: {exc}") from exc

    found: list[SegmentNormalisation] = []
    for entry in root.iter("RefLData"):
        run = _first_int(entry, "data_sets")
        if run is None:
            continue
        found.append(
            SegmentNormalisation(
                run=run,
                direct_beam=_first_int(entry, "norm_dataset"),
                theta=_first_float(entry, "theta"),
            )
        )

    if not found:
        raise TemplateError(f"{path} contains no RefLData entries.")
    return found


def find_template(directory: Path, run: int) -> Path | None:
    """Locate the template that drove a run's reduction.

    The template is named after the *first* run of a measurement, so a search
    for segment 218388's template by its own number finds nothing. Every
    template in the directory is read instead, and the one listing this run is
    returned.

    Args:
        directory: Directory to search.
        run: Any run number appearing in the template.

    Returns:
        The template path, or ``None`` if no template mentions the run.
    """
    directory = Path(directory)
    if not directory.is_dir():
        return None

    exact = directory / f"REF_L_{run}_auto_template.xml"
    if exact.is_file():
        return exact

    for candidate in sorted(directory.glob(TEMPLATE_GLOB)):
        try:
            entries = parse_template(candidate)
        except TemplateError:
            continue
        if any(entry.run == run for entry in entries):
            return candidate
    return None


def direct_beams_for(directory: Path, run: int) -> dict[int, int | None]:
    """Map each segment run of a measurement to its direct-beam run.

    Args:
        directory: Directory holding the templates.
        run: The measurement's first run number.

    Returns:
        ``{segment_run: direct_beam_run}``, empty when no template is found.
    """
    template = find_template(directory, run)
    if template is None:
        return {}
    try:
        entries = parse_template(template)
    except TemplateError:
        return {}
    return {entry.run: entry.direct_beam for entry in entries}


def _first_int(entry: ET.Element, tag: str) -> int | None:
    """Read the first integer from a comma-separated element."""
    text = _text(entry, tag)
    if text is None:
        return None
    head = text.split(",")[0].strip()
    try:
        return int(head)
    except ValueError:
        return None


def _first_float(entry: ET.Element, tag: str) -> float | None:
    """Read a float from an element."""
    text = _text(entry, tag)
    if text is None:
        return None
    try:
        return float(text.split(",")[0].strip())
    except ValueError:
        return None


def _text(entry: ET.Element, tag: str) -> str | None:
    """Return an element's stripped text, or ``None`` when absent or empty."""
    element = entry.find(tag)
    if element is None or element.text is None:
        return None
    text = element.text.strip()
    return text or None
