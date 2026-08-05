"""The `nrw-model/1` model spec: schema, resolution, and validation.

The centrepiece of the package. One schema covers steady-state co-refinement
*and* time-resolved series with functional constraints -- no upstream tool has
a time axis, so bolting one on would have fractured the schema.

Layers:

* :mod:`~nr_workbench.spec.models` -- the pydantic schema.
* :mod:`~nr_workbench.spec.constraints` -- the functional-form registry.
* :mod:`~nr_workbench.spec.resolve` -- spec to parameter table, pure Python.
* :mod:`~nr_workbench.spec.validate` -- the checks a schema cannot express.
* :mod:`~nr_workbench.spec.schema` -- JSON Schema emission for the editor.
"""

from __future__ import annotations

from nr_workbench.spec.models import ModelSpec, SpecError, load_spec
from nr_workbench.spec.resolve import ParameterTable, build_table, discover_measurements
from nr_workbench.spec.validate import ValidationReport, validate_spec

__all__ = [
    "ModelSpec",
    "ParameterTable",
    "SpecError",
    "ValidationReport",
    "build_table",
    "discover_measurements",
    "load_spec",
    "validate_spec",
]
