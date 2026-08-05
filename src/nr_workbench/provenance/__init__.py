"""Provenance: recording what produced every result, at the moment it is true.

The primary requirement of this package. A result that cannot be traced back to
its script, inputs, and environment is the failure mode nr-workbench exists to
eliminate.
"""

from __future__ import annotations

from nr_workbench.provenance.hashing import (
    FileDigest,
    HashCache,
    digest_files,
    inputs_digest,
)
from nr_workbench.provenance.index import FitIndex
from nr_workbench.provenance.record import FitDirectory, FitIdentity, FitRecord
from nr_workbench.provenance.whence import Freshness, Resolution, WhenceResult, whence

__all__ = [
    "FileDigest",
    "FitDirectory",
    "FitIdentity",
    "FitIndex",
    "FitRecord",
    "Freshness",
    "HashCache",
    "Resolution",
    "WhenceResult",
    "digest_files",
    "inputs_digest",
    "whence",
]
