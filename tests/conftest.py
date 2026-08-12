"""Shared fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from nr_workbench.project.render import RenderContext

#: A fixed timestamp, so rendered templates are byte-stable across test runs.
FIXED_CREATED = "2026-01-02T03:04:05Z"


@pytest.fixture
def context() -> RenderContext:
    """A render context with a fixed timestamp."""
    return RenderContext(
        project_name="test-project",
        beamtime="june2026",
        ipts="IPTS-00001",
        created=FIXED_CREATED,
    )


@pytest.fixture
def project(tmp_path: Path, context: RenderContext) -> Path:
    """A scaffolded project with the seed skills and one sample."""
    from nr_workbench.commands.init_cmd import plan_project_files
    from nr_workbench.commands.sample import plan_sample_files
    from nr_workbench.project.scaffold import apply_scaffold

    planned = plan_project_files(context)
    planned.extend(plan_sample_files(context, "Sample1"))
    apply_scaffold(tmp_path, planned)
    return tmp_path
