"""Path resolution for a workbench project.

One layout, always. The single structural decision that makes this work is that
``samples/`` is the top-level key and beamtime is *metadata* -- the four
mutually incompatible per-beamtime layouts in the old experiments-2025 repo are
what this replaces.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from nr_workbench.project.config import CONFIG_FILENAME

#: Machine-owned state directory at the project root.
STATE_DIR = ".nrw"

#: Per-sample subdirectories created by `nrw sample new`.
SAMPLE_SUBDIRS = (
    "data/steady",
    "data/tnr",
    "data/raw",
    "models",
    "assessments",
    "results",
    "reports",
)


class ProjectNotFoundError(Exception):
    """Raised when no project root can be found from a starting directory."""


def find_project_root(start: Path | None = None) -> Path:
    """Walk up from ``start`` looking for the directory containing ``nrw.toml``.

    Args:
        start: Directory to start from. Defaults to the current directory.

    Returns:
        The absolute path of the project root.

    Raises:
        ProjectNotFoundError: If no ``nrw.toml`` is found in ``start`` or any parent.
    """
    current = (Path.cwd() if start is None else Path(start)).resolve()
    for candidate in [current, *current.parents]:
        if (candidate / CONFIG_FILENAME).is_file():
            return candidate
    raise ProjectNotFoundError(
        f"No {CONFIG_FILENAME} found in {current} or any parent directory. "
        "Run `nrw init` to create a project here."
    )


@dataclass(frozen=True)
class ProjectLayout:
    """Resolves the standard paths of a workbench project.

    Attributes:
        root: Absolute path to the project root.
    """

    root: Path

    @classmethod
    def discover(cls, start: Path | None = None) -> ProjectLayout:
        """Build a layout by walking up from ``start`` to the project root.

        Args:
            start: Directory to start from. Defaults to the current directory.

        Returns:
            A layout rooted at the discovered project.

        Raises:
            ProjectNotFoundError: If no project root is found.
        """
        return cls(root=find_project_root(start))

    @property
    def config_file(self) -> Path:
        """Path to ``nrw.toml``."""
        return self.root / CONFIG_FILENAME

    @property
    def state_dir(self) -> Path:
        """Path to the machine-owned ``.nrw/`` directory."""
        return self.root / STATE_DIR

    @property
    def scaffold_lock(self) -> Path:
        """Path to the scaffold lock recording what `nrw init` installed."""
        return self.state_dir / "scaffold.lock.json"

    @property
    def skills_lock(self) -> Path:
        """Path to the lock recording installed skill versions and hashes."""
        return self.state_dir / "skills.lock.json"

    @property
    def index_file(self) -> Path:
        """Path to the append-only fit index (``.nrw/index.jsonl``)."""
        return self.state_dir / "index.jsonl"

    @property
    def schema_dir(self) -> Path:
        """Path to the generated JSON Schema directory."""
        return self.state_dir / "schema"

    @property
    def cache_dir(self) -> Path:
        """Path to the gitignored hash/thumbnail cache."""
        return self.state_dir / "cache"

    @property
    def backups_dir(self) -> Path:
        """Path to the directory holding `init --force` backups."""
        return self.state_dir / "backups"

    @property
    def skills_dir(self) -> Path:
        """Path to the tool-neutral, repo-root ``skills/`` directory.

        Repo-root rather than ``.claude/skills/`` because GitHub Copilot cannot
        read the latter; every assistant reaches this one by ``Read``.
        """
        return self.root / "skills"

    def agent_dirs(self, harnesses: Iterable[str] | None = None) -> tuple[Path, ...]:
        """The agent directories that receive thin dispatcher stubs.

        Args:
            harnesses: Harness names to collect directories for. Read from the
                project's ``nrw.toml`` when omitted.

        Returns:
            Absolute paths, skipping harnesses that read no subagent files.

        Raises:
            HarnessError: If a name is not a known harness.
            ProjectConfigError: If ``harnesses`` is omitted and there is no
                readable ``nrw.toml``.
        """
        from nr_workbench.harness import agent_dirs as harness_agent_dirs
        from nr_workbench.harness import resolve
        from nr_workbench.project.config import load_config

        if harnesses is None:
            harnesses = load_config(self.root).harnesses
        return tuple(
            self.root / relpath for relpath in harness_agent_dirs(resolve(harnesses))
        )

    @property
    def samples_dir(self) -> Path:
        """Path to ``samples/``."""
        return self.root / "samples"

    def sample(self, sample_id: str) -> Path:
        """Path to one sample's directory.

        Args:
            sample_id: The sample identifier, e.g. ``"Sample4"``.

        Returns:
            The absolute path to ``samples/<sample_id>``.
        """
        return self.samples_dir / sample_id

    def list_samples(self) -> list[str]:
        """List sample IDs present on disk, sorted.

        A directory counts as a sample only if it holds a ``sample.md``, so
        stray directories under ``samples/`` are not mistaken for samples.

        Returns:
            Sorted sample identifiers.
        """
        if not self.samples_dir.is_dir():
            return []
        return sorted(
            entry.name
            for entry in self.samples_dir.iterdir()
            if entry.is_dir() and (entry / "sample.md").is_file()
        )
