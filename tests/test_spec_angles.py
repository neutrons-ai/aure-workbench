"""A spec's angles come from the data files; none is assumed.

theta sets each probe's wavelength axis (``wl = 4*pi*sin(theta)/q``), and a
fit absorbs a wrong one into roughness rather than reporting it. So resolving
a spec reads every file's angle from its own header, checks an angle the spec
states against that record, and refuses a file that records none unless the
spec gives it. The spec once defaulted to the group's usual 0.45, 1.2, 3.5 and
0.6 -- and ``thetas`` also decided how many segments were read.

The fixture's segments sit at 0.45, 1.251 and 3.5 degrees: 1.251 is not the
usual 1.2, so a test that sees it has read the file.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pytest

from nr_workbench.spec.models import ModelSpec, SpecError
from nr_workbench.spec.resolve import discover_measurements

from .experiment_fixtures import reduced_rows, write_autoreduced

STEADY = "samples/S1/data/steady"
TNR = "samples/S1/data/tnr/100003"


def spec(
    states: list[dict[str, Any]] | None = None,
    series: list[dict[str, Any]] | None = None,
) -> ModelSpec:
    payload: dict[str, Any] = {
        "schema": "nrw-model/1",
        "name": "m",
        "sample": "S1",
        "materials": {"Film": {"rho": 4.0}, "Si": {"rho": 2.07}},
        "stack": [
            {"name": "Film", "thickness": 100, "roughness": 5},
            {"name": "Si"},
        ],
        "states": states or [],
    }
    if series:
        payload["series"] = series
    return ModelSpec.model_validate(payload)


def auto(run: int = 100001, **extra: Any) -> dict[str, Any]:
    return {"name": "s1", "run": run, "segments": "auto", "data_dir": STEADY, **extra}


def angles(model: ModelSpec, root: Path, group: str = "s1") -> list[float]:
    return [m.theta for m in discover_measurements(model, root)[group]]


def headerless(folder: Path, name: str) -> Path:
    """A reduced file with no header at all: nothing records its angle."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(reduced_rows(), encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# segments: auto
# --------------------------------------------------------------------------


def test_auto_segments_take_each_angle_from_their_own_file(tmp_path: Path) -> None:
    write_autoreduced(tmp_path / STEADY, 100001, [1, 2, 3])

    assert angles(spec([auto()]), tmp_path) == [0.45, 1.251, 3.5]


def test_auto_segments_are_every_one_on_disk_however_many(tmp_path: Path) -> None:
    """The number of segments was once how many angles the spec listed."""
    write_autoreduced(tmp_path / STEADY, 100001, [1, 2, 3, 4], planned=4)

    assert angles(spec([auto()]), tmp_path) == [0.45, 1.251, 3.5, 0.6]


def test_a_stated_angle_that_disagrees_with_its_file_is_refused(tmp_path: Path) -> None:
    write_autoreduced(tmp_path / STEADY, 100001, [1, 2, 3])
    model = spec([auto(thetas=[0.45, 1.2, 3.5])])

    with pytest.raises(SpecError) as refused:
        discover_measurements(model, tmp_path)

    message = str(refused.value)
    assert "segment 2 is given as 1.2 deg" in message
    assert "REFL_100001_2_100002_autoreduction.dat records 1.2510 deg" in message


def test_a_stated_angle_that_agrees_is_replaced_by_the_files_own(
    tmp_path: Path,
) -> None:
    """Within the tolerance it is the same setting; the file has the precision."""
    write_autoreduced(tmp_path / STEADY, 100001, [1, 2, 3])

    assert angles(spec([auto(thetas=[0.45, 1.25, 3.5])]), tmp_path) == [
        0.45,
        1.251,
        3.5,
    ]


def test_a_file_that_records_no_angle_is_refused_until_the_spec_gives_it(
    tmp_path: Path,
) -> None:
    headerless(tmp_path / STEADY, "REFL_100001_1_100001_partial.txt")

    with pytest.raises(SpecError) as refused:
        discover_measurements(spec([auto()]), tmp_path)

    message = str(refused.value)
    assert "REFL_100001_1_100001_partial.txt" in message
    assert "records no incident angle" in message
    assert "`thetas`" in message
    assert angles(spec([auto(thetas=[0.52])]), tmp_path) == [0.52]


def test_null_reads_that_segments_angle_from_its_file(tmp_path: Path) -> None:
    write_autoreduced(tmp_path / STEADY, 100001, [1])
    headerless(tmp_path / STEADY, "REFL_100001_2_100002_partial.txt")

    assert angles(spec([auto(thetas=[None, 1.3])]), tmp_path) == [0.45, 1.3]
    with pytest.raises(SpecError, match="REFL_100001_2_100002_partial.txt"):
        discover_measurements(spec([auto(thetas=[0.45, None])]), tmp_path)


def test_thetas_must_give_one_angle_per_segment(tmp_path: Path) -> None:
    write_autoreduced(tmp_path / STEADY, 100001, [1, 2, 3])

    with pytest.raises(SpecError, match=r"gives 2 angle\(s\), but run 100001 has 3"):
        discover_measurements(spec([auto(thetas=[0.45, 1.251])]), tmp_path)


def test_a_gap_in_the_segments_is_refused(tmp_path: Path) -> None:
    """Segment 2 missing is usually a segment not reduced yet."""
    write_autoreduced(tmp_path / STEADY, 100001, [1, 3])

    with pytest.raises(SpecError, match="not contiguous from 1"):
        discover_measurements(spec([auto()]), tmp_path)


# --------------------------------------------------------------------------
# Explicit segments, and a combined curve
# --------------------------------------------------------------------------


def test_explicit_segments_read_their_angles_and_check_a_stated_one(
    tmp_path: Path,
) -> None:
    one, two, _ = write_autoreduced(tmp_path / STEADY, 100001, [1, 2, 3])
    files = [p.relative_to(tmp_path).as_posix() for p in (one, two)]

    read = spec([{"name": "s1", "segments": [{"file": files[0]}, {"file": files[1]}]}])
    stated = spec([{"name": "s1", "segments": [{"file": files[1], "theta": 1.2}]}])

    assert angles(read, tmp_path) == [0.45, 1.251]
    with pytest.raises(SpecError, match="segment 1 is given as 1.2 deg"):
        discover_measurements(stated, tmp_path)


def test_a_combined_curve_takes_its_angle_from_its_file(tmp_path: Path) -> None:
    folder = tmp_path / STEADY
    folder.mkdir(parents=True)
    (folder / "REFL_100001_combined_data_auto.txt").write_text(
        f'# Meta:{{"theta": {math.radians(0.52)!r}}}\n' + reduced_rows(),
        encoding="utf-8",
    )

    assert angles(spec([auto(kind="combined")]), tmp_path) == [pytest.approx(0.52)]
    with pytest.raises(SpecError, match="holds one angle, not 3"):
        discover_measurements(
            spec([auto(kind="combined", thetas=[0.45, 1.2, 3.5])]), tmp_path
        )


# --------------------------------------------------------------------------
# A time-resolved series
# --------------------------------------------------------------------------


def slices(root: Path) -> None:
    for seconds in (0, 240):
        headerless(root / TNR, f"r100003_t{seconds:06d}.txt")


def tnr(**extra: Any) -> dict[str, Any]:
    return {
        "name": "tnr",
        "run": 100003,
        "reduced_dir": TNR,
        "time_from": "filename",
        **extra,
    }


def test_a_series_takes_its_angle_from_its_runs_summed_dataset(tmp_path: Path) -> None:
    """The slices carry no header; the same run, summed, in data/steady does."""
    slices(tmp_path)
    write_autoreduced(tmp_path / STEADY, 100003, [1], planned=1)

    assert angles(spec(series=[tnr()]), tmp_path, "tnr") == [0.45, 0.45]


def test_a_series_with_no_summed_dataset_is_refused_until_the_spec_gives_it(
    tmp_path: Path,
) -> None:
    slices(tmp_path)

    with pytest.raises(SpecError) as refused:
        discover_measurements(spec(series=[tnr()]), tmp_path)

    message = str(refused.value)
    assert "no file of run 100003 in samples/S1/data/steady records one" in message
    assert "`theta`" in message
    assert angles(spec(series=[tnr(theta=0.6)]), tmp_path, "tnr") == [0.6, 0.6]


def test_a_series_angle_that_disagrees_with_the_summed_dataset_is_refused(
    tmp_path: Path,
) -> None:
    slices(tmp_path)
    write_autoreduced(tmp_path / STEADY, 100003, [1], planned=1)

    with pytest.raises(SpecError, match="given as 0.6 deg"):
        discover_measurements(spec(series=[tnr(theta=0.6)]), tmp_path)
