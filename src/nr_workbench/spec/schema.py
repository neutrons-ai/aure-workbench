"""Emit the JSON Schema for `nrw-model/1`.

Written to `.nrw/schema/nrw-model-1.json` at init and wired into
`.vscode/settings.json`, so a spec validates live in the editor and an agent
can read the schema as a file rather than inferring it from examples.

A test asserts the committed schema equals what this produces, so the two
cannot drift.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def build_schema() -> dict[str, Any]:
    """Return the JSON Schema for a model spec.

    Returns:
        The schema document.
    """
    from nr_workbench.spec.models import SCHEMA_VERSION, ModelSpec

    schema = ModelSpec.model_json_schema(by_alias=True)
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = SCHEMA_VERSION
    schema["description"] = (
        "A neutron reflectometry model: one layer stack, any number of "
        "steady-state states and time-resolved series, with functional "
        "constraints across a series."
    )
    return schema


def write_schema(path: Path) -> Path:
    """Write the schema to disk.

    Args:
        path: Destination file.

    Returns:
        The path written.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(build_schema(), indent=2) + "\n", encoding="utf-8")
    return path
