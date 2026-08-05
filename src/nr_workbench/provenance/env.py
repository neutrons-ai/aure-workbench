"""Capture the environment a fit ran in.

"Reproducible" is a claim you have to be able to check. Recording exact
versions, the full resolved dependency set, and the git state of the project --
including a patch when the tree was dirty -- is what makes it checkable two
years later.

The aure commit gets special handling: its metadata reports version ``0.1.0``
for every build, so the version string identifies nothing. The commit pip
resolved is the only real identifier.
"""

from __future__ import annotations

import json
import platform
import subprocess
import sys
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path
from typing import Any

#: Packages whose exact version can change a fit result or its interpretation.
TRACKED_PACKAGES = (
    "nr-workbench",
    "aure",
    "refl1d",
    "bumps",
    "numpy",
    "scipy",
    "matplotlib",
    "periodictable",
)

_GIT_TIMEOUT = 30


@dataclass
class GitState:
    """The project's git state at the moment of a run.

    Attributes:
        available: Whether the project is inside a usable git work tree.
        commit: Resolved HEAD sha, if any.
        branch: Current branch name, if any.
        dirty: Whether tracked files had uncommitted changes.
        patch: Unified diff of tracked changes when dirty, else ``None``.
            Without this, a fit run from a dirty tree records a commit that
            does not describe the code that actually ran.
    """

    available: bool = False
    commit: str | None = None
    branch: str | None = None
    dirty: bool = False
    patch: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return the JSON-serialisable form, omitting the patch body."""
        return {
            "available": self.available,
            "commit": self.commit,
            "branch": self.branch,
            "dirty": self.dirty,
        }


@dataclass
class Environment:
    """Everything about the runtime worth recording.

    Attributes:
        python: Interpreter version.
        platform: Platform string.
        executable: Path to the interpreter.
        packages: Version of each tracked package that is installed.
        aure_commit: The commit aure was installed from, if resolvable.
        git: The project's git state.
        requirements: Full resolved dependency list (``pip freeze`` equivalent).
    """

    python: str
    platform: str
    executable: str
    packages: dict[str, str] = field(default_factory=dict)
    aure_commit: str | None = None
    git: GitState = field(default_factory=GitState)
    requirements: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """Return the JSON-serialisable summary (not the requirements list)."""
        payload: dict[str, Any] = {
            "python": self.python,
            "platform": self.platform,
            "executable": self.executable,
            "packages": dict(sorted(self.packages.items())),
            "git": self.git.as_dict(),
        }
        if self.aure_commit:
            payload["aure_commit"] = self.aure_commit
        return payload


def package_version(name: str) -> str | None:
    """Return an installed package's version, or None if it is absent.

    Args:
        name: Distribution name.

    Returns:
        The version string, or ``None``.
    """
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def aure_commit() -> str | None:
    """Recover the git commit aure was installed from.

    aure's ``pyproject.toml`` has reported ``0.1.0`` for every release, and its
    ``v0.1.0`` and ``v0.1.1`` tags point at the same commit, so the version
    string cannot identify a build. pip records the real source in
    ``direct_url.json`` for a VCS install.

    Returns:
        The resolved commit sha, the URL if no commit was recorded, or ``None``.
    """
    try:
        dist = metadata.distribution("aure")
        raw = dist.read_text("direct_url.json")
    except (metadata.PackageNotFoundError, OSError):
        return None
    if not raw:
        return None
    try:
        info = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return (info.get("vcs_info") or {}).get("commit_id") or info.get("url")


def _git(root: Path, *args: str) -> str | None:
    """Run a git command in ``root``, returning stripped stdout or None."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def capture_git(root: Path) -> GitState:
    """Capture the git state of the project at ``root``.

    Never raises: a project need not be a git repository, and git need not be
    installed. Absence is recorded, not treated as an error.

    Args:
        root: Project root.

    Returns:
        The git state, with ``available=False`` if there is no work tree.
    """
    inside = _git(root, "rev-parse", "--is-inside-work-tree")
    if inside != "true":
        return GitState(available=False)

    commit = _git(root, "rev-parse", "HEAD")
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    status = _git(root, "status", "--porcelain")
    dirty = bool(status)
    patch = _git(root, "diff", "HEAD") if dirty else None

    return GitState(
        available=True,
        commit=commit,
        branch=branch if branch != "HEAD" else None,
        dirty=dirty,
        patch=patch or None,
    )


def freeze() -> list[str]:
    """Return the full resolved dependency set, one ``name==version`` per line.

    Uses ``importlib.metadata`` rather than shelling out to ``pip freeze``:
    it is faster, needs no subprocess, and reports what *this* interpreter can
    actually import.

    Returns:
        Sorted ``name==version`` strings.
    """
    seen: dict[str, str] = {}
    for dist in metadata.distributions():
        name = dist.metadata["Name"]
        if name and name not in seen:
            seen[name] = dist.version or "unknown"
    return [
        f"{name}=={version}"
        for name, version in sorted(seen.items(), key=lambda kv: kv[0].lower())
    ]


def capture(root: Path, *, include_requirements: bool = True) -> Environment:
    """Capture the full environment for a provenance record.

    Args:
        root: Project root, for git state.
        include_requirements: Collect the full dependency list. Off in tests
            and dry runs where it is noise.

    Returns:
        The captured environment.
    """
    packages = {}
    for name in TRACKED_PACKAGES:
        version = package_version(name)
        if version is not None:
            packages[name] = version

    return Environment(
        python=platform.python_version(),
        platform=platform.platform(),
        executable=sys.executable,
        packages=packages,
        aure_commit=aure_commit(),
        git=capture_git(Path(root)),
        requirements=freeze() if include_requirements else [],
    )
