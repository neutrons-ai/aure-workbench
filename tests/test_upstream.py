"""The vendored-file drift checker.

The point of `upstream.toml` is that taking someone else's code creates an
obligation to notice when it changes. A manifest nobody checks is worse than
none, because it looks like the obligation is being met.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import sync_upstream  # noqa: E402


def write(path: Path, text: str) -> str:
    """Write a file and return its sha256."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return hashlib.sha256(text.encode()).hexdigest()


def manifest(tmp_path: Path, body: str) -> Path:
    """Write an upstream.toml into a temporary tree."""
    path = tmp_path / "upstream.toml"
    path.write_text(body, encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# The real manifest
# --------------------------------------------------------------------------


def test_the_repos_own_manifest_is_clean() -> None:
    """Every file this repo vendored is present and unmodified.

    Runs offline. This is the half of the check that catches an accidental
    edit to a file that is supposed to be a byte-identical copy.
    """
    report = sync_upstream.check(remote=False)

    assert report.ok, [f.as_dict() for f in report.findings]
    assert report.checked == 1


def test_every_manifest_entry_names_a_commit() -> None:
    """Without a commit there is nothing to compare upstream against.

    An entry with no commit is not tracked, it just looks tracked.
    """
    document = sync_upstream.load_manifest()

    untracked = [
        entry["dest"]
        for kind in ("verbatim", "adapted")
        for entry in document.get(kind, [])
        if not entry.get("commit") and not entry.get("commits")
    ]

    assert not untracked, f"entries with no upstream commit recorded: {untracked}"


def test_verbatim_entries_carry_a_hash() -> None:
    """A verbatim copy is a contract; the hash is how it is enforced."""
    document = sync_upstream.load_manifest()

    unhashed = [
        entry["dest"]
        for entry in document.get("verbatim", [])
        if not entry.get("sha256")
    ]

    assert not unhashed, f"verbatim entries with no sha256: {unhashed}"


# --------------------------------------------------------------------------
# Detection
# --------------------------------------------------------------------------


def test_a_local_edit_to_a_verbatim_copy_is_caught(tmp_path: Path) -> None:
    """Editing a byte-identical copy here is the failure this exists for."""
    digest = write(tmp_path / "v" / "shared.py", "original\n")
    path = manifest(
        tmp_path,
        f'[[verbatim]]\ndest = "v/shared.py"\nrepo = "o/r"\npath = "a.py"\n'
        f'commit = "abc"\nsha256 = "{digest}"\n',
    )
    (tmp_path / "v" / "shared.py").write_text("edited\n", encoding="utf-8")

    report = sync_upstream.check(manifest_path=path)

    assert not report.ok
    assert report.findings[0].problem == "local-edit"
    assert "never be edited" in report.findings[0].detail


def test_a_missing_file_is_caught(tmp_path: Path) -> None:
    """A manifest that records a file nobody vendored is a lie."""
    path = manifest(
        tmp_path,
        '[[adapted]]\ndest = "gone.py"\nrepo = "o/r"\npath = "a.py"\ncommit = "abc"\n',
    )

    report = sync_upstream.check(manifest_path=path)

    assert [f.problem for f in report.findings] == ["missing"]


def test_a_glob_that_matches_nothing_is_caught(tmp_path: Path) -> None:
    """One upstream source can fan out to several files; zero is a bug.

    A pattern destination cannot be hashed, so existence is the only check
    available -- and skipping it entirely would let a whole fan-out vanish.
    """
    path = manifest(
        tmp_path,
        '[[adapted]]\ndest = "skills/tnr-*"\nrepo = "o/r"\npaths = ["a.md"]\n'
        'commit = "abc"\n',
    )

    report = sync_upstream.check(manifest_path=path)

    assert [f.problem for f in report.findings] == ["missing"]
    assert "matches nothing" in report.findings[0].detail


def test_a_glob_that_matches_is_reported_as_skipped(tmp_path: Path) -> None:
    """Matching files cannot be hashed, but the fan-out is confirmed present."""
    write(tmp_path / "skills" / "tnr-a" / "SKILL.md", "x")
    write(tmp_path / "skills" / "tnr-b" / "SKILL.md", "y")
    path = manifest(
        tmp_path,
        '[[adapted]]\ndest = "skills/tnr-*"\nrepo = "o/r"\npaths = ["a.md"]\n'
        'commit = "abc"\n',
    )

    report = sync_upstream.check(manifest_path=path)

    assert report.ok
    assert any("2 match(es)" in note for note in report.skipped)


def test_upstream_movement_is_reported_without_changing_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Drift is information for a human, never an automatic merge.

    For `adapted` files especially: the adaptation notes say what we changed
    and why, and that reasoning is what decides whether to follow upstream.
    """
    digest = write(tmp_path / "a.py", "ours\n")
    path = manifest(
        tmp_path,
        f'[[adapted]]\ndest = "a.py"\nrepo = "o/r"\npath = "up.py"\n'
        f'commit = "0000000000000000000000000000000000000000"\nsha256 = "{digest}"\n',
    )
    monkeypatch.setattr(sync_upstream, "latest_commit", lambda repo, p: "f" * 40)

    report = sync_upstream.check(remote=True, manifest_path=path)

    assert [f.problem for f in report.findings] == ["upstream-moved"]
    assert "is now at ffffffffffff" in report.findings[0].detail
    assert (tmp_path / "a.py").read_text() == "ours\n", "must not touch the file"


def test_an_unreachable_upstream_is_skipped_not_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No network is not the same as drift, and must not fail a nightly job."""
    digest = write(tmp_path / "a.py", "ours\n")
    path = manifest(
        tmp_path,
        f'[[adapted]]\ndest = "a.py"\nrepo = "o/r"\npath = "up.py"\n'
        f'commit = "abc"\nsha256 = "{digest}"\n',
    )
    monkeypatch.setattr(sync_upstream, "latest_commit", lambda repo, p: None)

    report = sync_upstream.check(remote=True, manifest_path=path)

    assert report.ok
    assert any("unreachable" in note for note in report.skipped)


# --------------------------------------------------------------------------
# Multi-source entries
# --------------------------------------------------------------------------


def test_sources_handles_one_repo_with_several_paths() -> None:
    """The tNR skills came from six notes in one repository."""
    sources = sync_upstream._sources(
        {"repo": "o/r", "paths": ["a.md", "b.md"], "commit": "abc"}
    )

    assert sources == [("o/r", "a.md", "abc"), ("o/r", "b.md", "abc")]


def test_sources_handles_parallel_repos_and_paths() -> None:
    """The overview skill was assembled from two different repositories."""
    sources = sync_upstream._sources(
        {
            "repos": ["o/one", "o/two"],
            "paths": ["a.md", "b.md"],
            "commits": ["aaa", "bbb"],
        }
    )

    assert sources == [("o/one", "a.md", "aaa"), ("o/two", "b.md", "bbb")]


def test_sources_handles_the_ordinary_single_case() -> None:
    sources = sync_upstream._sources({"repo": "o/r", "path": "a.py", "commit": "abc"})

    assert sources == [("o/r", "a.py", "abc")]


def test_main_exits_nonzero_on_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CI needs a non-zero exit to open an issue on."""
    path = manifest(
        tmp_path,
        '[[adapted]]\ndest = "gone.py"\nrepo = "o/r"\npath = "a.py"\ncommit = "x"\n',
    )
    monkeypatch.setattr(sync_upstream, "MANIFEST", path)

    assert sync_upstream.main([]) == 1
    assert sync_upstream.main(["--json"]) == 1
