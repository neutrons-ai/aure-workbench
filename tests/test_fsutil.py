"""The shared lock and atomic write.

The scaffold lock, the fit index, the experiment catalog and each sample's copy
record all go through these, from request threads and terminals at once, so
what matters is what a reader or a second writer can observe.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest

from nr_workbench import fsutil
from nr_workbench.fsutil import advisory_lock, atomic_write_bytes


def test_atomic_write_bytes_replaces_and_leaves_nothing_behind(tmp_path: Path) -> None:
    target = tmp_path / "scaffold.lock.json"
    target.write_bytes(b"old\n")

    atomic_write_bytes(target, b"new\n")

    assert target.read_bytes() == b"new\n"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["scaffold.lock.json"]


def test_atomic_write_bytes_that_fails_keeps_the_old_file_whole(
    tmp_path: Path, monkeypatch
) -> None:
    target = tmp_path / "sources.json"
    target.write_bytes(b"old\n")

    def failing(source, destination):
        raise OSError("disk full")

    monkeypatch.setattr(fsutil.os, "replace", failing)

    with pytest.raises(OSError, match="disk full"):
        atomic_write_bytes(target, b"new\n")

    assert target.read_bytes() == b"old\n"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["sources.json"]


def test_atomic_write_bytes_puts_its_temp_file_in_scratch(
    tmp_path: Path, monkeypatch
) -> None:
    """For a committed directory, where a stray temp file would be committed too."""
    committed, scratch = tmp_path / "experiment", tmp_path / "cache"
    committed.mkdir()
    seen: list[Path] = []
    real = os.replace

    def watching(source, destination):
        seen.append(Path(source))
        real(source, destination)

    monkeypatch.setattr(fsutil.os, "replace", watching)

    atomic_write_bytes(committed / "runs.parquet", b"x", scratch=scratch)

    assert [p.parent for p in seen] == [scratch]
    assert [p.name for p in committed.iterdir()] == ["runs.parquet"]


def test_two_writers_never_share_a_temp_file(tmp_path: Path, monkeypatch) -> None:
    """One shared ".tmp" name let two writers rename each other's half-written file."""
    names: list[str] = []
    real = os.replace

    def recording(source, destination):
        names.append(Path(source).name)
        real(source, destination)

    monkeypatch.setattr(fsutil.os, "replace", recording)
    target = tmp_path / "scaffold.lock.json"

    atomic_write_bytes(target, b"a")
    atomic_write_bytes(target, b"b")

    assert len(set(names)) == 2


@pytest.mark.skipif(os.name == "nt", reason="no fcntl on Windows")
def test_advisory_lock_excludes_a_second_holder(tmp_path: Path) -> None:
    held, attempted, acquired = threading.Event(), threading.Event(), []
    guarded = tmp_path / "catalog"

    def second() -> None:
        attempted.set()
        with advisory_lock(guarded):
            acquired.append(held.is_set())

    with advisory_lock(guarded):
        other = threading.Thread(target=second)
        other.start()
        assert attempted.wait(timeout=5)
        other.join(timeout=0.2)
        assert other.is_alive() and acquired == []
        held.set()
    other.join(timeout=5)

    assert acquired == [True]
