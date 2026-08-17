"""Seeing and stopping an unattended session.

A session could be started and could not be stopped: ending one meant finding it
with `ps`, reading the prompt path out of its command line to work out which
sample it was on, and killing the pid by hand -- while a fit was writing into
the project.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from nr_workbench.agent import running as run_mod
from nr_workbench.cli import main

pytestmark = pytest.mark.integration


def invoke(root: Path, monkeypatch: pytest.MonkeyPatch, *args: str):
    monkeypatch.chdir(root)
    return CliRunner().invoke(main, list(args))


def test_a_recorded_session_is_reported(project: Path, monkeypatch) -> None:
    run_mod.record(
        project, "S1", os.getpid(), "2026-08-11T20:00:00Z", project / "t.jsonl"
    )

    result = invoke(project, monkeypatch, "agent", "status")

    assert result.exit_code == 0, result.output
    assert "S1" in result.output
    assert "running" in result.output


def test_a_dead_pid_reads_as_stale_not_as_running(project: Path) -> None:
    """A crashed session leaves its pidfile; that must not look like a live run."""
    run_mod.record(project, "S1", 2**30, "2026-08-11T20:00:00Z", project / "t.jsonl")

    sessions = run_mod.running(project)

    assert len(sessions) == 1
    assert sessions[0].alive is False


def test_stopping_a_stale_session_clears_it(project: Path, monkeypatch) -> None:
    run_mod.record(project, "S1", 2**30, "2026-08-11T20:00:00Z", project / "t.jsonl")

    result = invoke(project, monkeypatch, "agent", "stop", "S1")

    assert result.exit_code == 0, result.output
    assert "already gone" in result.output
    assert run_mod.running(project) == []


def test_stopping_when_nothing_runs_says_so(project: Path, monkeypatch) -> None:
    result = invoke(project, monkeypatch, "agent", "stop", "S1")

    assert result.exit_code != 0
    assert "No session recorded as running" in result.output


def test_status_with_nothing_running(project: Path, monkeypatch) -> None:
    result = invoke(project, monkeypatch, "agent", "status")

    assert result.exit_code == 0
    assert "No unattended session is running" in result.output


def test_a_second_session_on_one_sample_is_refused(project: Path) -> None:
    """Two sessions interleave their fits and neither record survives it."""
    from nr_workbench.agent.session import SessionError, run

    notes = project / "samples" / "Sample1" / "sample.md"
    notes.write_text(
        "# S\n\n## Fits to perform\n\nCo-refine the runs.\n", encoding="utf-8"
    )
    run_mod.record(project, "Sample1", os.getpid(), "2026-08-11T20:00:00Z", notes)

    with pytest.raises(SessionError, match="already analysing"):
        run(project, "Sample1")


def test_a_stale_pidfile_does_not_lock_the_sample_out(project: Path) -> None:
    """A crashed session must not make its sample permanently unanalysable."""
    from nr_workbench.agent.session import _require_not_already_running

    run_mod.record(project, "Sample1", 2**30, "2026-08-11T20:00:00Z", project / "t")

    _require_not_already_running(project, "Sample1")  # must not raise

    assert run_mod.running(project, "Sample1") == []


def test_the_pidfile_is_written_by_the_real_call_site(
    project: Path, monkeypatch
) -> None:
    """Stub the boundary to the OS, not the function under test.

    The crash this pins was a `datetime` passed where a `str` was declared, so
    `json.dumps` blew up the moment a session started. Every existing test
    replaced `_stream` wholesale or called `record()` with a literal string, so
    the one path that carries a real timestamp -- run() -> _stream() -> record()
    -- was exercised nowhere, and new code went straight into it.

    So this fakes `subprocess.Popen` and lets the production line run.
    """
    import json
    import subprocess

    from nr_workbench.agent import session as session_mod

    notes = project / "samples" / "Sample1" / "sample.md"
    notes.write_text(
        "# S\n\n## Fits to perform\n\nCo-refine the runs.\n", encoding="utf-8"
    )
    seen: dict[str, str] = {}

    class FakeStdout:
        """Enough of a pipe for `_stream`: iterable, and closeable.

        Reads the pidfile while being iterated, which is the only window it
        exists in -- `_stream` clears it in its `finally`, before `wait()`.
        """

        def __iter__(self):
            seen.update(
                json.loads(
                    run_mod.pidfile(project, "Sample1").read_text(encoding="utf-8")
                )
            )
            return iter(())

        def close(self) -> None:
            pass

    class FakeProcess:
        pid = os.getpid()
        stdout = FakeStdout()

        def wait(self) -> int:
            return 0

    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: FakeProcess())
    monkeypatch.setattr(session_mod, "resolve_harness", lambda *_: ["true"])

    session_mod.run(project, "Sample1")

    assert seen["sample"] == "Sample1"
    assert isinstance(seen["started"], str), "a datetime here is the crash"
    assert seen["started"].endswith("Z"), seen["started"]
    assert run_mod.running(project) == [], "cleared on the way out"


def test_a_failure_after_spawning_does_not_leak_the_harness(
    project: Path, monkeypatch
) -> None:
    """The damage the pidfile crash actually did.

    The TypeError fired *after* `Popen` had succeeded, so the harness was already
    alive. The parent `nrw` died with a traceback, the child was reparented to
    init, and it kept running unattended on the sample -- no pidfile, invisible
    to `nrw agent status`, still writing results. The traceback made it look as
    though nothing had started.

    So the invariant is not "the pidfile write must not fail". It is: if
    anything raises after spawning, the spawned process group dies with it.
    """
    import subprocess

    from nr_workbench.agent import session as session_mod

    notes = project / "samples" / "Sample1" / "sample.md"
    notes.write_text(
        "# S\n\n## Fits to perform\n\nCo-refine the runs.\n", encoding="utf-8"
    )
    killed: list[int] = []

    class FakeStdout:
        def __iter__(self):
            raise RuntimeError("anything at all, after the process exists")

        def close(self) -> None:
            pass

    class FakeProcess:
        pid = 4242
        stdout = FakeStdout()

        def wait(self) -> int:
            return 0

    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: FakeProcess())
    monkeypatch.setattr(session_mod, "resolve_harness", lambda *_: ["true"])
    monkeypatch.setattr(session_mod, "_kill_group", killed.append)

    with pytest.raises(RuntimeError):
        session_mod.run(project, "Sample1")

    assert 4242 in killed, "the harness was left running"
    assert run_mod.running(project) == [], "and its pidfile was left behind"


def test_a_pidfile_that_cannot_be_written_does_not_kill_the_session(
    project: Path, monkeypatch
) -> None:
    """A pidfile is a convenience; the session is the work.

    Losing `nrw agent stop` for one run is a far smaller cost than refusing to
    analyse the sample -- so the write is wrapped separately from the invariant
    above.
    """
    import subprocess

    from nr_workbench.agent import running as running_mod
    from nr_workbench.agent import session as session_mod

    notes = project / "samples" / "Sample1" / "sample.md"
    notes.write_text(
        "# S\n\n## Fits to perform\n\nCo-refine the runs.\n", encoding="utf-8"
    )

    class FakeStdout:
        def __iter__(self):
            return iter(())

        def close(self) -> None:
            pass

    class FakeProcess:
        pid = os.getpid()
        stdout = FakeStdout()

        def wait(self) -> int:
            return 0

    def explode(*args, **kwargs):
        raise OSError("read-only filesystem")

    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: FakeProcess())
    monkeypatch.setattr(session_mod, "resolve_harness", lambda *_: ["true"])
    monkeypatch.setattr(running_mod, "record", explode)

    result = session_mod.run(project, "Sample1")  # must not raise

    assert result.returncode == 0
