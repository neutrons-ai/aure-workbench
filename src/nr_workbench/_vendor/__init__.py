"""Byte-identical copies of files shared across the neutron-ai repos.

**Nothing in this package may be edited.** These files exist verbatim in
several repositories on purpose -- ``result_manifest.py`` carries a comment
saying exactly that -- so that any orchestrator can drive any of the tools
without a schema negotiation. A local "improvement" here silently forks a
shared contract.

Provenance of each file is recorded in ``upstream.toml`` at the repo root, and
``tests/test_vendor.py`` re-hashes them against the values recorded there.

To change one of these, change it upstream and re-sync.
"""

from __future__ import annotations
