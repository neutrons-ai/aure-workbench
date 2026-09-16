"""The small amount of git this package needs, and the rule for using it.

Two places ask git questions: :mod:`nr_workbench.project.toolpath`, which
checks that the machine-local files it just wrote cannot be committed, and
``nrw check``, which enforces provenance rule 3 -- no absolute paths in
committed files.

The rule for both: **git being absent is not a finding.** A project may be a
plain directory, may live under another VCS, or may be running somewhere git is
not installed. Reporting a problem we did not observe sends a scientist to fix
something that is not broken, so every helper here returns "nothing to say"
rather than raising when git cannot answer.
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path

#: Seconds before a git call is abandoned. Generous: a cold index on a network
#: filesystem is slow, and a timeout here is reported as "no answer", so being
#: impatient would silently turn a real check into a skipped one.
_TIMEOUT = 30.0


def run_git(
    root: Path,
    args: Sequence[str],
    *,
    stdin: str | None = None,
    accept: Sequence[int] = (0,),
) -> str | None:
    """Run a git command in ``root`` and return its stdout, or None.

    Args:
        root: Directory to run in, normally the project root.
        args: Arguments after ``git``, e.g. ``["ls-files", "-z"]``.
        stdin: Text to write to the process's stdin.
        accept: Return codes whose stdout is meaningful. Anything else -- most
            importantly 128, "not a git repository" -- yields None.

    Returns:
        Standard output on an accepted return code, otherwise None. Also None
        if git is missing, the directory is unreadable, or the call times out.
    """
    try:
        result = subprocess.run(  # noqa: S603 - argv is literals plus caller paths
            ["git", *args],  # noqa: S607 - git is resolved from PATH by design
            input=stdin,
            capture_output=True,
            text=True,
            cwd=root,
            timeout=_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode not in accept:
        return None
    return result.stdout


def is_repository(root: Path) -> bool:
    """Report whether ``root`` is inside a git working tree.

    Args:
        root: Directory to test.

    Returns:
        True only if git confirms it. False when git is absent or says no.
    """
    out = run_git(root, ["rev-parse", "--is-inside-work-tree"])
    return out is not None and out.strip() == "true"


def tracked_files(root: Path) -> tuple[str, ...]:
    """List the paths git is tracking, relative to ``root``.

    Args:
        root: Project root.

    Returns:
        Tracked paths in POSIX form, or an empty tuple if git cannot answer.
        ``-z`` is used so that a filename containing a newline -- rare, but it
        would otherwise split into two bogus entries -- stays intact.
    """
    out = run_git(root, ["ls-files", "-z"])
    if out is None:
        return ()
    return tuple(name for name in out.split("\0") if name)


def ignored(root: Path, relpaths: Sequence[str]) -> frozenset[str]:
    """Return which of ``relpaths`` git would refuse to add.

    Asks git rather than reading ``.gitignore``, because a rule can also come
    from ``.git/info/exclude`` or the user's global ignore file, and only git
    knows about all three.

    Args:
        root: Project root.
        relpaths: Project-relative paths to test.

    Returns:
        The subset that is ignored. Empty when git cannot answer, which callers
        must treat as "unknown", not as "nothing is ignored".
    """
    if not relpaths:
        return frozenset()
    # 0: at least one path matched a rule. 1: none did. Both are real answers.
    out = run_git(
        root,
        ["check-ignore", "--stdin"],
        stdin="\n".join(relpaths),
        accept=(0, 1),
    )
    if out is None:
        return frozenset()
    return frozenset(line.strip() for line in out.splitlines() if line.strip())


def grep(root: Path, pattern: str) -> tuple[tuple[str, int, str], ...]:
    """Search tracked files for an extended-regexp pattern.

    ``git grep`` rather than a Python walk: it searches only tracked files,
    which is exactly the question rule 3 asks, and it skips binaries by itself.

    Args:
        root: Project root.
        pattern: Extended regular expression.

    Returns:
        ``(relpath, line_number, line_text)`` per match, or an empty tuple if
        git cannot answer or nothing matched.
    """
    out = run_git(
        root,
        ["grep", "-I", "-n", "-E", "-e", pattern, "--", "."],
        accept=(0, 1),
    )
    if out is None:
        return ()

    matches: list[tuple[str, int, str]] = []
    for line in out.splitlines():
        # `path:lineno:text`, and text may itself contain colons.
        parts = line.split(":", 2)
        if len(parts) != 3 or not parts[1].isdigit():
            continue
        matches.append((parts[0], int(parts[1]), parts[2]))
    return tuple(matches)
