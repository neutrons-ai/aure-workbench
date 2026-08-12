"""Time-resolved neutron reflectometry change assessment.

Split out of a single 1992-line module with one Click command carrying ~60
options. The numerics were sound and are carried over byte-for-byte; only the
structure changed.

Three rules hold this layer together:

1. **Analysis functions take arrays and return dataclasses.** No I/O, no CLI,
   no matplotlib inside ``metrics/``. That is what makes them testable.
2. **The math did not change.** ``tests/test_tnr_characterization.py`` compares
   this package's ASCII output byte-for-byte against the original tool's.
3. **Output filenames are preserved**, so existing notes and habits still work.

Read the metrics in the order ``docs/notes.md`` prescribes: variogram (is
anything changing?), amplitude (how?), Q bands (where, and what kind?),
chi-squared (quote delta), then PCA/KL if one template is not enough.
"""

from __future__ import annotations

from nr_workbench.tnr.io import load_run
from nr_workbench.tnr.metrics.amplitude import (
    AmplitudeResult,
    analyze_amplitude,
    build_template,
)
from nr_workbench.tnr.metrics.chi2 import analyze_chi2, analyze_chi2_ref, analyze_delta2
from nr_workbench.tnr.metrics.decomposition import analyze_kl, analyze_pca
from nr_workbench.tnr.metrics.qbands import QBandResult, analyze_qbands
from nr_workbench.tnr.metrics.variogram import VariogramResult, analyze_variogram

__all__ = [
    "AmplitudeResult",
    "QBandResult",
    "VariogramResult",
    "analyze_amplitude",
    "analyze_chi2",
    "analyze_chi2_ref",
    "analyze_delta2",
    "analyze_kl",
    "analyze_pca",
    "analyze_qbands",
    "analyze_variogram",
    "build_template",
    "load_run",
]
