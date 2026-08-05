"""CLI command implementations.

Each module here is imported lazily by :mod:`nr_workbench.cli` so that
``nrw --help`` stays fast. Keep heavy imports (refl1d, aure, matplotlib) inside
functions, not at module scope.
"""

from __future__ import annotations
