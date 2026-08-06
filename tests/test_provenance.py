"""Tests for the fit record, the append-only index, and figure stamping."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from nr_workbench.provenance.env import Environment, GitState
from nr_workbench.provenance.hashing import FileDigest
from nr_workbench.provenance.index import EVENT_PROMOTE, FitIndex
from nr_workbench.provenance.record import (
    FitDirectory,
    FitIdentity,
    FitRecord,
    create_unique,
    env_digest,
    make_fit_id,
    settings_digest,
)
from nr_workbench.provenance.stamp import read_stamp, stamp_file

MOMENT = datetime(2026, 8, 5, 14, 3, 11, tzinfo=UTC)


def make_identity(**overrides: str) -> FitIdentity:
    """Build a FitIdentity with sensible defaults."""
    values = {
        "script_sha256": "s" * 64,
        "inputs_digest": "i" * 64,
        "settings_digest": "t" * 64,
        "env_digest": "e" * 64,
    }
    values.update(overrides)
    return FitIdentity(**values)  # type: ignore[arg-type]


def make_record(**overrides: object) -> FitRecord:
    """Build a FitRecord with sensible defaults."""
    identity = overrides.pop("identity", make_identity())
    values: dict = {
        "fit_id": "20260805-140311Z-abcdef12",
        "sample": "Sample1",
        "model": "cu-d2o",
        "script_origin": "script",
        "identity": identity,
        "inputs": [FileDigest("script", "models/m.py", "s" * 64, 10)],
        "settings": {"method": "amoeba", "steps": 40},
        "environment": Environment(
            python="3.12.0", platform="linux", executable="/usr/bin/python"
        ),
        "started_at": "2026-08-05T14:03:11Z",
    }
    values.update(overrides)
    return FitRecord(**values)


# --------------------------------------------------------------------------
# Identity
# --------------------------------------------------------------------------


def test_fit_id_is_sortable_and_content_identifying() -> None:
    fit_id = make_fit_id(MOMENT, "abcdef1234567890")

    assert fit_id.startswith("20260805-140311Z-")
    assert fit_id.endswith("abcdef12")


def test_run_key_is_stable_for_identical_runs() -> None:
    assert make_identity().run_key == make_identity().run_key


@pytest.mark.parametrize(
    "field",
    ["script_sha256", "inputs_digest", "settings_digest", "env_digest"],
)
def test_run_key_changes_when_any_component_changes(field: str) -> None:
    """Each component must be able to make a run distinct on its own."""
    assert make_identity().run_key != make_identity(**{field: "z" * 64}).run_key


def test_settings_digest_ignores_key_order() -> None:
    assert settings_digest({"a": 1, "b": 2}) == settings_digest({"b": 2, "a": 1})


def test_env_digest_ignores_platform_and_interpreter_path() -> None:
    """Two machines running the same versions should agree.

    Platform strings and interpreter paths differ between a laptop and CI
    without implying a different answer, so they must not enter the digest.
    """
    base = Environment(
        python="3.12.0",
        platform="macOS",
        executable="/a/python",
        packages={"refl1d": "1.0.1"},
    )
    other = Environment(
        python="3.12.0",
        platform="Linux",
        executable="/b/python",
        packages={"refl1d": "1.0.1"},
    )

    assert env_digest(base) == env_digest(other)


def test_env_digest_changes_with_a_package_version() -> None:
    base = Environment(
        python="3.12.0", platform="x", executable="y", packages={"refl1d": "1.0.1"}
    )
    bumped = Environment(
        python="3.12.0", platform="x", executable="y", packages={"refl1d": "1.0.2"}
    )

    assert env_digest(base) != env_digest(bumped)


def test_env_digest_changes_with_the_aure_commit() -> None:
    """aure reports version 0.1.0 for every build, so the commit is the identity."""
    base = Environment(python="3.12.0", platform="x", executable="y", aure_commit="aaa")
    other = Environment(
        python="3.12.0", platform="x", executable="y", aure_commit="bbb"
    )

    assert env_digest(base) != env_digest(other)


# --------------------------------------------------------------------------
# FitDirectory
# --------------------------------------------------------------------------


def test_fit_directory_refuses_to_reuse_an_existing_directory(tmp_path: Path) -> None:
    """Fit directories are write-once; reusing one would destroy a record."""
    directory = FitDirectory(tmp_path / "fit1")
    directory.create()

    with pytest.raises(FileExistsError):
        directory.create()


def test_create_unique_disambiguates_a_same_second_collision(tmp_path: Path) -> None:
    """Two forced replicates started in the same second must both be recorded.

    A fit_id is a second-resolution timestamp plus a content hash, so a forced
    re-run of an identical fit collides. Failing there would lose the replicate.
    """
    first, first_id = create_unique(tmp_path, "20260805-140311Z-abcdef12")
    second, second_id = create_unique(tmp_path, "20260805-140311Z-abcdef12")

    assert first_id == "20260805-140311Z-abcdef12"
    assert second_id == "20260805-140311Z-abcdef12-2"
    assert first.path.is_dir() and second.path.is_dir()
    assert first.path != second.path


def test_create_unique_is_safe_under_concurrency(tmp_path: Path) -> None:
    """mkdir is atomic, so racing processes take successive suffixes."""
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(lambda _: create_unique(tmp_path, "same-id")[1], range(16))
        )

    assert len(set(results)) == 16
    assert len([p for p in tmp_path.iterdir() if p.is_dir()]) == 16


def test_fit_directory_writes_the_expected_skeleton(tmp_path: Path) -> None:
    directory = FitDirectory(tmp_path / "fit1")
    directory.create()

    for sub in ("env", "fit", "figures"):
        assert (tmp_path / "fit1" / sub).is_dir()


def test_manifest_nests_provenance_inside_the_shared_envelope(tmp_path: Path) -> None:
    """The outer schema must stay ndip-tool-result/1 so orchestrators can read it."""
    directory = FitDirectory(tmp_path / "fit1")
    directory.create()

    manifest = directory.write_manifest(make_record(chisq=1.83, n_free=7))

    assert manifest["schema"] == "ndip-tool-result/1"
    assert manifest["tool"] == "nrw-fit-run"
    assert manifest["info"]["chisq"] == 1.83
    assert manifest["provenance"]["schema"] == "nrw-provenance/1"
    assert manifest["provenance"]["fit_id"] == "20260805-140311Z-abcdef12"
    assert directory.read_manifest() == manifest


def test_environment_patch_is_written_only_when_git_is_dirty(tmp_path: Path) -> None:
    """A fit from a dirty tree records a commit that does not describe the code.

    The patch is what closes that gap.
    """
    clean = FitDirectory(tmp_path / "clean")
    clean.create()
    clean.write_environment(
        Environment(
            python="3", platform="x", executable="y", git=GitState(available=True)
        )
    )
    assert not (tmp_path / "clean" / "env" / "project.patch").exists()

    dirty = FitDirectory(tmp_path / "dirty")
    dirty.create()
    dirty.write_environment(
        Environment(
            python="3",
            platform="x",
            executable="y",
            git=GitState(available=True, dirty=True, patch="diff --git a/x b/x\n"),
        )
    )
    assert (
        (tmp_path / "dirty" / "env" / "project.patch")
        .read_text()
        .startswith("diff --git")
    )


def test_inputs_file_records_a_digest_over_all_inputs(tmp_path: Path) -> None:
    directory = FitDirectory(tmp_path / "fit1")
    directory.create()
    inputs = [
        FileDigest("script", "m.py", "a" * 64, 1),
        FileDigest("data:steady:x", "data/x.txt", "b" * 64, 2),
    ]

    directory.write_inputs(inputs)

    payload = json.loads(
        (tmp_path / "fit1" / "inputs.json").read_text(encoding="utf-8")
    )
    assert payload["schema"] == "nrw-inputs/1"
    assert len(payload["inputs"]) == 2
    assert payload["inputs_digest"]
    assert len(directory.read_inputs()) == 2


# --------------------------------------------------------------------------
# FitIndex
# --------------------------------------------------------------------------


def test_index_appends_one_line_per_entry(tmp_path: Path) -> None:
    index = FitIndex(tmp_path / "index.jsonl")

    index.append({"fit_id": "a"})
    index.append({"fit_id": "b"})

    lines = (tmp_path / "index.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert [json.loads(line)["fit_id"] for line in lines] == ["a", "b"]


def test_index_never_rewrites_earlier_entries(tmp_path: Path) -> None:
    """Append-only is what makes the index git-mergeable and lossless."""
    index = FitIndex(tmp_path / "index.jsonl")
    index.append({"fit_id": "a"})
    first = (tmp_path / "index.jsonl").read_text(encoding="utf-8")

    index.append({"fit_id": "b"})

    assert (tmp_path / "index.jsonl").read_text(encoding="utf-8").startswith(first)


def test_index_skips_a_malformed_line_without_losing_the_rest(tmp_path: Path) -> None:
    """One bad line must not make every other record unreadable."""
    path = tmp_path / "index.jsonl"
    path.write_text(
        '{"event":"fit","fit_id":"a"}\nnot json\n{"event":"fit","fit_id":"b"}\n',
        encoding="utf-8",
    )

    assert [e["fit_id"] for e in FitIndex(path).entries()] == ["a", "b"]


def test_index_returns_fits_newest_first(tmp_path: Path) -> None:
    index = FitIndex(tmp_path / "index.jsonl")
    index.append({"fit_id": "old", "started_at": "2026-01-01T00:00:00Z"})
    index.append({"fit_id": "new", "started_at": "2026-06-01T00:00:00Z"})

    assert [e["fit_id"] for e in index.fits()] == ["new", "old"]


def test_index_orders_same_second_fits_by_append_order(tmp_path: Path) -> None:
    """Two fits can land inside one second, and `started_at` cannot tell them
    apart. The append-only index knows the real order; `nrw ls` and every
    "latest fit" caller depend on it, so a tie must not fall back to *oldest*
    first -- which is what a stable sort with reverse=True quietly does."""
    index = FitIndex(tmp_path / "index.jsonl")
    same_second = "2026-01-01T00:00:00Z"
    index.append({"fit_id": "first", "started_at": same_second})
    index.append({"fit_id": "second", "started_at": same_second})
    index.append({"fit_id": "third", "started_at": same_second})

    assert [e["fit_id"] for e in index.fits()] == ["third", "second", "first"]


def test_index_filters_by_sample(tmp_path: Path) -> None:
    index = FitIndex(tmp_path / "index.jsonl")
    index.append({"fit_id": "a", "sample": "S1"})
    index.append({"fit_id": "b", "sample": "S2"})

    assert [e["fit_id"] for e in index.fits(sample="S2")] == ["b"]


def test_index_resolves_a_fit_id_prefix(tmp_path: Path) -> None:
    index = FitIndex(tmp_path / "index.jsonl")
    index.append({"fit_id": "20260805-140311Z-abcdef12"})

    assert len(index.resolve("20260805-1403")) == 1


def test_current_label_returns_the_latest_promotion(tmp_path: Path) -> None:
    index = FitIndex(tmp_path / "index.jsonl")
    index.append({"fit_id": "first", "label": "final"}, event=EVENT_PROMOTE)
    index.append({"fit_id": "second", "label": "final"}, event=EVENT_PROMOTE)

    current = index.current_label("final")

    assert current is not None
    assert current["fit_id"] == "second"


def test_superseded_promotions_stay_in_the_index(tmp_path: Path) -> None:
    """What was once considered final is provenance in its own right."""
    index = FitIndex(tmp_path / "index.jsonl")
    index.append({"fit_id": "first", "label": "final"}, event=EVENT_PROMOTE)
    index.append({"fit_id": "second", "label": "final"}, event=EVENT_PROMOTE)

    assert [e["fit_id"] for e in index.promotions()] == ["first", "second"]


def test_concurrent_appends_do_not_interleave(tmp_path: Path) -> None:
    """Two fits finishing together must not corrupt a line."""
    from concurrent.futures import ThreadPoolExecutor

    index = FitIndex(tmp_path / "index.jsonl")

    def append(n: int) -> None:
        index.append({"fit_id": f"fit-{n}", "payload": "x" * 200})

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(append, range(200)))

    lines = (tmp_path / "index.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 200
    assert all(json.loads(line)["fit_id"].startswith("fit-") for line in lines)


# --------------------------------------------------------------------------
# Figure stamping
# --------------------------------------------------------------------------


SVG = '<?xml version="1.0"?>\n<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>\n'


def test_svg_stamp_round_trips(tmp_path: Path) -> None:
    target = tmp_path / "fig.svg"
    target.write_text(SVG, encoding="utf-8")

    assert stamp_file(target, "20260805-140311Z-abcdef12")
    assert read_stamp(target) == "20260805-140311Z-abcdef12"


def test_svg_stamp_keeps_the_document_valid(tmp_path: Path) -> None:
    """A stamped figure must still parse as XML -- it has to render."""
    import xml.etree.ElementTree as ET

    target = tmp_path / "fig.svg"
    target.write_text(SVG, encoding="utf-8")
    stamp_file(target, "abc-123")

    ET.fromstring(target.read_text(encoding="utf-8"))


def test_svg_restamping_replaces_rather_than_accumulates(tmp_path: Path) -> None:
    target = tmp_path / "fig.svg"
    target.write_text(SVG, encoding="utf-8")

    stamp_file(target, "first")
    stamp_file(target, "second")

    assert read_stamp(target) == "second"
    assert target.read_text(encoding="utf-8").count("<desc>") == 1


def test_png_stamp_round_trips_and_keeps_the_image_readable(tmp_path: Path) -> None:
    """The stamp is what survives a figure being pasted into a slide deck."""
    png = _minimal_png()
    target = tmp_path / "fig.png"
    target.write_bytes(png)

    assert stamp_file(target, "20260805-140311Z-abcdef12")
    assert read_stamp(target) == "20260805-140311Z-abcdef12"
    assert target.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert target.read_bytes().endswith(png[-12:])


def test_unstamped_file_reads_as_none(tmp_path: Path) -> None:
    target = tmp_path / "fig.svg"
    target.write_text(SVG, encoding="utf-8")

    assert read_stamp(target) is None


def test_unsupported_format_is_declined_not_corrupted(tmp_path: Path) -> None:
    target = tmp_path / "notes.txt"
    target.write_text("plain\n", encoding="utf-8")

    assert stamp_file(target, "abc") is False
    assert target.read_text(encoding="utf-8") == "plain\n"


def _minimal_png() -> bytes:
    """Build the smallest valid PNG: signature, IHDR, IDAT, IEND."""
    import struct
    import zlib

    def chunk(kind: bytes, body: bytes) -> bytes:
        return (
            struct.pack(">I", len(body))
            + kind
            + body
            + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0)
    idat = zlib.compress(b"\x00\x00")
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", idat)
        + chunk(b"IEND", b"")
    )
