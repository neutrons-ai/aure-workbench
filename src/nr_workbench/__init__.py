"""nr-workbench: a project workbench for neutron reflectometry at SNS REF_L.

`nr-workbench init` scaffolds an analysis project -- one layout for every
sample, a curated ``skills/`` directory that AI coding assistants read, and a
provenance ledger that keeps every result linked to the script, data, and
environment that produced it.

Public API::

    from nr_workbench import __version__
    from nr_workbench.project import ProjectConfig, ProjectLayout

Everything else is internal and may change between releases.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
