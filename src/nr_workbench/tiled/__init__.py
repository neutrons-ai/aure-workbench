"""Tiled: where an experiment's reduced data, and its catalog, may be kept.

docs/plan-tiled-catalog.md builds this package up. :mod:`.address` holds the
rules every part of it follows -- which server, and what an address there may
be -- so that nothing is sent anywhere they do not allow.

Nothing here imports an HTTP client at module scope, and this file imports
nothing at all: :mod:`nr_workbench.project.config`, which nearly every command
loads, imports :mod:`.address`.
"""
