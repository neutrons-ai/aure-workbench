"""Make ``nrw`` reachable from a session the project did not start.

An unattended session inherits its environment from ``nrw agent run``, so it
always finds ``nrw``. An *interactive* session does not: the scientist opens a
coding harness from an editor, the harness spawns a shell that never sourced
the virtualenv, and ``nrw`` is not there.

Measured on the reference beamtime project, that cost ten tool calls of
searching ``~/.venv``, ``~/.pixi``, ``~/.zshrc`` and ``~/git`` before the
binary was found -- and then a prefix on 136 of the session's 268 calls,
because shell state does not persist between them.

Three fixes, in increasing order of how much they take over:

``NRW_BIN``
    An environment variable holding the absolute path, written into the
    harness's machine-local settings. Additive: it introduces a new name and
    changes nothing that already worked.

``.nrw/bin/nrw``
    A shim inside the project. Short, relative, needs no ``export``, and works
    for any harness whatsoever because it is just a file.

``PATH``
    Only on request -- see :func:`path_override`. Harness settings do **not**
    interpolate ``${PATH}`` in an environment value; the string is passed
    through literally, so the value has to be a full PATH and writing one is
    an override rather than an addition. That is destructive enough to be a
    thing a person asks for, not a thing a scaffold does.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Environment variable carrying the absolute path to the ``nrw`` executable.
NRW_BIN_ENV = "NRW_BIN"

#: Project-local shim, relative to the root. Under ``.nrw/`` because it is
#: machine-local state: it names an absolute path, so it must never be committed.
SHIM_RELPATH = ".nrw/bin/nrw"

#: Machine-local harness settings. ``settings.local.json`` rather than
#: ``settings.json`` because the values here are absolute paths, and provenance
#: rule 3 is that no committed file carries one.
LOCAL_SETTINGS = ".claude/settings.local.json"

#: The machine-local files *this module* writes. Checked after every install.
MACHINE_LOCAL = (SHIM_RELPATH, LOCAL_SETTINGS)

#: Every harness's machine-local file, including harnesses this project is not
#: set up for. What `nrw check` polices, which is a wider question than what
#: `install` wrote: a project scaffolded for Claude Code can still have an
#: OpenCode file committed into it by whoever opened it in OpenCode, and a
#: list that only grows when someone adds a harness is a list that gets
#: forgotten.
MACHINE_LOCAL_ALL = (SHIM_RELPATH, LOCAL_SETTINGS, ".opencode/local.json")

#: What a process launched from a desktop session gets, near enough. Used to
#: probe the shell honestly -- see :func:`resolvable_in_fresh_shell`.
_BARE_PATH = "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

#: Environment variables a login shell needs to source a normal profile.
_PROBE_KEEP = ("HOME", "USER", "LOGNAME", "SHELL", "TERM", "LANG", "LC_ALL")

_SHIM_TEMPLATE = """\
#!/bin/sh
# Written by `nrw init`. Machine-local: it names an absolute path, so it is
# gitignored and must not be committed.
#
# Why this exists: an interactive coding session started from an editor does
# not inherit the environment that has `nrw` on its PATH. Calling this shim
# from the project root works regardless of how the session was started.
exec {executable} "$@"
"""


@dataclass(frozen=True)
class ToolPathReport:
    """What was installed to make ``nrw`` reachable.

    Attributes:
        executable: The resolved ``nrw`` executable, or None if it could not
            be found.
        shim: Path of the written shim, relative to the root.
        settings: Path of the settings file written, relative to the root.
        settings_note: Why the settings file was skipped, if it was.
        unignored: Machine-local paths this function wrote that git is *not*
            ignoring, so they are committable. Empty is the healthy case; see
            :func:`unignored_paths`.
        tracked: The subset of ``unignored`` that git is already tracking. A
            separate field because the remedy differs and an ignore rule is
            not it -- git applies no ignore rule to a path already in the
            index, which is why adding one to ``.gitignore`` appears to do
            nothing until the file is also removed from it.
        stale_path: Set when the settings file already carried a ``PATH``
            whose first entry is not the directory of the ``nrw`` we just
            resolved -- the fingerprint of a committed ``PATH`` from someone
            else's machine. Holds that first entry.
    """

    executable: Path | None
    shim: str | None = None
    settings: str | None = None
    settings_note: str = ""
    unignored: tuple[str, ...] = ()
    tracked: tuple[str, ...] = ()
    stale_path: str = ""


def nrw_executable() -> Path | None:
    """Locate the ``nrw`` console script that is running.

    Tries the invoked path first, because that is the one the user actually
    reached and it stays correct when several virtualenvs each have an ``nrw``.
    ``PATH`` is consulted next, and the interpreter's own ``bin`` last -- that
    one covers ``python -m nr_workbench``, where ``argv[0]`` is not a script.

    Returns:
        Absolute path to the executable, or None if none of the candidates
        exists and is executable.
    """
    candidates: list[Path] = []
    argv0 = sys.argv[0] if sys.argv else ""
    if argv0:
        candidates.append(Path(argv0))
    found = shutil.which("nrw")
    if found:
        candidates.append(Path(found))
    candidates.append(Path(sys.executable).parent / "nrw")

    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved.is_file() and os.access(resolved, os.X_OK):
            return resolved
    return None


def resolvable_in_fresh_shell(*, timeout: float = 15.0) -> bool:
    """Report whether a shell like a harness's could find ``nrw``.

    The probe has to be run with a *scrubbed* environment. This process was
    started from somewhere that already has ``nrw`` on its PATH -- otherwise it
    would not be running -- so a child inheriting that environment finds it
    every time, and the check would pass in exactly the situation it exists to
    catch.

    So: a login shell (which sources the user's profile, as the harness's does)
    given only the variables a desktop-launched process would have, and a bare
    system PATH. If the profile puts ``nrw`` on PATH, this finds it. If ``nrw``
    only lives in a virtualenv that the user activates by hand, it does not --
    which is the truth.

    Args:
        timeout: Seconds to wait. A slow profile is not a failure, so a
            timeout reports "resolvable" rather than raising an alarm nobody
            can act on.

    Returns:
        True if the probe shell resolved ``nrw``, or could not be run at all.
    """
    shell = os.environ.get("SHELL") or "/bin/sh"
    if not Path(shell).is_file():
        shell = "/bin/sh"

    environment = {key: os.environ[key] for key in _PROBE_KEEP if key in os.environ}
    environment["PATH"] = _BARE_PATH

    try:
        result = subprocess.run(  # noqa: S603 - argv is a shell path plus literals
            [shell, "-lc", "command -v nrw"],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=environment,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        # Cannot probe. Claiming a problem we did not observe would send
        # someone to fix a PATH that is fine.
        return True
    return result.returncode == 0 and bool(result.stdout.strip())


def write_shim(root: Path, executable: Path) -> str:
    """Write the project-local ``nrw`` shim and make it executable.

    Args:
        root: Project root.
        executable: Absolute path the shim should exec.

    Returns:
        The shim's path relative to the root, in POSIX form.
    """
    target = Path(root) / SHIM_RELPATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        _SHIM_TEMPLATE.format(executable=_quote(executable)), encoding="utf-8"
    )
    target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return SHIM_RELPATH


def _quote(path: Path) -> str:
    """Shell-quote a path for the shim's ``exec`` line."""
    import shlex

    return shlex.quote(str(path))


def _load_settings(path: Path) -> tuple[dict[str, Any] | None, str]:
    """Read a JSON settings file, distinguishing absent from malformed.

    Returns:
        ``(document, note)``. ``document`` is None when the file exists but
        cannot be used, in which case ``note`` says why -- a hand-edited
        settings file is the user's, and overwriting it to save them a keypress
        is not a trade this makes.
    """
    if not path.is_file():
        return ({}, "")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return (None, f"{path.name} could not be read ({exc}); left alone")
    if not isinstance(document, dict):
        return (None, f"{path.name} is not a JSON object; left alone")
    return (document, "")


def _write_settings(path: Path, document: dict[str, Any]) -> None:
    """Write a settings document, creating its directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def unignored_paths(root: Path, relpaths: Iterable[str]) -> tuple[str, ...]:
    """Return which of ``relpaths`` git would let someone commit.

    This module is the one place that knowingly writes an absolute path into a
    directory that gets shared, archived and sometimes published, so it is the
    right place to check that the path cannot escape. The check is against git
    itself rather than against the text of ``.gitignore``: a rule can come from
    the repository's file, from ``.git/info/exclude``, or from the user's global
    ignore file, and only ``git check-ignore`` knows about all three.

    Args:
        root: Project root.
        relpaths: Project-relative paths to test.

    Returns:
        The subset that is **not** ignored, in the order given. Empty when
        every path is safely ignored -- and also when this is not a git
        repository or git is not installed, because there is then nothing to
        commit to and a warning would send someone to fix a non-problem.

        A path that is already *tracked* is reported here too. ``git
        check-ignore`` says "not ignored" for one, which looks like a quirk and
        is exactly right: a matching rule in ``.gitignore`` has no effect on a
        file already in the index, so the path really is still committable.
        That is the state the field failure was in.
    """
    from nr_workbench.project import vcs

    candidates = [path for path in relpaths if (Path(root) / path).exists()]
    if not candidates or not vcs.is_repository(Path(root)):
        return ()

    safe = vcs.ignored(Path(root), candidates)
    return tuple(path for path in candidates if path not in safe)


def stale_path_entry(environment: Mapping[str, Any], executable: Path) -> str:
    """Detect a ``PATH`` entry that points at another machine's install.

    ``install`` refreshes ``NRW_BIN`` but deliberately leaves an existing
    ``PATH`` alone -- see :func:`path_override` for why writing one is a
    person's decision. That is correct in isolation and wrong after the file
    has been committed and cloned: the new clone's sessions then run with
    somebody else's virtualenv first on ``PATH``, and on a shared filesystem it
    resolves rather than failing.

    Args:
        environment: The ``env`` mapping from the local settings file.
        executable: The ``nrw`` we just resolved on this machine.

    Returns:
        The offending first ``PATH`` entry, or an empty string if ``PATH`` is
        absent or already leads with this machine's ``nrw``.
    """
    value = environment.get("PATH")
    if not isinstance(value, str) or not value:
        return ""
    first = value.split(os.pathsep)[0]
    if not first or Path(first) == executable.parent:
        return ""
    return first


def _tracked_subset(root: Path, relpaths: Sequence[str]) -> tuple[str, ...]:
    """Return which of ``relpaths`` git is already tracking.

    Args:
        root: Project root.
        relpaths: Project-relative paths to test.

    Returns:
        The tracked subset, in the order given, or empty if git cannot answer.
    """
    from nr_workbench.project import vcs

    if not relpaths:
        return ()
    known = set(vcs.tracked_files(Path(root)))
    return tuple(path for path in relpaths if path in known)


def install(root: Path, *, executable: Path | None = None) -> ToolPathReport:
    """Install the additive fixes: the shim, and ``NRW_BIN`` in local settings.

    Idempotent, and never destructive. An existing settings file is merged into
    rather than replaced, and a ``PATH`` entry already in it is left exactly as
    it is -- this function does not write one (see :func:`path_override`).

    Args:
        root: Project root.
        executable: Override the resolved executable. For tests.

    Returns:
        What was installed.
    """
    resolved = executable or nrw_executable()
    if resolved is None:
        return ToolPathReport(executable=None)

    shim = write_shim(Path(root), resolved)

    settings_path = Path(root) / LOCAL_SETTINGS
    document, note = _load_settings(settings_path)
    if document is None:
        unignored = unignored_paths(root, MACHINE_LOCAL)
        return ToolPathReport(
            executable=resolved,
            shim=shim,
            settings_note=note,
            unignored=unignored,
            tracked=_tracked_subset(root, unignored),
        )

    environment = document.get("env")
    if not isinstance(environment, dict):
        environment = {}
    stale = stale_path_entry(environment, resolved)
    environment[NRW_BIN_ENV] = str(resolved)
    document["env"] = environment
    _write_settings(settings_path, document)

    unignored = unignored_paths(root, MACHINE_LOCAL)
    return ToolPathReport(
        executable=resolved,
        shim=shim,
        settings=LOCAL_SETTINGS,
        unignored=unignored,
        tracked=_tracked_subset(root, unignored),
        stale_path=stale,
    )


def path_override(root: Path, *, executable: Path | None = None) -> str:
    """Compute the literal ``PATH`` that would make bare ``nrw`` work.

    Harness settings do not interpolate, so this is a full PATH rather than an
    append -- which is why it is computed here for a caller to show a person
    before writing it. Snapshotting a PATH freezes it: anything added to the
    profile afterwards is invisible to sessions in this project until it is
    written again.

    Args:
        root: Project root. Unused, present for symmetry with :func:`install`.
        executable: Override the resolved executable. For tests.

    Returns:
        The PATH value to write, or an empty string if ``nrw`` was not found.
    """
    del root
    resolved = executable or nrw_executable()
    if resolved is None:
        return ""
    bindir = str(resolved.parent)
    current = os.environ.get("PATH", _BARE_PATH)
    parts = [part for part in current.split(os.pathsep) if part and part != bindir]
    return os.pathsep.join([bindir, *parts])


def apply_path_override(root: Path, value: str) -> tuple[str | None, str]:
    """Write a ``PATH`` entry into the machine-local harness settings.

    Args:
        root: Project root.
        value: The literal PATH to write, as from :func:`path_override`.

    Returns:
        ``(relpath_written, note)``. ``relpath_written`` is None on refusal.
    """
    settings_path = Path(root) / LOCAL_SETTINGS
    document, note = _load_settings(settings_path)
    if document is None:
        return (None, note)

    environment = document.get("env")
    if not isinstance(environment, dict):
        environment = {}
    environment["PATH"] = value
    document["env"] = environment
    _write_settings(settings_path, document)
    return (LOCAL_SETTINGS, "")


def invocation(root: Path) -> str:
    """The command an assistant in this project should use to run ``nrw``.

    Args:
        root: Project root.

    Returns:
        ``"nrw"`` when a fresh shell resolves it, the shim's relative path when
        one is installed, or the absolute executable as a last resort.
    """
    if resolvable_in_fresh_shell():
        return "nrw"
    if (Path(root) / SHIM_RELPATH).is_file():
        return f"./{SHIM_RELPATH}"
    resolved = nrw_executable()
    return str(resolved) if resolved else "nrw"
