"""One job at a time, for the Experiment page: a fit, run as child processes.

A fit takes minutes to hours of CPU, so it runs neither in the request nor in
the server's process: nothing could stop it there, and a crash in bumps would
take the page down with it. A job is a short list of ``nrw`` commands --
``model generate``, then ``fit run`` -- run one after another in the project,
each in a process group of its own, so that Cancel stops every process a step
started, bumps' parallel workers included.

One at a time: two fits compete for the same cores, and neither finishes sooner
than if it had waited its turn. What a job printed is kept in
``.nrw/jobs/<id>.log`` and what it was in ``<id>.json``, so the page can say
what became of a job after ``nrw serve`` was restarted under it.

Cancel sends SIGTERM, never SIGINT: bumps' DREAM takes a keyboard interrupt as
"stop sampling" and returns the chain so far, which ``nrw fit run`` would then
record as a finished fit.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import signal
import subprocess
import threading
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nr_workbench.web.models import nrw_command

#: Where jobs are kept, under the project. It ignores itself (see
#: :meth:`JobRunner.start`): the project's ``.gitignore`` learns the rule only
#: when ``nrw init`` runs again, and a DREAM log does not belong in git.
JOBS_DIR = ".nrw/jobs"

#: Seconds Cancel waits after SIGTERM before SIGKILL.
CANCEL_GRACE = 5.0

#: The most of a job's log one request returns, in bytes.
LOG_CHUNK = 256 * 1024

#: A job is ``running`` until it ends as one of these. ``detached``: it was
#: still running when ``nrw serve`` stopped without stopping it, so how it
#: ended is not known here -- a fit that finished is in Fits all the same.
ENDED = ("ok", "failed", "cancelled", "detached")

_log = logging.getLogger(__name__)


class JobBusy(Exception):
    """A job is running, and jobs run one at a time."""


class JobNotFound(LookupError):
    """No such job, or not the current one."""


@dataclass
class Job:
    """One job: what it runs, and how far it got.

    Attributes:
        id: Its id, also the name of its log.
        label: What the page calls it, e.g. ``dream fit of oxide``.
        sample: The sample it is for.
        model: The model it fits.
        steps: The ``nrw`` arguments of each step, run in order.
        status: ``running``, or one of :data:`ENDED`.
        step: The step running, or the last one that ran, from 1.
        exit_code: The last step's exit status.
        started_at: When it started, UTC.
        finished_at: When it ended, UTC.
        fit_id: The fit it recorded, once known.
    """

    id: str
    label: str
    sample: str
    model: str
    steps: list[list[str]]
    status: str = "running"
    step: int = 0
    exit_code: int | None = None
    started_at: str = ""
    finished_at: str = ""
    fit_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """The job as the page reads it."""
        return asdict(self)


class JobRunner:
    """Runs one job at a time in the project, and remembers the last.

    Args:
        root: Project root: the jobs' working directory.
        on_finished: Called with a job once it ends, from the job's thread --
            to find the fit it recorded.
    """

    def __init__(
        self, root: Path, *, on_finished: Callable[[Job], None] | None = None
    ) -> None:
        self.root = Path(root)
        self.directory = self.root / JOBS_DIR
        self.on_finished = on_finished
        self._lock = threading.Lock()
        self._process: subprocess.Popen[bytes] | None = None
        self._cancelled = False
        self._thread: threading.Thread | None = None
        self._job = self._last_recorded()

    def current(self) -> Job | None:
        """The job running, or the last one that ran: a copy, taken whole."""
        with self._lock:
            return replace(self._job) if self._job is not None else None

    def start(
        self, *, label: str, sample: str, model: str, steps: Sequence[Sequence[str]]
    ) -> Job:
        """Start a job, unless one is running.

        Raises:
            JobBusy: A job is running.
        """
        with self._lock:
            if self._job is not None and self._job.status == "running":
                raise JobBusy(
                    f"The {self._job.label} is still running, and one job runs at "
                    "a time. Cancel it, or wait for it to finish."
                )
            job = Job(
                id=datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ-") + secrets.token_hex(3),
                label=label,
                sample=sample,
                model=model,
                steps=[list(step) for step in steps],
                started_at=_now(),
            )
            self.directory.mkdir(parents=True, exist_ok=True)
            ignore = self.directory / ".gitignore"
            if not ignore.exists():
                ignore.write_text("*\n", encoding="utf-8")
            self._job = job
            self._cancelled = False
            self._save(job)
            self._thread = threading.Thread(
                target=self._run, args=(job,), name=f"nrw-job-{job.id}", daemon=True
            )
            self._thread.start()
            return job

    def cancel(self, job_id: str) -> Job:
        """Stop the running job: SIGTERM to its step's group, SIGKILL if it lingers.

        Returns:
            The job; ``cancelled`` once its thread has seen the step end.

        Raises:
            JobNotFound: *job_id* is not the current job.
        """
        with self._lock:
            job = self._job
            if job is None or job.id != job_id:
                raise JobNotFound(f"No job {job_id!r} is running here.")
            if job.status != "running":
                return job
            self._cancelled = True
            process = self._process
        if process is not None:
            _stop(process)
        return job

    def stop(self) -> Job | None:
        """Cancel whatever is running, as ``nrw serve`` stops; the job, if any."""
        job = self.current()
        if job is None or job.status != "running":
            return None
        self.cancel(job.id)
        thread = self._thread
        if thread is not None:
            thread.join(CANCEL_GRACE + 1)
        return job

    def log(self, job_id: str, offset: int = 0) -> tuple[str, int]:
        """What the job printed, from byte *offset*; and the offset to read on from.

        Raises:
            JobNotFound: No log for *job_id*.
        """
        path = self._path(job_id, ".log")
        try:
            with path.open("rb") as handle:
                handle.seek(max(0, offset))
                chunk = handle.read(LOG_CHUNK)
        except FileNotFoundError as exc:
            raise JobNotFound(f"No job {job_id!r} has a log here.") from exc
        # A chunk may end inside a character: stop before it, and read it whole
        # next time.
        text = chunk.decode("utf-8", errors="ignore")
        return text, max(0, offset) + len(text.encode("utf-8"))

    # -- the job's own thread -------------------------------------------------

    def _run(self, job: Job) -> None:
        with self._path(job.id, ".log").open("ab") as log:
            for number, args in enumerate(job.steps, 1):
                process = self._launch(job, number, args, log)
                if process is None:
                    break
                code = process.wait()
                with self._lock:
                    self._process = None
                    job.exit_code = code
                    if self._cancelled or code != 0:
                        break
            with self._lock:
                cancelled = self._cancelled
            if cancelled:
                log.write(b"\nCancelled.\n")
        ended = "cancelled" if cancelled else "ok" if job.exit_code == 0 else "failed"
        # Before the status changes: a page that sees the job end should find
        # the fit it recorded with it.
        if self.on_finished is not None:
            try:
                self.on_finished(job)
            except Exception:
                _log.exception("Finding what job %s recorded failed", job.id)
        with self._lock:
            job.status = ended
            job.finished_at = _now()
            self._save(job)

    def _launch(
        self, job: Job, number: int, args: list[str], log: Any
    ) -> subprocess.Popen[bytes] | None:
        """Start step *number*, unless the job was cancelled; ``None`` if not."""
        with self._lock:
            if self._cancelled:
                return None
            job.step = number
            log.write(f"$ nrw {' '.join(args)}\n".encode())
            log.flush()
            try:
                self._process = subprocess.Popen(
                    nrw_command(*args),
                    cwd=self.root,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    # Its own group, so Cancel reaches every process the step
                    # starts -- and Ctrl-C on nrw serve does not.
                    start_new_session=True,
                )
            except OSError as exc:
                log.write(f"could not start: {exc}\n".encode())
                job.exit_code = None
                return None
            self._save(job)
            return self._process

    # -- records ---------------------------------------------------------------

    def _path(self, job_id: str, suffix: str) -> Path:
        # Ids are made here, but the page sends them back: never a path.
        if not job_id or not all(c.isalnum() or c in "-_" for c in job_id):
            raise JobNotFound(f"No job {job_id!r} here.")
        return self.directory / f"{job_id}{suffix}"

    def _save(self, job: Job) -> None:
        path = self._path(job.id, ".json")
        partial = path.with_suffix(".json.partial")
        partial.write_text(json.dumps(job.as_dict(), indent=2) + "\n", encoding="utf-8")
        os.replace(partial, path)

    def _last_recorded(self) -> Job | None:
        """The newest job on disk; marked ``detached`` if it never ended."""
        try:
            newest = max(self.directory.glob("*.json"), key=lambda p: p.name)
        except (ValueError, OSError):
            return None
        try:
            job = Job(**json.loads(newest.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            return None
        if job.status == "running":
            # The server that ran it stopped without stopping it. Its step may
            # still be running, or may have finished; nothing here knows which,
            # and a pid from another server's life is never signalled.
            job.status = "detached"
            self._save(job)
        return job


def _stop(process: subprocess.Popen[bytes]) -> None:
    """SIGTERM to the step's process group; SIGKILL if it outlives the grace."""
    _signal(process, signal.SIGTERM)

    def kill_if_alive() -> None:
        if process.poll() is None:
            _signal(process, getattr(signal, "SIGKILL", signal.SIGTERM))

    timer = threading.Timer(CANCEL_GRACE, kill_if_alive)
    timer.daemon = True
    timer.start()


def _signal(process: subprocess.Popen[bytes], sig: int) -> None:
    if process.poll() is not None:
        return  # reaped: its pid may belong to someone else by now
    with suppress(ProcessLookupError, PermissionError):
        if hasattr(os, "killpg"):
            os.killpg(process.pid, sig)
        else:  # no process groups: the step itself
            process.send_signal(sig)


def _now() -> str:
    # The fit record's own format, so a job's start and its fit's compare.
    from nr_workbench.provenance.record import format_timestamp, utc_now

    return format_timestamp(utc_now())
