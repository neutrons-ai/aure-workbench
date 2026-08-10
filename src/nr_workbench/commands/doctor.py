"""``nrw doctor`` -- report on the environment and the current project.

Deliberately tolerant: every check degrades to a reported status rather than an
exception, because the whole point is to run when something is wrong.
"""

from __future__ import annotations

import json
import platform
import sys
from dataclasses import asdict, dataclass
from importlib import metadata
from typing import Any

import click

#: Packages worth reporting a version for. `aure` is listed last because it is
#: the one most likely to be a git pin whose reported version lies.
_PACKAGES = (
    "numpy",
    "scipy",
    "matplotlib",
    "pydantic",
    "click",
    "refl1d",
    "bumps",
    "aure",
)

_OK = "ok"
_MISSING = "missing"


@dataclass
class Check:
    """One diagnostic line.

    Attributes:
        name: Short label.
        status: ``ok``, ``missing``, ``warn``, or ``error``.
        detail: Human-readable detail, e.g. a version or an explanation.
    """

    name: str
    status: str
    detail: str = ""


def _package_version(name: str) -> str | None:
    """Return an installed package's version, or None if absent."""
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def _aure_commit() -> str | None:
    """Recover the git commit aure was installed from, if recorded.

    aure's ``pyproject.toml`` has reported version ``0.1.0`` across every tag,
    so the version string cannot identify a build. pip records the actual
    source in ``direct_url.json`` for a VCS install, which is the only reliable
    identifier -- and it is what belongs in a provenance record.

    Returns:
        The resolved commit SHA, a URL, or None if not determinable.
    """
    try:
        dist = metadata.distribution("aure")
    except metadata.PackageNotFoundError:
        return None
    try:
        raw = dist.read_text("direct_url.json")
    except Exception:
        return None
    if not raw:
        return None
    try:
        info = json.loads(raw)
    except json.JSONDecodeError:
        return None
    vcs = info.get("vcs_info") or {}
    return vcs.get("commit_id") or info.get("url")


def collect_checks() -> list[Check]:
    """Run every diagnostic and return the results.

    Returns:
        Checks in display order: interpreter, packages, then project state.
    """
    checks: list[Check] = [
        Check("python", _OK, platform.python_version()),
        Check("platform", _OK, platform.platform()),
    ]

    from nr_workbench import __version__

    checks.append(Check("nr-workbench", _OK, __version__))

    for name in _PACKAGES:
        version = _package_version(name)
        if version is None:
            status = _MISSING
            detail = "not installed"
            if name == "aure":
                detail = "not installed (pip install -e '.' pulls it from git)"
            checks.append(Check(name, status, detail))
            continue
        detail = version
        if name == "aure":
            commit = _aure_commit()
            # The declared version is the same for every aure build, so show
            # the commit -- it is the only thing that identifies what is here.
            detail = (
                f"{version} @ {commit[:12]}"
                if commit and len(commit) >= 12
                else version
            )
        checks.append(Check(name, _OK, detail))

    checks.extend(_llm_checks())
    checks.extend(_project_checks())
    checks.extend(_agent_checks())
    return checks


def _agent_checks() -> list[Check]:
    """Report whether an unattended session could run here, and be limited.

    Both halves matter and they fail independently. A project can have the
    harness installed and no limits configured, which is worse than having
    neither -- it is the state where `nrw agent run` works and nothing stops a
    promotion. A project scaffolded before `.claude/settings.json` existed is
    exactly that state, so this says so rather than staying quiet.
    """
    import shutil

    from nr_workbench.project.layout import ProjectLayout, ProjectNotFoundError

    binary = shutil.which("claude")
    if binary:
        version = _harness_version(binary)
        detail = f"{version} at {binary}" if version else binary
        checks = [Check("harness", _OK, detail)]
    else:
        checks = [
            Check("harness", _MISSING, "`claude` not on PATH; `nrw agent run` needs it")
        ]

    try:
        root = ProjectLayout.discover().root
    except ProjectNotFoundError:
        return checks

    settings = root / ".claude" / "settings.json"
    if not settings.is_file():
        checks.append(
            Check(
                "agent limits",
                "warn",
                "no .claude/settings.json; run `nrw init` to add the hook that "
                "refuses promote, --upload and --force",
            )
        )
        return checks

    try:
        configured = json.loads(settings.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        checks.append(Check("agent limits", "error", f"{settings.name}: {exc}"))
        return checks

    if not isinstance(configured, dict):
        checks.append(
            Check("agent limits", "error", f"{settings.name} is not a JSON object")
        )
        return checks

    permissions = configured.get("permissions")
    denied = len(
        (permissions.get("deny") or []) if isinstance(permissions, dict) else []
    )
    where = _guard_hook_event(configured)

    if where == "PreToolUse":
        detail = f"PreToolUse hook + {denied} deny rule(s)"
        status = _OK
    elif where:
        # The misconfiguration most worth naming: the hook is there, so a
        # substring test would call this fine, but PostToolUse runs *after*
        # the command and refuses nothing.
        detail = f"`nrw agent guard` is on {where}, not PreToolUse -- it cannot refuse"
        status = "warn"
    else:
        detail = f"{denied} deny rule(s), but no `nrw agent guard` hook"
        status = "warn"

    checks.append(Check("agent limits", status, detail))
    return checks


def _guard_hook_event(configured: dict[str, Any]) -> str:
    """Which hook event runs `nrw agent guard`, or an empty string.

    Walks the structure rather than searching the serialised text: a hook
    registered under the wrong event, or matched against the wrong tool, is
    present in the JSON and does nothing.
    """
    hooks = configured.get("hooks")
    if not isinstance(hooks, dict):
        return ""
    for event, entries in hooks.items():
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict):
                continue
            matcher = str(entry.get("matcher", ""))
            if event == "PreToolUse" and "Bash" not in matcher:
                continue
            for hook in entry.get("hooks") or []:
                command = str(hook.get("command", "")) if isinstance(hook, dict) else ""
                if "agent guard" in command:
                    return str(event)
    return ""


def _harness_version(binary: str) -> str | None:
    """The installed harness version, or None if it will not say."""
    import subprocess

    try:
        result = subprocess.run(  # noqa: S603 - argv from shutil.which
            [binary, "--version"], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip().split()[0] if result.stdout.strip() else None


def _llm_checks() -> list[Check]:
    """Report the language-model endpoint, and where its settings came from.

    Only `nrw model new --from-notes` needs one, so an absent endpoint is a
    normal state rather than a problem -- but a *misconfigured* one is worth
    seeing, and so is the fact that a setting arrived from a file the user may
    not have known was being read.
    """
    from nr_workbench.aure_adapter import llm_info
    from nr_workbench.env import describe, load_env

    sources = load_env()
    checks: list[Check] = []

    if sources.files:
        checks.append(
            Check("config", _OK, ", ".join(str(path) for path in sources.files))
        )
    else:
        checks.append(Check("config", _MISSING, "no .env, ~/.nrw or ~/.aure found"))

    info = llm_info()
    if info.get("available"):
        endpoint = f"{info.get('provider')}/{info.get('model')}"
        if info.get("base_url"):
            endpoint += f" @ {info['base_url']}"
        checks.append(Check("llm", _OK, endpoint))
    else:
        checks.append(
            Check(
                "llm",
                _MISSING,
                "no endpoint; `nrw model new --print-prompt` works without one",
            )
        )

    settings = describe()
    if settings:
        checks.append(
            Check(
                "llm settings",
                _OK,
                ", ".join(f"{k}={v}" for k, v in sorted(settings.items())),
            )
        )
    return checks


def _project_checks() -> list[Check]:
    """Report on the project rooted at the current directory, if there is one."""
    from nr_workbench.project.config import ProjectConfigError, load_config
    from nr_workbench.project.layout import ProjectLayout, ProjectNotFoundError

    try:
        layout = ProjectLayout.discover()
    except ProjectNotFoundError:
        return [Check("project", _MISSING, "no nrw.toml here; run `nrw init`")]

    checks = [Check("project", _OK, str(layout.root))]

    try:
        config = load_config(layout.root)
    except ProjectConfigError as exc:
        checks.append(Check("nrw.toml", "error", str(exc)))
        return checks

    checks.append(Check("instrument", _OK, f"{config.facility} {config.instrument}"))
    if config.beamtime:
        checks.append(Check("beamtime", _OK, config.beamtime))

    samples = layout.list_samples()
    checks.append(
        Check(
            "samples",
            _OK if samples else "warn",
            ", ".join(samples) if samples else "none yet; run `nrw sample new <ID>`",
        )
    )

    if layout.skills_dir.is_dir():
        installed = sorted(
            p.parent.name for p in layout.skills_dir.glob("*/*/SKILL.md")
        )
        checks.append(
            Check(
                "skills",
                _OK if installed else "warn",
                f"{len(installed)} installed: {', '.join(installed)}"
                if installed
                else "none installed",
            )
        )
    else:
        checks.append(Check("skills", "warn", "no skills/ directory; run `nrw init`"))

    return checks


def run_doctor(*, as_json: bool = False) -> None:
    """Print the diagnostics.

    Args:
        as_json: Emit machine-readable JSON instead of a table.

    Raises:
        SystemExit: With code 1 if any check errored.
    """
    checks = collect_checks()

    if as_json:
        click.echo(json.dumps([asdict(c) for c in checks], indent=2))
    else:
        width = max(len(c.name) for c in checks)
        for check in checks:
            marker = {"ok": "✓", "missing": "·", "warn": "!", "error": "✗"}.get(
                check.status, "?"
            )
            click.echo(f"  {marker} {check.name:<{width}}  {check.detail}")

    if any(c.status == "error" for c in checks):
        sys.exit(1)


def environment_snapshot() -> dict[str, Any]:
    """Capture the versions that belong in a provenance record.

    Separate from :func:`collect_checks` because a fit manifest needs exact
    values, not human-readable status lines. The aure commit is included
    because its version string is identical across every build.

    Returns:
        Mapping suitable for serialising into a fit manifest.
    """
    from nr_workbench import __version__

    snapshot: dict[str, Any] = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "nr_workbench": __version__,
        "executable": sys.executable,
    }
    for name in _PACKAGES:
        version = _package_version(name)
        if version is not None:
            snapshot[name] = version
    commit = _aure_commit()
    if commit:
        snapshot["aure_commit"] = commit
    return snapshot
