"""Write a neutral ``ndip-tool-result/1`` manifest.

A tool reports what it did in **its own vocabulary** — the resolved inputs it
used (``params``), the files it produced (``artifacts``), scalar diagnostics
(``info``), and a status. Any orchestrator that drives the tool can map that
into whatever bookkeeping it keeps; the tool itself stays completely
self-contained and knows nothing about downstream schemas.

This module is deliberately dependency-free and schema-agnostic.
"""

from __future__ import annotations

import json
from importlib.metadata import packages_distributions, version
from typing import Any, Dict, List, Optional

SCHEMA = "ndip-tool-result/1"

# status values understood by the orchestrator
VALID_STATUS = {"ok", "failed", "skipped", "dry-run", "needs-reprocessing"}


def _tool_version() -> str:
    # Auto-derive the installed distribution version for whatever top-level
    # package vendors this module (analyzer_tools | assembler | nr_isaac_format),
    # so this file stays byte-identical across the repos that share it.
    try:
        top = __name__.split(".")[0]
        dists = packages_distributions().get(top)
        if dists:
            return version(dists[0])
    except Exception:  # pragma: no cover - editable/source runs
        pass
    return "unknown"


def build_manifest(
    tool: str,
    status: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    artifacts: Optional[Dict[str, Any]] = None,
    info: Optional[Dict[str, Any]] = None,
    messages: Optional[List[Dict[str, str]]] = None,
    exit_code: int = 0,
) -> Dict[str, Any]:
    """Return a manifest dict. ``None`` values in params/artifacts are dropped."""
    manifest: Dict[str, Any] = {
        "tool": tool,
        "tool_version": _tool_version(),
        "schema": SCHEMA,
        "status": status,
        "exit_code": exit_code,
        "params": {k: v for k, v in (params or {}).items() if v is not None},
        "artifacts": {k: v for k, v in (artifacts or {}).items() if v is not None},
        "info": {k: v for k, v in (info or {}).items() if v is not None},
    }
    if messages:
        manifest["messages"] = messages
    return manifest


def write_manifest(path: str, *args: Any, **kwargs: Any) -> Dict[str, Any]:
    """Build a manifest (see :func:`build_manifest`) and write it to *path*."""
    manifest = build_manifest(*args, **kwargs)
    with open(path, "w") as f:
        json.dump(manifest, f, indent=2)
    return manifest
