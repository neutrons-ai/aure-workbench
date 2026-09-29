"""The page's job runner: steps in order, one job at a time, and Cancel.

Each step here is a small Python program standing in for an ``nrw`` command,
so what is tested is the runner: order, output, stopping, and what a restart
finds. The real commands are run end to end in ``test_web_models.py``.
"""

from __future__ import annotations

import json
import os
import sys
import textwrap
import time
from pathlib import Path

import pytest

from nr_workbench.web import jobs as jobs_module
from nr_workbench.web.jobs import JobBusy, JobNotFound, JobRunner


@pytest.fixture
def programs(monkeypatch):
    """Run each step's first argument as a Python program, not as nrw."""
    monkeypatch.setattr(
        jobs_module,
        "nrw_command",
        lambda code, *rest: [sys.executable, "-c", textwrap.dedent(code), *rest],
    )


def ended(runner: JobRunner, timeout: float = 20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = runner.current()
        if job is not None and job.status != "running":
            return job
        time.sleep(0.05)
    raise AssertionError(f"the job did not end: {runner.current()}")


def wait_for(path: Path, timeout: float = 20) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file() and path.read_text():
            return path.read_text()
        time.sleep(0.05)
    raise AssertionError(f"{path} never appeared")


def start(runner: JobRunner, *steps: list[str]):
    return runner.start(label="amoeba fit of m", sample="S1", model="m", steps=steps)


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_the_steps_run_in_order_and_what_they_print_is_kept(
    tmp_path: Path, programs
) -> None:
    runner = JobRunner(tmp_path)

    job = start(runner, ["print('generated')"], ["print('fitted')"])
    done = ended(runner)
    text, offset = runner.log(job.id)

    assert (done.status, done.step, done.exit_code) == ("ok", 2, 0)
    assert text.index("generated") < text.index("fitted")
    assert text.startswith("$ nrw print('generated')\n")
    # Read on from where the page left off: nothing new.
    assert runner.log(job.id, offset) == ("", offset)
    recorded = json.loads((tmp_path / ".nrw" / "jobs" / f"{job.id}.json").read_text())
    assert recorded["status"] == "ok"


def test_the_jobs_are_kept_out_of_git_in_any_project(tmp_path: Path, programs) -> None:
    import subprocess

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    runner = JobRunner(tmp_path)
    start(runner, ["print('a long DREAM log')"])
    ended(runner)

    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout

    assert ".nrw/jobs" not in status


def test_a_step_that_fails_stops_the_job(tmp_path: Path, programs) -> None:
    runner = JobRunner(tmp_path)

    job = start(runner, ["import sys; print('no'); sys.exit(3)"], ["print('never')"])
    done = ended(runner)

    assert (done.status, done.step, done.exit_code) == ("failed", 1, 3)
    assert "$ nrw print('never')" not in runner.log(job.id)[0]


def test_cancel_stops_the_step_and_every_process_it_started(
    tmp_path: Path, programs
) -> None:
    # A step that starts a worker of its own, as bumps does with --parallel.
    pids = tmp_path / "pids"
    step = f"""
        import os, subprocess, sys, time
        worker = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        open({str(pids)!r}, "w").write(f"{{os.getpid()}} {{worker.pid}}")
        time.sleep(60)
    """
    runner = JobRunner(tmp_path)
    job = start(runner, [step], ["print('never')"])
    parent, worker = map(int, wait_for(pids).split())

    runner.cancel(job.id)
    done = ended(runner)

    assert done.status == "cancelled"
    assert done.step == 1
    deadline = time.monotonic() + 10
    while (alive(parent) or alive(worker)) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not alive(parent) and not alive(worker)
    assert runner.log(job.id)[0].rstrip().endswith("Cancelled.")


def test_cancel_sends_sigterm_never_the_interrupt_dream_takes_as_done(
    tmp_path: Path, programs
) -> None:
    # bumps' DREAM catches KeyboardInterrupt and returns the chain so far,
    # which `nrw fit run` would record as a finished fit.
    heard = tmp_path / "heard"
    step = f"""
        import signal, sys, time
        def note(number, frame):
            open({str(heard)!r}, "w").write(signal.Signals(number).name)
            sys.exit(0)
        signal.signal(signal.SIGINT, note)
        signal.signal(signal.SIGTERM, note)
        open({str(heard)!r} + ".ready", "w").write("ready")
        time.sleep(60)
    """
    runner = JobRunner(tmp_path)
    job = start(runner, [step])
    wait_for(Path(f"{heard}.ready"))

    runner.cancel(job.id)
    ended(runner)

    assert wait_for(heard) == "SIGTERM"


def test_one_job_runs_at_a_time(tmp_path: Path, programs) -> None:
    runner = JobRunner(tmp_path)
    job = start(runner, ["import time; time.sleep(60)"])

    with pytest.raises(JobBusy, match="one job runs at a time"):
        start(runner, ["print('second')"])

    runner.cancel(job.id)
    ended(runner)
    start(runner, ["print('second')"])
    assert ended(runner).status == "ok"


def test_stopping_the_server_stops_its_job(tmp_path: Path, programs) -> None:
    runner = JobRunner(tmp_path)
    start(runner, ["import time; time.sleep(60)"])

    stopped = runner.stop()

    assert stopped is not None
    assert runner.current().status == "cancelled"
    assert runner.stop() is None  # nothing left to stop


def test_a_job_no_server_saw_end_is_detached_and_never_signalled(
    tmp_path: Path, monkeypatch
) -> None:
    directory = tmp_path / ".nrw" / "jobs"
    directory.mkdir(parents=True)
    record = {
        "id": "20260929T000000Z-abcdef",
        "label": "dream fit of m",
        "sample": "S1",
        "model": "m",
        "steps": [["fit", "run", "samples/S1/models/m.py"]],
        "status": "running",
        "step": 1,
    }
    (directory / f"{record['id']}.json").write_text(json.dumps(record))
    signalled = []
    monkeypatch.setattr(os, "killpg", lambda *args: signalled.append(args))

    runner = JobRunner(tmp_path)

    assert runner.current().status == "detached"
    on_disk = json.loads((directory / f"{record['id']}.json").read_text())
    assert on_disk["status"] == "detached"
    assert runner.stop() is None and signalled == []


@pytest.mark.parametrize("job_id", ["../secret", "a/b", "", "x.json"])
def test_a_job_id_from_the_page_is_never_a_path(tmp_path: Path, job_id: str) -> None:
    # A file where "../secret" would lead: read, it would be served as a log.
    (tmp_path / ".nrw" / "jobs").mkdir(parents=True)
    (tmp_path / ".nrw" / "secret.log").write_text("not a job's")
    runner = JobRunner(tmp_path)

    with pytest.raises(JobNotFound):
        runner.log(job_id)
    with pytest.raises(JobNotFound):
        runner.cancel(job_id)


def test_nrw_serve_stops_the_job_it_ran_when_it_stops(
    tmp_path: Path, programs, monkeypatch
) -> None:
    from click.testing import CliRunner
    from flask import Flask

    from nr_workbench.cli import main

    root = tmp_path / "proj"
    assert CliRunner().invoke(main, ["init", str(root)]).exit_code == 0
    started = {}

    def run(self, **kwargs) -> None:
        # What a person does while the server runs: start a fit, then Ctrl-C.
        started["runner"] = self.config["NRW_MODELS"].jobs
        start(started["runner"], ["import time; time.sleep(60)"])

    monkeypatch.setattr(Flask, "run", run)
    monkeypatch.setenv("NRW_SERVE_TOKEN", "")

    result = CliRunner().invoke(main, ["serve", "--root", str(root)])

    assert result.exit_code == 0, result.output
    assert "Stopped the amoeba fit of m." in result.output
    assert started["runner"].current().status == "cancelled"
