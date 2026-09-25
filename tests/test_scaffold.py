"""Tests for the idempotent scaffold engine.

The engine's whole job is to be safe to re-run on top of a scientist's live
working directory, so the tests concentrate on the boundary between "we
installed this and may upgrade it" and "the user owns this, hands off".
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nr_workbench.project.scaffold import (
    NEW_SUFFIX,
    Outcome,
    PlannedFile,
    apply_scaffold,
    classify,
    load_lock,
    sha256_bytes,
)


def planned(
    relpath: str = "a.txt", content: bytes = b"hello\n", version: int = 1
) -> PlannedFile:
    """Build a PlannedFile for tests."""
    return PlannedFile(
        relpath=relpath,
        content=content,
        template_id=f"t/{relpath}",
        template_version=version,
    )


# --------------------------------------------------------------------------
# classify
# --------------------------------------------------------------------------


def test_classify_absent_file_returns_create(tmp_path: Path) -> None:
    outcome = classify(planned(), tmp_path / "a.txt", None)
    assert outcome is Outcome.CREATE


def test_classify_identical_content_returns_unchanged(tmp_path: Path) -> None:
    target = tmp_path / "a.txt"
    target.write_bytes(b"hello\n")

    outcome = classify(
        planned(), target, {"sha256_at_install": sha256_bytes(b"hello\n")}
    )

    assert outcome is Outcome.UNCHANGED


def test_classify_identical_content_without_lock_returns_unchanged(
    tmp_path: Path,
) -> None:
    """Matching bytes need no lock entry -- there is nothing to do either way."""
    target = tmp_path / "a.txt"
    target.write_bytes(b"hello\n")

    assert classify(planned(), target, None) is Outcome.UNCHANGED


def test_classify_unmodified_since_install_returns_upgrade(tmp_path: Path) -> None:
    """We wrote v1, the user did not touch it, the template moved to v2."""
    target = tmp_path / "a.txt"
    target.write_bytes(b"old\n")

    outcome = classify(
        planned(content=b"new\n", version=2),
        target,
        {"sha256_at_install": sha256_bytes(b"old\n")},
    )

    assert outcome is Outcome.UPGRADE


def test_classify_user_edited_file_returns_drifted(tmp_path: Path) -> None:
    target = tmp_path / "a.txt"
    target.write_bytes(b"user edited this\n")

    outcome = classify(
        planned(content=b"new\n"),
        target,
        {"sha256_at_install": sha256_bytes(b"old\n")},
    )

    assert outcome is Outcome.DRIFTED


def test_classify_unknown_preexisting_file_returns_untracked(tmp_path: Path) -> None:
    """A file we never installed belongs to the user, whatever it contains."""
    target = tmp_path / "a.txt"
    target.write_bytes(b"pre-existing\n")

    assert classify(planned(content=b"new\n"), target, None) is Outcome.UNTRACKED


# --------------------------------------------------------------------------
# apply_scaffold
# --------------------------------------------------------------------------


def test_apply_creates_files_and_writes_lock(tmp_path: Path) -> None:
    report = apply_scaffold(
        tmp_path, [planned("dir/a.txt"), planned("b.txt", b"two\n")]
    )

    assert (tmp_path / "dir/a.txt").read_bytes() == b"hello\n"
    assert (tmp_path / "b.txt").read_bytes() == b"two\n"
    assert report.count(Outcome.CREATE) == 2

    lock = load_lock(tmp_path / ".nrw" / "scaffold.lock.json")
    assert set(lock) == {"dir/a.txt", "b.txt"}
    assert lock["b.txt"]["sha256_at_install"] == sha256_bytes(b"two\n")


def test_apply_twice_changes_nothing(tmp_path: Path) -> None:
    """The core idempotency guarantee: a second run must not touch any byte."""
    files = [planned("a.txt"), planned("sub/b.txt", b"two\n")]
    apply_scaffold(tmp_path, files)
    before = {p: p.read_bytes() for p in sorted(tmp_path.rglob("*")) if p.is_file()}

    report = apply_scaffold(tmp_path, files)

    after = {p: p.read_bytes() for p in sorted(tmp_path.rglob("*")) if p.is_file()}
    assert report.changed == []
    assert report.count(Outcome.UNCHANGED) == 2
    # The lock records a timestamp, so compare only the installed files.
    lock = tmp_path / ".nrw" / "scaffold.lock.json"
    assert {k: v for k, v in after.items() if k != lock} == {
        k: v for k, v in before.items() if k != lock
    }


def test_apply_upgrades_untouched_file_when_template_changes(tmp_path: Path) -> None:
    apply_scaffold(tmp_path, [planned(content=b"v1\n", version=1)])

    report = apply_scaffold(tmp_path, [planned(content=b"v2\n", version=2)])

    assert (tmp_path / "a.txt").read_bytes() == b"v2\n"
    assert report.count(Outcome.UPGRADE) == 1


def test_apply_never_overwrites_user_edits(tmp_path: Path) -> None:
    """The rule that makes `init` safe on a live working directory."""
    apply_scaffold(tmp_path, [planned(content=b"v1\n")])
    (tmp_path / "a.txt").write_bytes(b"MY OWN NOTES\n")

    report = apply_scaffold(tmp_path, [planned(content=b"v2\n", version=2)])

    assert (tmp_path / "a.txt").read_bytes() == b"MY OWN NOTES\n"
    assert (tmp_path / f"a.txt{NEW_SUFFIX}").read_bytes() == b"v2\n"
    assert [f.relpath for f in report.drifted] == ["a.txt"]
    assert report.files[0].wrote_alongside == f"a.txt{NEW_SUFFIX}"


def test_apply_leaves_untracked_preexisting_files_alone(tmp_path: Path) -> None:
    """`init` must be safe to run on top of an existing beamtime folder."""
    (tmp_path / "a.txt").write_bytes(b"someone else's file\n")

    report = apply_scaffold(tmp_path, [planned(content=b"ours\n")])

    assert (tmp_path / "a.txt").read_bytes() == b"someone else's file\n"
    assert report.count(Outcome.UNTRACKED) == 1
    assert not (tmp_path / f"a.txt{NEW_SUFFIX}").exists()


def test_apply_dry_run_writes_nothing(tmp_path: Path) -> None:
    report = apply_scaffold(tmp_path, [planned()], dry_run=True)

    assert not (tmp_path / "a.txt").exists()
    assert not (tmp_path / ".nrw").exists()
    assert report.dry_run is True
    assert report.count(Outcome.CREATE) == 1


def test_apply_force_overwrites_and_backs_up(tmp_path: Path) -> None:
    apply_scaffold(tmp_path, [planned(content=b"v1\n")])
    (tmp_path / "a.txt").write_bytes(b"MY OWN NOTES\n")

    report = apply_scaffold(tmp_path, [planned(content=b"v2\n", version=2)], force=True)

    assert (tmp_path / "a.txt").read_bytes() == b"v2\n"
    assert report.drifted == []
    backups = list((tmp_path / ".nrw" / "backups").rglob("a.txt"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == b"MY OWN NOTES\n"


def test_apply_collects_diffs_when_requested(tmp_path: Path) -> None:
    apply_scaffold(tmp_path, [planned(content=b"v1\n")])
    sink: list[str] = []

    apply_scaffold(
        tmp_path,
        [planned(content=b"v2\n", version=2)],
        dry_run=True,
        show_diff=True,
        diff_sink=sink,
    )

    assert len(sink) == 1
    assert "-v1" in sink[0]
    assert "+v2" in sink[0]


def test_apply_recovers_from_corrupt_lock(tmp_path: Path) -> None:
    """A damaged lock must not brick `init`; it degrades to conservative.

    With no usable lock we cannot tell "we installed this" from "the user wrote
    this", so every differing file reads as the user's and is left alone. The
    corrupt lock is replaced with a valid one so the project recovers.
    """
    apply_scaffold(tmp_path, [planned(content=b"v1\n")])
    lock = tmp_path / ".nrw" / "scaffold.lock.json"
    lock.write_text("{ not json", encoding="utf-8")

    report = apply_scaffold(
        tmp_path, [planned(content=b"v2\n", version=2)], rebuild_lock=True
    )

    assert (tmp_path / "a.txt").read_bytes() == b"v1\n"
    assert report.count(Outcome.UNTRACKED) == 1
    assert (
        json.loads(lock.read_text(encoding="utf-8"))["schema"] == "nrw-scaffold-lock/1"
    )


def test_apply_reclaims_matching_files_after_lock_loss(tmp_path: Path) -> None:
    """A file whose bytes match what we would install is provably ours.

    This is how a project recovers from a lost lock: identical content is
    re-recorded, so the next template bump can upgrade it normally instead of
    treating it as user-owned forever.
    """
    apply_scaffold(tmp_path, [planned(content=b"v1\n")])
    lock = tmp_path / ".nrw" / "scaffold.lock.json"
    lock.unlink()

    apply_scaffold(tmp_path, [planned(content=b"v1\n")])

    assert "a.txt" in load_lock(lock)


@pytest.mark.parametrize(
    ("old", "new"),
    [
        pytest.param(
            b"\x00\x01\x02", b"\x00\x01\x03", id="nul-bytes-decode-but-are-not-text"
        ),
        pytest.param(b"\xff\xfe\x01", b"\xff\xfe\x02", id="invalid-utf8"),
    ],
)
def test_apply_binary_content_diff_does_not_mangle_output(
    tmp_path: Path, old: bytes, new: bytes
) -> None:
    """Diffing must degrade for binary payloads rather than raise or garble.

    NUL-containing content decodes as UTF-8 perfectly well -- control bytes are
    valid code points -- so a try/except around .decode() is not enough to
    detect it.
    """
    apply_scaffold(tmp_path, [planned("logo.bin", old)])
    sink: list[str] = []

    apply_scaffold(
        tmp_path,
        [planned("logo.bin", new, version=2)],
        dry_run=True,
        show_diff=True,
        diff_sink=sink,
    )

    assert "binary file" in sink[0]


@pytest.mark.parametrize("relpath", ["a.txt", "deep/nested/dir/file.md"])
def test_apply_creates_parent_directories(tmp_path: Path, relpath: str) -> None:
    apply_scaffold(tmp_path, [planned(relpath)])

    assert (tmp_path / relpath).is_file()


def test_apply_refuses_a_damaged_lock_unless_asked_to_rebuild(tmp_path: Path) -> None:
    """A partial plan over a lock that failed to load would drop every other entry."""
    from nr_workbench.project.scaffold import LockProblemError

    apply_scaffold(tmp_path, [planned(content=b"v1\n")])
    lock = tmp_path / ".nrw" / "scaffold.lock.json"
    lock.write_text("{ not json", encoding="utf-8")

    with pytest.raises(LockProblemError):
        apply_scaffold(tmp_path, [planned(content=b"v2\n", version=2)])
    assert lock.read_text(encoding="utf-8") == "{ not json"


def test_even_a_rebuild_refuses_git_conflict_markers(tmp_path: Path) -> None:
    """Two people's entries: a rebuild would keep neither side's samples."""
    from nr_workbench.project.scaffold import LockProblemError

    apply_scaffold(tmp_path, [planned(content=b"v1\n")])
    lock = tmp_path / ".nrw" / "scaffold.lock.json"
    conflicted = "<<<<<<< HEAD\n" + lock.read_text() + "=======\n>>>>>>> other\n"
    lock.write_text(conflicted, encoding="utf-8")

    with pytest.raises(LockProblemError, match="conflict markers"):
        apply_scaffold(tmp_path, [planned(content=b"v2\n")], rebuild_lock=True)
    assert lock.read_text(encoding="utf-8") == conflicted
