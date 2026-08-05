"""Guard the vendored files against silent drift.

``_vendor/`` holds byte-identical copies of contracts shared across the
neutron-ai repos. The value of "byte-identical" is entirely in it being true,
so it is checked rather than asserted in a comment.
"""

from __future__ import annotations

import hashlib
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = REPO_ROOT / "upstream.toml"


def load_verbatim() -> list[dict]:
    """Read the verbatim entries from ``upstream.toml``."""
    if not UPSTREAM.is_file():
        pytest.skip("no upstream.toml in this checkout")
    return tomllib.loads(UPSTREAM.read_text(encoding="utf-8")).get("verbatim", [])


@pytest.mark.parametrize("entry", load_verbatim(), ids=lambda e: e["dest"])
def test_vendored_file_matches_its_recorded_hash(entry: dict) -> None:
    """A local edit to a shared contract silently forks it. Catch it here."""
    target = REPO_ROOT / entry["dest"]
    assert target.is_file(), f"{entry['dest']} is recorded in upstream.toml but missing"

    actual = hashlib.sha256(target.read_bytes()).hexdigest()

    assert actual == entry["sha256"], (
        f"{entry['dest']} has been modified. It is a byte-identical copy of "
        f"{entry['repo']}:{entry['path']} and must not be edited here. "
        "Change it upstream and re-sync, updating upstream.toml."
    )


def test_every_vendored_file_is_declared() -> None:
    """A file in _vendor/ with no upstream.toml entry has lost its provenance."""
    declared = {entry["dest"] for entry in load_verbatim()}
    on_disk = {
        path.relative_to(REPO_ROOT).as_posix()
        for path in (REPO_ROOT / "src" / "nr_workbench" / "_vendor").glob("*.py")
        if path.name != "__init__.py"
    }

    assert on_disk <= declared, (
        f"undeclared vendored files: {sorted(on_disk - declared)}"
    )


def test_manifest_writer_still_emits_the_shared_schema() -> None:
    """The interop promise: an orchestrator reads ndip-tool-result/1 from us."""
    from nr_workbench._vendor.result_manifest import (
        SCHEMA,
        VALID_STATUS,
        build_manifest,
    )

    manifest = build_manifest(
        "nrw-fit-run", "ok", params={"method": "amoeba"}, info={"chisq": 1.2}
    )

    assert SCHEMA == "ndip-tool-result/1"
    assert manifest["schema"] == SCHEMA
    assert manifest["status"] in VALID_STATUS
    assert manifest["params"] == {"method": "amoeba"}
    assert manifest["info"] == {"chisq": 1.2}
