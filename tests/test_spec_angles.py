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

import json
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
    write_autoreduced(tmp_path / STEADY, 100001, [1], planned=2)
    headerless(tmp_path / STEADY, "REFL_100001_2_100002_partial.txt")

    assert angles(spec([auto(thetas=[None, 1.3])]), tmp_path) == [0.45, 1.3]
    with pytest.raises(SpecError, match="REFL_100001_2_100002_partial.txt"):
        discover_measurements(spec([auto(thetas=[0.45, None])]), tmp_path)


def test_thetas_must_give_one_angle_per_segment(tmp_path: Path) -> None:
    write_autoreduced(tmp_path / STEADY, 100001, [1, 2, 3])

    with pytest.raises(SpecError, match=r"gives 2 angle\(s\), but run 100001 has 3"):
        discover_measurements(spec([auto(thetas=[0.45, 1.251])]), tmp_path)


def test_more_angles_than_segments_is_refused_too(tmp_path: Path) -> None:
    write_autoreduced(tmp_path / STEADY, 100001, [1, 2, 3])

    with pytest.raises(SpecError, match=r"gives 4 angle\(s\), but run 100001 has 3"):
        discover_measurements(spec([auto(thetas=[0.45, 1.251, 3.5, 0.6])]), tmp_path)


def test_a_run_missing_its_last_planned_segment_is_refused(tmp_path: Path) -> None:
    """1 and 2 of a run planned with 3 are contiguous, and still not all of it."""
    write_autoreduced(tmp_path / STEADY, 100001, [1, 2], planned=3)

    with pytest.raises(SpecError, match="headers plan 3 segments, but only 2"):
        discover_measurements(spec([auto()]), tmp_path)


def test_a_stated_angle_further_off_than_rounding_is_refused(tmp_path: Path) -> None:
    """0.435 for a file that records 0.45: a guess or a copy, not a rounding."""
    write_autoreduced(tmp_path / STEADY, 100001, [1, 2, 3])

    with pytest.raises(SpecError, match="segment 1 is given as 0.435 deg"):
        discover_measurements(spec([auto(thetas=[0.435, 1.251, 3.5])]), tmp_path)


def test_a_header_that_cannot_be_read_is_named(tmp_path: Path) -> None:
    folder = tmp_path / STEADY
    folder.mkdir(parents=True)
    (folder / "REFL_100001_1_100001_partial.txt").write_text(
        '# Meta:{"theta": 0.0078\n' + reduced_rows(), encoding="utf-8"
    )

    with pytest.raises(SpecError, match="header cannot be read"):
        discover_measurements(spec([auto()]), tmp_path)


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
    with pytest.raises(SpecError, match=f"{two.name} records 1.2510 deg"):
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


def test_thetas_beside_listed_segments_is_refused() -> None:
    """It would be ignored: each listed segment carries its own `theta`."""
    with pytest.raises(ValueError, match="`thetas` is for `segments: auto`"):
        spec([{"name": "s1", "segments": [{"file": "a.txt"}], "thetas": [0.45]}])


@pytest.mark.parametrize("angle", [0, -0.45, 90, float("nan")])
def test_an_angle_outside_0_to_90_degrees_is_refused(angle: float) -> None:
    with pytest.raises(ValueError, match="between 0 and 90"):
        spec([auto(thetas=[angle])])


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
    """The slices carry no header; the same run, summed, in data/steady does.

    Another run's file sits beside it, at 1.251: the angle is its own run's.
    """
    slices(tmp_path)
    write_autoreduced(tmp_path / STEADY, 100003, [1], planned=1)
    write_autoreduced(tmp_path / STEADY, 100001, [2], planned=3)

    assert angles(spec(series=[tnr()]), tmp_path, "tnr") == [0.45, 0.45]


def test_a_flat_tnr_folder_finds_the_summed_dataset_beside_it(tmp_path: Path) -> None:
    """Slices straight in data/tnr, as `nrw sample new` suggests copying them."""
    flat = "samples/S1/data/tnr"
    for seconds in (0, 240):
        headerless(tmp_path / flat, f"r100003_t{seconds:06d}.txt")
    write_autoreduced(tmp_path / STEADY, 100003, [1], planned=1)

    assert angles(spec(series=[tnr(reduced_dir=flat)]), tmp_path, "tnr") == [
        0.45,
        0.45,
    ]


def sidecar(root: Path, run: int, *, named: int | None = None) -> None:
    """A reduction JSON for *run*, and its two labelled slices."""
    folder = root / TNR
    labels = ("eis_1", "eis_2")
    intervals = [
        {"label": label, "interval_type": "hold", "start": f"2026-09-28T10:0{i}:00"}
        for i, label in enumerate(labels)
    ]
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"r{named or run}_eis_reduction.json").write_text(
        json.dumps({"run_number": run, "intervals": intervals}), encoding="utf-8"
    )
    for label in labels:
        headerless(folder, f"r{run}_{label}.txt")


def test_a_series_read_from_its_sidecar_takes_its_runs_angle(tmp_path: Path) -> None:
    sidecar(tmp_path, 100003)
    write_autoreduced(tmp_path / STEADY, 100003, [1], planned=1)

    series = [tnr(time_from="reduction_json")]

    assert angles(spec(series=series), tmp_path, "tnr") == [0.45, 0.45]


def test_a_sidecar_of_another_run_is_refused(tmp_path: Path) -> None:
    """It would fit that run's slices, at that run's angle, under this name."""
    sidecar(tmp_path, 100005)
    series = [tnr(time_from="reduction_json")]

    with pytest.raises(SpecError, match="no \\*_reduction.json for run 100003"):
        discover_measurements(spec(series=series), tmp_path)

    sidecar(tmp_path, 100005, named=100003)
    with pytest.raises(SpecError, match="reduction of run 100005, not 100003"):
        discover_measurements(spec(series=series), tmp_path)


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
