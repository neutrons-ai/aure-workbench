"""The parts of sample.md's shape that more than one reader depends on.

``sample.md`` is read by about twenty modules. Four of them read its
measurement table -- ISAAC's conditions (:mod:`nr_workbench.conditions`), the
reconciliation against file headers (:mod:`nr_workbench.reconcile`), adoption
into the experiment catalog, and the catalog's own guard against a second
table -- and each used to decide for itself which column is the run and which
the condition. They disagreed: a ``| Run | Type | Conditions |`` table gave
ISAAC a condition and gave adoption an empty one. What they must agree on
lives here.
"""

from __future__ import annotations

import re

#: A markdown table row: ``| 218393 | full Q | -0.5 mA/cm2 |``.
TABLE_ROW_RE = re.compile(r"^\s*\|(.+)\|\s*$")

#: Header cells that name the run column, lowercased.
RUN_HEADERS = ("run", "run number")

#: Header cells that name the condition column, lowercased.
CONDITION_HEADERS = ("condition", "conditions")

#: Header cells that name the measurement-type column, lowercased.
TYPE_HEADERS = ("type",)


def table_cells(line: str) -> list[str] | None:
    """The stripped cells of a table row, or ``None`` if *line* is not one."""
    match = TABLE_ROW_RE.match(line)
    if match is None:
        return None
    return [cell.strip() for cell in match.group(1).split("|")]


def column(header: list[str], names: tuple[str, ...]) -> int | None:
    """Index of the first header cell among *names*, or ``None``.

    Args:
        header: Lowercased header cells.
        names: Acceptable names, most preferred first.
    """
    for name in names:
        if name in header:
            return header.index(name)
    return None
