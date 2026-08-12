"""Knowing a session is running, and being able to stop it.

A session could be started and could not be stopped. ``nrw agent`` had ``run``,
``watch`` and ``guard``; the only way to end a run was to find it with ``ps``,
read the prompt path out of its command line to work out which sample it was on,
and ``kill`` the pid by hand. That is the wrong thing to be doing at 2am, and it
is the wrong thing to be doing when a fit is writing into the project.

Two things make it more than a convenience:

**Killing the harness is not enough.** It launches ``refl1d``, and a fit that
outlives the session keeps writing into a result directory nobody is watching --
which is how two fits end up interleaved. So stopping goes through
:func:`~nr_workbench.agent.session._kill_group`, the same path the ``--timeout``
already used internally.

**The record has to say what was interrupted.** A killed session leaves a
half-finished sample, and the next person needs to know that rather than
inferring it from a fit with no note.

The pidfile is deliberately dumb -- a small JSON file per running session, named
for the sample -- and every reader treats a pid that no longer exists as stale
rather than as truth. A crashed session leaves a file behind; that is expected,
and reported instead of trusted.
"""

from __future__ import annotations

import json
import os
import signal
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Where a running session records itself, under the project's ``.nrw/agent``.
PIDFILE_SUFFIX = ".running.json"


@dataclass(frozen=True)
class Running:
    """A session that recorded itself as running.

    Attributes:
        sample: The sample it is analysing.
        pid: The harness process id.
        started: ISO-8601 start time.
        transcript: Path to its transcript, relative to the project root.
        alive: Whether that pid still exists.
    """

    sample: str
    pid: int
    started: str
    transcript: str
    alive: bool

    def as_dict(self) -> dict[str, Any]:
        """Return the JSON form."""
        return {
            "sample": self.sample,
            "pid": self.pid,
            "started": self.started,
            "transcript": self.transcript,
            "alive": self.alive,
        }


def _directory(root: Path) -> Path:
    """The directory pidfiles live in."""
    from nr_workbench.agent.session import SESSION_DIR

    return Path(root) / ".nrw" / SESSION_DIR


def pidfile(root: Path, sample: str) -> Path:
    """Path of the pidfile for one sample."""
    return _directory(root) / f"{sample}{PIDFILE_SUFFIX}"


def is_alive(pid: int) -> bool:
    """Whether a process exists, without signalling it.

    ``kill(pid, 0)`` performs the permission and existence checks and delivers
    nothing, which is exactly the question being asked.
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    return True


def record(root: Path, sample: str, pid: int, started: str, transcript: Path) -> Path:
    """Write the pidfile for a session that is starting.

    Args:
        root: Project root.
        sample: Sample identifier.
        pid: The harness process id.
        started: ISO-8601 start time.
        transcript: Where its transcript is being written.

    Returns:
        The pidfile path.
    """
    path = pidfile(Path(root), sample)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        relative = str(Path(transcript).relative_to(Path(root)))
    except ValueError:
        relative = str(transcript)
    path.write_text(
        json.dumps(
            {
                "sample": sample,
                "pid": pid,
                "started": started,
                "transcript": relative,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def clear(root: Path, sample: str) -> None:
    """Remove a session's pidfile. Safe to call when there is none."""
    pidfile(Path(root), sample).unlink(missing_ok=True)


def running(root: Path, sample: str | None = None) -> list[Running]:
    """Every session that recorded itself, alive or stale.

    Stale entries are returned rather than hidden: a pidfile whose process is
    gone means a session died without cleaning up, and that is worth seeing.

    Args:
        root: Project root.
        sample: Restrict to one sample.

    Returns:
        Sessions, sorted by sample.
    """
    directory = _directory(Path(root))
    if not directory.is_dir():
        return []

    found: list[Running] = []
    for path in sorted(directory.glob(f"*{PIDFILE_SUFFIX}")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            pid = int(payload["pid"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        name = str(payload.get("sample") or path.name[: -len(PIDFILE_SUFFIX)])
        if sample is not None and name != sample:
            continue
        found.append(
            Running(
                sample=name,
                pid=pid,
                started=str(payload.get("started") or "?"),
                transcript=str(payload.get("transcript") or "?"),
                alive=is_alive(pid),
            )
        )
    return found


def stop(root: Path, session: Running) -> str:
    """Stop one running session and everything it started.

    Args:
        root: Project root.
        session: The session to stop.

    Returns:
        A sentence saying what happened, for the caller to print.
    """
    from nr_workbench.agent.session import _kill_group

    if not session.alive:
        clear(Path(root), session.sample)
        return (
            f"{session.sample}: pid {session.pid} was already gone -- stale pidfile "
            "removed. The session died without cleaning up; its transcript is "
            f"{session.transcript}."
        )

    # The group, not the pid: the harness launches refl1d, and a fit that
    # outlives its session keeps writing into the project.
    _kill_group(session.pid)
    clear(Path(root), session.sample)
    return (
        f"{session.sample}: stopped pid {session.pid} and everything it started. "
        f"What it managed is in {session.transcript}; the sample is half-analysed, "
        "so write down where it got to before starting another."
    )


def signal_name(number: int) -> str:
    """Human name for a signal number, for the record."""
    try:
        return signal.Signals(number).name
    except ValueError:
        return str(number)
