"""Turn the packaged template tree into a list of files to install.

Files ending in ``.j2`` are rendered with Jinja2 and lose the suffix; every
other file is copied byte-for-byte. Rendering happens in memory so the caller
can hand the results to the idempotent scaffold engine rather than writing
directly over a scientist's edits.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any

from jinja2 import StrictUndefined, Template

from nr_workbench import __version__
from nr_workbench.project.config import CONTRACT_VERSION
from nr_workbench.project.scaffold import PlannedFile

#: Bump when a template's *content* changes, so existing projects pick it up on
#: the next `nrw init`. Files the user has edited are still never overwritten.
TEMPLATE_VERSION = 1

JINJA_SUFFIX = ".j2"

#: Never installed into a user's project.
#:
#: `__pycache__` is not hypothetical: the template tree lives inside the
#: package, so pip byte-compiles any `.py` template on install --
#: `templates/project/scripts/install_skills.py` gains a sibling
#: `__pycache__/install_skills.cpython-3xx.pyc` in site-packages. Without this
#: filter that stale .pyc is copied into every scaffolded project. An editable
#: install never shows it, because nothing compiles the source tree.
_EXCLUDED_DIRS = frozenset({"__pycache__"})
_EXCLUDED_SUFFIXES = frozenset({".pyc", ".pyo"})


class TemplateError(Exception):
    """Raised when the packaged template tree is missing or cannot render."""


@dataclass(frozen=True)
class RenderContext:
    """Values substituted into ``.j2`` templates.

    Attributes:
        project_name: Name of the project being scaffolded.
        facility: Facility name, e.g. ``"SNS"``.
        instrument: Instrument name, e.g. ``"REF_L"``.
        beamtime: Optional beamtime label.
        ipts: Optional IPTS proposal identifier.
        sample_id: Sample identifier, when rendering the sample templates.
        title: Human-readable sample title, when rendering sample templates.
        created: ISO-8601 UTC timestamp for the scaffold run.
    """

    project_name: str
    facility: str = "SNS"
    instrument: str = "REF_L"
    beamtime: str | None = None
    ipts: str | None = None
    sample_id: str = ""
    title: str = ""
    created: str = ""

    def as_dict(self) -> dict[str, Any]:
        """Return the template variables, filling in derived defaults.

        Returns:
            Mapping of template variable name to value.
        """
        return {
            "project_name": self.project_name,
            "facility": self.facility,
            "instrument": self.instrument,
            "beamtime": self.beamtime or "",
            "ipts": self.ipts or "",
            "sample_id": self.sample_id,
            "title": self.title or self.sample_id,
            "created": self.created or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "nrw_version": __version__,
            "contract_version": CONTRACT_VERSION,
        }


def templates_root() -> Path:
    """Return the directory holding the packaged templates.

    Returns:
        Absolute path to ``nr_workbench/templates``.

    Raises:
        TemplateError: If the directory is absent, which means the wheel was
            built without its package-data.
    """
    root = Path(str(resources.files("nr_workbench"))) / "templates"
    if not root.is_dir():
        raise TemplateError(
            f"No templates directory at {root}. If this is an installed "
            "nr-workbench, the wheel was built without its package-data -- see "
            "[tool.setuptools.package-data] in pyproject.toml."
        )
    return root


def render_tree(
    subdir: str,
    context: RenderContext,
    *,
    prefix: str = "",
) -> list[PlannedFile]:
    """Render one packaged template subtree into planned files.

    Args:
        subdir: Subdirectory of the templates root, e.g. ``"project"``.
        context: Values to substitute into ``.j2`` templates.
        prefix: POSIX path prepended to each output path, e.g.
            ``"samples/Sample1"``.

    Returns:
        Planned files in sorted path order.

    Raises:
        TemplateError: If the subtree is missing or a template fails to render.
    """
    root = templates_root() / subdir
    if not root.is_dir():
        raise TemplateError(f"No template subtree at {root}")

    variables = context.as_dict()
    planned: list[PlannedFile] = []

    for source in sorted(root.rglob("*")):
        if not source.is_file() or source.name == "__init__.py":
            continue
        if _EXCLUDED_DIRS & set(source.parts) or source.suffix in _EXCLUDED_SUFFIXES:
            continue

        relative = source.relative_to(root).as_posix()
        if relative.endswith(JINJA_SUFFIX):
            relative = relative[: -len(JINJA_SUFFIX)]
            raw = source.read_text(encoding="utf-8")
            try:
                # StrictUndefined: a typo'd variable must fail loudly at scaffold
                # time, not silently produce an empty field in a config file.
                content = Template(raw, undefined=StrictUndefined).render(**variables)
            except Exception as exc:
                raise TemplateError(f"Failed to render {source}: {exc}") from exc
            data = content.encode("utf-8")
        else:
            data = source.read_bytes()

        target = f"{prefix}/{relative}" if prefix else relative
        planned.append(
            PlannedFile(
                relpath=target,
                content=data,
                template_id=f"{subdir}/{source.relative_to(root).as_posix()}",
                template_version=TEMPLATE_VERSION,
            )
        )

    return planned
