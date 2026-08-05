"""Constants shared across the tNR analyses.

Lifted verbatim from ``experiments-2025/tnr_chi2.py``; see ``upstream.toml``.
"""

from __future__ import annotations

HOLD_COLOR = "#1f77b4"  # blue
EIS_COLOR = "#d62728"  # red
OTHER_COLOR = "#7f7f7f"  # grey

MARKER_BY_TYPE = {"hold": "o", "eis": "s"}
OTHER_MARKER = "^"

Z68 = 1.0  # 68.27% two-sided
Z95 = 1.959963984540054  # 95.00% two-sided

DEFAULT_TEMPLATE_SMOOTH = 9
DEFAULT_N_QBANDS = 4
DEFAULT_N_LAG_BINS = 6

# E[max(0, g^2 - 1)] for g ~ N(0,1) == 2*phi(1) == sqrt(2/(pi*e)).
# This is the per-bin bias introduced by clipping noise-negative bins
# elementwise in the delta^2 statistic; see analyze_delta2.
CLIP_BIAS = 0.4839414490382867
