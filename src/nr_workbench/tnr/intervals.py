"""Interval-type helpers.

Ordering and styling by interval type is needed by both the metrics and the
plots, and neither concern should drag matplotlib into the other. Lifted from
``experiments-2025/tnr_chi2.py``; see ``upstream.toml``.
"""

from __future__ import annotations

import numpy as np

from nr_workbench.tnr.constants import (
    EIS_COLOR,
    HOLD_COLOR,
    MARKER_BY_TYPE,
    OTHER_COLOR,
    OTHER_MARKER,
)


def type_style(itype: str) -> tuple[str, str]:
    """(color, marker) for an interval_type, with a fallback for unknown types."""
    if itype == "hold":
        return HOLD_COLOR, MARKER_BY_TYPE["hold"]
    if itype == "eis":
        return EIS_COLOR, MARKER_BY_TYPE["eis"]
    return OTHER_COLOR, OTHER_MARKER


def ordered_types(types) -> list[str]:
    """Interval types present, with hold/eis first and the rest sorted."""
    types_arr = np.array(types)
    present = [t for t in ("hold", "eis") if (types_arr == t).any()]
    return present + sorted(set(types_arr.tolist()) - {"hold", "eis"})
