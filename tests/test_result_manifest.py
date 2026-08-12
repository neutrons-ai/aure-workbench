"""The ``ndip-tool-result/1`` manifest schema.

This is the interop seam: any orchestrator that understands the schema can
read a manifest from this tool without knowing anything else about it.
"""

from __future__ import annotations

from nr_workbench.provenance.result_manifest import (
    SCHEMA,
    VALID_STATUS,
    build_manifest,
)


def test_manifest_writer_emits_the_shared_schema() -> None:
    manifest = build_manifest(
        "nrw-fit-run", "ok", params={"method": "amoeba"}, info={"chisq": 1.2}
    )

    assert SCHEMA == "ndip-tool-result/1"
    assert manifest["schema"] == SCHEMA
    assert manifest["status"] in VALID_STATUS
    assert manifest["params"] == {"method": "amoeba"}
    assert manifest["info"] == {"chisq": 1.2}
