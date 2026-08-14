"""Emit the JSON Schema for `nrw-model/1`.

The schema is *derived* from the pydantic models in
:mod:`nr_workbench.spec.models`, which are the only authority on what a spec
may contain. Two copies of it are written to disk:

* ``.nrw/schema/nrw-model-1.json`` in every project, by `nrw init` (and again
  by `nrw model schema`). ``.vscode/settings.json`` maps it onto
  ``samples/*/models/*.yaml``, so a spec validates live in the editor.
* ``skills/reflectometry/nrw-model-spec/assets/nrw-model-1.json`` in this
  package, so the skill can hand an agent the schema as a file.

Both are generated from :func:`schema_bytes`. The skill asset was previously
hand-maintained and drifted: it lost ``Constraint.endpoint_range`` (and
``Trim``, ``probe.dq_scale``, ``per: angle``) while still declaring
``additionalProperties: false``, so anything validating against it *rejected
valid specs*. A reader who trusted it concluded ``endpoint_range`` was
unsupported and designed around its absence.

``tests/test_spec.py::test_the_bundled_schema_asset_matches_the_live_models``
now fails on any such drift; ``python tools/regen_schema_asset.py`` fixes it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

#: Filename used for every on-disk copy of the schema, in a project and in the
#: skill asset alike. `.vscode/settings.json` refers to it by this name.
SCHEMA_FILENAME = "nrw-model-1.json"

#: Path of the project copy, relative to the project root.
PROJECT_SCHEMA_RELPATH = f".nrw/schema/{SCHEMA_FILENAME}"

#: Path of the skill asset, relative to the ``nr_workbench`` package root.
SKILL_ASSET_RELPATH = f"skills/reflectometry/nrw-model-spec/assets/{SCHEMA_FILENAME}"


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


def schema_bytes() -> bytes:
    """Return the exact bytes every on-disk copy of the schema must hold.

    The scaffold engine and the drift test both compare file *contents*, so
    the serialisation has to live in one place rather than being reproduced at
    each call site.

    Returns:
        UTF-8 JSON, two-space indented, newline-terminated.
    """
    return (json.dumps(build_schema(), indent=2) + "\n").encode("utf-8")


def skill_asset_path() -> Path:
    """Return the path of the schema copy bundled with the skill.

    Returns:
        Absolute path to the packaged asset. It is not guaranteed to exist --
        the caller may be about to write it.
    """
    from importlib import resources

    package_root = Path(str(resources.files("nr_workbench")))
    return package_root.joinpath(*SKILL_ASSET_RELPATH.split("/"))


def write_schema(path: Path) -> Path:
    """Write the schema to disk.

    Args:
        path: Destination file.

    Returns:
        The path written.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(schema_bytes())
    return path
