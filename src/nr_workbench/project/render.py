"""Turn the packaged template tree into a list of files to install.

Files ending in ``.j2`` are rendered with Jinja2 and lose the suffix; every
other file is copied byte-for-byte. Rendering happens in memory so the caller
can hand the results to the idempotent scaffold engine rather than writing
directly over a scientist's edits.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any

from jinja2 import StrictUndefined, Template

from nr_workbench import __version__
from nr_workbench.harness import DEFAULT_HARNESSES, agent_dirs, resolve
from nr_workbench.project.config import CONTRACT_VERSION
from nr_workbench.project.scaffold import PlannedFile

#: Bump when a template's *content* changes, so existing projects pick it up on
#: the next `nrw init`. Files the user has edited are still never overwritten.
TEMPLATE_VERSION = 1

JINJA_SUFFIX = ".j2"

#: Never installed into a user's project.
#:
#: `__pycache__` is not hypothetical: the template and skill trees live inside
#: the package, so pip byte-compiles any `.py` they contain on install -- a
#: skill's `scripts/*.py` gains a sibling `__pycache__/*.cpython-3xx.pyc` in
#: site-packages. Without this filter that stale .pyc is copied into every
#: scaffolded project. An editable install never shows it, because nothing
#: compiles the source tree.
_EXCLUDED_DIRS = frozenset({"__pycache__"})
_EXCLUDED_SUFFIXES = frozenset({".pyc", ".pyo"})


class TemplateError(Exception):
    """Raised when the packaged template tree is missing or cannot render."""


def _prose_list(items: tuple[str, ...]) -> str:
    """Join paths the way a sentence would, as ``a``, ``a and b``, ``a, b and c``.

    Instruction files name the directories a project actually has, so a
    scaffold for one assistant does not tell it to look somewhere that was
    never written.

    Args:
        items: Paths to join.

    Returns:
        A prose fragment with each path in backticks, or an empty string.
    """
    quoted = [f"`{item}/`" for item in items]
    if not quoted:
        return ""
    if len(quoted) == 1:
        return quoted[0]
    return f"{', '.join(quoted[:-1])} and {quoted[-1]}"


@dataclass(frozen=True)
class MeasurementRow:
    """One row of the measurement table in ``sample.md``.

    Attributes:
        run: The run number.
        type: The *Type* column, e.g. ``full Q``.
        condition: The *Condition* column, e.g. ``OCV``.
    """

    run: int
    type: str = ""
    condition: str = ""


@dataclass(frozen=True)
class SampleProse:
    """What the experiment catalog writes into a sample's ``sample.md``.

    Every field defaults to empty, and an empty field renders exactly the
    scaffold ``nrw sample new`` has always written -- guidance comment and all
    -- so a sample the catalog does not manage is byte-for-byte unchanged.

    Attributes:
        managed: Whether the catalog owns this file; adds a note saying so.
        description: The *Description* section.
        details: The *Details* section.
        measurement_conditions: The *Measurement conditions* section.
        fits_to_perform: The *Fits to perform* section.
        measurements: Rows of the measurement table, in run order.
    """

    managed: bool = False
    description: str = ""
    details: str = ""
    measurement_conditions: str = ""
    fits_to_perform: str = ""
    measurements: tuple[MeasurementRow, ...] = ()


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
        harnesses: Names of the coding assistants this project is scaffolded
            for. Templates use it to record the choice and to recommend the
            matching editor extensions.
        prose: What the experiment catalog writes into ``sample.md``; empty for
            a sample it does not manage.
    """

    project_name: str
    facility: str = "SNS"
    instrument: str = "REF_L"
    beamtime: str | None = None
    ipts: str | None = None
    sample_id: str = ""
    title: str = ""
    created: str = ""
    harnesses: tuple[str, ...] = DEFAULT_HARNESSES
    prose: SampleProse = field(default_factory=SampleProse)

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
            "harnesses": list(self.harnesses),
            "agent_dirs": list(agent_dirs(resolve(self.harnesses))),
            "agent_dirs_prose": _prose_list(agent_dirs(resolve(self.harnesses))),
            "vscode_extensions": [
                harness.vscode_extension
                for harness in resolve(self.harnesses)
                if harness.vscode_extension
            ],
            # The scaffolded nrw.toml documents the experiment's default data
            # location. Rendered from the one constant, not copied into the
            # template, because the location is provisional and will move.
            "experiment_location": _default_experiment_location(),
            # Always defined, so StrictUndefined still catches a typo in a
            # template rather than rendering an empty section.
            "managed": self.prose.managed,
            "description": self.prose.description,
            "details": self.prose.details,
            "measurement_conditions": self.prose.measurement_conditions,
            "fits_to_perform": self.prose.fits_to_perform,
            "measurements": list(self.prose.measurements),
        }


def _default_experiment_location() -> str:
    # Function-local: project/ sits below experiment/, and this is the one
    # value it needs from there.
    from nr_workbench.experiment.config import DEFAULT_LOCATION

    return DEFAULT_LOCATION


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
                #
                # keep_trailing_newline: Jinja drops the final newline by
                # default, so every rendered file shipped without one -- git
                # reports "\ No newline at end of file" on each of them, and a
                # scaffolded project's own `end-of-file-fixer` would rewrite
                # them on its first commit. Only visible once a file that
                # previously had one (.vscode/extensions.json) became a
                # template; the .md and .toml ones had been missing it since
                # the beginning.
                content = Template(
                    raw, undefined=StrictUndefined, keep_trailing_newline=True
                ).render(**variables)
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
