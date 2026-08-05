"""Tests for content hashing and the digest cache."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nr_workbench.provenance.hashing import (
    FileDigest,
    HashCache,
    canonical_json,
    digest_files,
    inputs_digest,
    sha256_bytes,
    sha256_file,
)


def write(path: Path, text: str) -> Path:
    """Write text to a path, creating parents."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_sha256_file_matches_sha256_bytes(tmp_path: Path) -> None:
    target = write(tmp_path / "a.txt", "hello\n")

    assert sha256_file(target) == sha256_bytes(b"hello\n")


def test_sha256_file_handles_content_larger_than_one_chunk(tmp_path: Path) -> None:
    """Chunked reading must produce the same digest as hashing in one go."""
    payload = b"x" * (3 * (1 << 20) + 17)
    target = tmp_path / "big.bin"
    target.write_bytes(payload)

    assert sha256_file(target) == sha256_bytes(payload)


def test_canonical_json_is_order_independent() -> None:
    """Equal data must hash equal, whatever order the keys arrived in."""
    assert canonical_json({"b": 1, "a": 2}) == canonical_json({"a": 2, "b": 1})


def test_digest_files_returns_paths_relative_to_the_root(tmp_path: Path) -> None:
    write(tmp_path / "data" / "a.txt", "one")

    digests = digest_files([("data:a", tmp_path / "data" / "a.txt")], root=tmp_path)

    assert digests[0].path == "data/a.txt"
    assert digests[0].role == "data:a"
    assert digests[0].bytes == 3


def test_digest_files_is_sorted_for_stable_records(tmp_path: Path) -> None:
    write(tmp_path / "b.txt", "b")
    write(tmp_path / "a.txt", "a")

    digests = digest_files(
        [("data:b", tmp_path / "b.txt"), ("data:a", tmp_path / "a.txt")], root=tmp_path
    )

    assert [d.role for d in digests] == ["data:a", "data:b"]


def test_digest_files_raises_on_a_missing_input(tmp_path: Path) -> None:
    """A fit cannot be recorded against inputs that are not there."""
    with pytest.raises(FileNotFoundError, match="gone.txt"):
        digest_files([("data:gone", tmp_path / "gone.txt")], root=tmp_path)


def test_inputs_digest_ignores_input_ordering() -> None:
    a = FileDigest("data:a", "a.txt", "aa", 1)
    b = FileDigest("data:b", "b.txt", "bb", 2)

    assert inputs_digest([a, b]) == inputs_digest([b, a])


def test_inputs_digest_changes_when_any_file_changes() -> None:
    a = FileDigest("data:a", "a.txt", "aa", 1)
    changed = FileDigest("data:a", "a.txt", "zz", 1)

    assert inputs_digest([a]) != inputs_digest([changed])


# --------------------------------------------------------------------------
# HashCache
# --------------------------------------------------------------------------


def test_cache_returns_the_same_digest_as_direct_hashing(tmp_path: Path) -> None:
    target = write(tmp_path / "a.txt", "hello")
    cache = HashCache(tmp_path / "cache.json")

    assert cache.digest(target) == sha256_file(target)


def test_cache_notices_a_changed_file(tmp_path: Path) -> None:
    """Keyed on (size, mtime_ns), so a rewrite must invalidate the entry."""
    target = write(tmp_path / "a.txt", "one")
    cache = HashCache(tmp_path / "cache.json")
    first = cache.digest(target)

    # Force a distinct mtime: a same-second rewrite can otherwise collide.
    write(target, "two")
    import os

    stat = target.stat()
    os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))

    assert cache.digest(target) != first
    assert cache.digest(target) == sha256_file(target)


def test_cache_persists_between_instances(tmp_path: Path) -> None:
    target = write(tmp_path / "a.txt", "hello")
    cache_path = tmp_path / "cache.json"

    first = HashCache(cache_path)
    expected = first.digest(target)
    first.save()

    assert HashCache(cache_path).digest(target) == expected
    assert json.loads(cache_path.read_text(encoding="utf-8"))


def test_cache_survives_corruption(tmp_path: Path) -> None:
    """A damaged cache costs a re-hash; it must not raise."""
    target = write(tmp_path / "a.txt", "hello")
    cache_path = tmp_path / "cache.json"
    cache_path.write_text("{ not json", encoding="utf-8")

    assert HashCache(cache_path).digest(target) == sha256_file(target)


def test_cache_without_a_path_still_hashes(tmp_path: Path) -> None:
    target = write(tmp_path / "a.txt", "hello")

    assert HashCache(None).digest(target) == sha256_file(target)
