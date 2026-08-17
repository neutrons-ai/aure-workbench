"""Reading and writing ``nrw.toml``, the project manifest.

Deliberately *not* a module-level singleton. nr-analyzer caches its resolved
config in a module global on first access, which makes it impossible to operate
on two projects in one process and surprises tests; we pass a
:class:`ProjectConfig` explicitly instead.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nr_workbench.harness import DEFAULT_HARNESSES

CONFIG_FILENAME = "nrw.toml"

#: Bumped when the on-disk contract changes in a way that needs migration.
CONTRACT_VERSION = 1

#: BL-4B conventions. These are facts about the instrument and its reduction
#: pipeline, not preferences -- see skills/reflectometry/refl-bl4b-instrument.
DEFAULT_CONVENTIONS: dict[str, Any] = {
    "steady_state_glob": "REFL_{run}_combined_data_auto.txt",
    "partial_glob": "REFL_{run}_{seg}_{subrun}_partial.txt",
    "tnr_slice_glob": "r{run}_t{t_s:06d}.txt",
    "tnr_intervals_glob": "r{run}_*reduction.json",
    "standard_thetas": [0.45, 1.2, 3.5],
    "tnr_theta": 0.6,
    # The 4th column of every REF_L reduced file is FWHM, not sigma. Getting
    # this wrong scales every resolution by 2.355 and quietly ruins a fit.
    "dq_convention": "FWHM",
}


class ProjectConfigError(Exception):
    """Raised when ``nrw.toml`` is missing, unreadable, or malformed."""


@dataclass(frozen=True)
class ProjectConfig:
    """Parsed contents of a project's ``nrw.toml``.

    Attributes:
        root: Absolute path to the project root (the directory holding nrw.toml).
        name: Project name.
        contract_version: On-disk contract version this project was created with.
        facility: Facility name, e.g. ``"SNS"``.
        instrument: Instrument name. Only ``"REF_L"`` is supported at v1.
        beamtime: Optional beamtime label, e.g. ``"june2026"``.
        ipts: Optional IPTS proposal identifier.
        conventions: Filename and instrument conventions; see DEFAULT_CONVENTIONS.
        harnesses: Names of the coding assistants this project is scaffolded
            for; see :mod:`nr_workbench.harness`.
        raw: The full parsed TOML document, for forward-compatible access.
    """

    root: Path
    name: str
    contract_version: int = CONTRACT_VERSION
    facility: str = "SNS"
    instrument: str = "REF_L"
    beamtime: str | None = None
    ipts: str | None = None
    conventions: dict[str, Any] = field(
        default_factory=lambda: dict(DEFAULT_CONVENTIONS)
    )
    harnesses: tuple[str, ...] = DEFAULT_HARNESSES
    raw: dict[str, Any] = field(default_factory=dict)


def load_config(root: Path) -> ProjectConfig:
    """Load ``nrw.toml`` from a project root.

    Args:
        root: Directory expected to contain ``nrw.toml``.

    Returns:
        The parsed project configuration.

    Raises:
        ProjectConfigError: If the file is absent or is not valid TOML.
    """
    config_path = Path(root) / CONFIG_FILENAME
    if not config_path.is_file():
        raise ProjectConfigError(
            f"No {CONFIG_FILENAME} found at {config_path}. Run `nrw init` to create a project."
        )
    try:
        with config_path.open("rb") as handle:
            document = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ProjectConfigError(f"{config_path} is not valid TOML: {exc}") from exc

    project = document.get("project", {})
    beamtime = document.get("beamtime", {})
    conventions = dict(DEFAULT_CONVENTIONS)
    conventions.update(document.get("conventions", {}))

    # A project written before harnesses were selectable has no [harness]
    # table, and must keep getting what it already has on disk.
    configured = document.get("harness", {}).get("kinds")
    harnesses = (
        tuple(str(name) for name in configured)
        if isinstance(configured, list)
        else DEFAULT_HARNESSES
    )

    return ProjectConfig(
        root=Path(root).resolve(),
        name=project.get("name", Path(root).resolve().name),
        contract_version=int(document.get("contract_version", CONTRACT_VERSION)),
        facility=project.get("facility", "SNS"),
        instrument=project.get("instrument", "REF_L"),
        beamtime=beamtime.get("label"),
        ipts=beamtime.get("ipts"),
        conventions=conventions,
        harnesses=harnesses,
        raw=document,
    )
