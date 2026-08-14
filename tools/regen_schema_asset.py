#!/usr/bin/env python3
"""Regenerate the committed JSON Schema asset from the live pydantic models.

`skills/reflectometry/nrw-model-spec/assets/nrw-model-1.json` is a *generated*
artifact that happens to be committed, because the skill has to be able to hand
an agent the schema as a file without a project around it. It used to be
maintained by hand, and it drifted: it lost `Constraint.endpoint_range` while
still declaring `additionalProperties: false`, so it rejected specs that the
code accepts, and a reader trusted it over the SKILL.md prose beside it.

Run this after any change to `nr_workbench.spec.models`. It is the fix
`tests/test_spec.py::test_the_bundled_schema_asset_matches_the_live_models`
tells you to run.

Usage:
    python tools/regen_schema_asset.py           # rewrite if stale
    python tools/regen_schema_asset.py --check   # exit 1 if stale, write nothing
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def main(argv: list[str] | None = None) -> int:
    """Regenerate (or check) the bundled schema asset.

    Args:
        argv: Command-line arguments, defaulting to ``sys.argv[1:]``.

    Returns:
        Process exit code: 0 if the asset is current or was rewritten, 1 if
        ``--check`` found it stale.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="report staleness without writing, for CI.",
    )
    args = parser.parse_args(argv)

    from nr_workbench.spec.schema import SKILL_ASSET_RELPATH, schema_bytes

    # Resolve against the source tree, not site-packages: an editable install
    # makes these the same file, a real one does not, and rewriting a wheel's
    # copy would be a no-op that looks like success.
    target = ROOT / "src" / "nr_workbench" / SKILL_ASSET_RELPATH
    expected = schema_bytes()
    current = target.read_bytes() if target.is_file() else b""

    if current == expected:
        print(f"up to date: {target.relative_to(ROOT)}")
        return 0

    if args.check:
        print(
            f"STALE: {target.relative_to(ROOT)} does not match "
            "nr_workbench.spec.models.\n"
            "Run `python tools/regen_schema_asset.py` and commit the result.",
            file=sys.stderr,
        )
        return 1

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(expected)
    print(f"rewrote: {target.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
