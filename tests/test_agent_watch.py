"""Does the watcher wait long enough, and hold back what it should?

Two failure modes, and they need opposite tests. Starting too early fits one
angle segment of three and gets a plausible answer from a third of the data, so
the settling tests grow a measurement file by file and assert nothing is ready
until it stops changing. Starting on the wrong data is worse, so the quarantine
tests build each mis-filing fingerprint on purpose.

The fixtures write real filenames, because the subrun rule reads them --
`REFL_218386_2_218387_partial.txt` is the second angle segment of run 218386,
and a program that never parses that string cannot notice when it is wrong.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from nr_workbench.agent import watch
from nr_workbench.project.scan import scan_sample

HEADER = """\
# Datafile created by RefectivityReduction
# Meta:
#    sample_title=Cu film in THF
#    norm_run=218380
#    theta={theta}
# Q (1/A)  R  dR  dQ (1/A)
0.0100 1.0000e+00 1.0e-02 1.0e-04
0.0200 5.0000e-01 5.0e-03 2.0e-04
"""


def make_sample(root: Path, sample: str = "S1") -> Path:
    """A project skeleton with one sample and nothing in it."""
    (root / ".nrw").mkdir(parents=True, exist_ok=True)
    (root / "nrw.toml").write_text(
        '[project]\nname = "t"\n[instrument]\nfacility = "SNS"\ninstrument = "REF_L"\n',
        encoding="utf-8",
    )
    directory = root / "samples" / sample
    (directory / "data" / "steady").mkdir(parents=True, exist_ok=True)
    (directory / "data" / "tnr").mkdir(parents=True, exist_ok=True)
    (directory / "reports").mkdir(parents=True, exist_ok=True)
    (directory / "sample.md").write_text("# S1\n", encoding="utf-8")
    return directory


def write_segment(
    directory: Path, run: int, segment: int, subrun: int, *, at: float | None = None
) -> Path:
    """One angle segment, named the way reduction names it.

    Args:
        at: Modification time to stamp on it. Settling is measured from the
            files themselves, so a test that wants an old measurement has to
            write an old file rather than advance a counter.
    """
    path = directory / "data" / "steady" / f"REFL_{run}_{segment}_{subrun}_partial.txt"
    path.write_text(HEADER.format(theta=0.3 * segment), encoding="utf-8")
    if at is not None:
        os.utime(path, (at, at))
    return path


# --------------------------------------------------------------------------
# Settling: a measurement arrives over minutes
# --------------------------------------------------------------------------


def test_a_measurement_is_not_ready_while_its_files_are_still_arriving(
    tmp_path: Path,
) -> None:
    """The whole reason for the watcher. Segment 1 alone looks like a complete
    measurement to anything that only counts files."""
    directory = make_sample(tmp_path)
    state = watch.WatchState()

    write_segment(directory, 218386, 1, 218386, at=1000.0)
    first = watch.assess(tmp_path, "S1", state, settle_seconds=60, now=1000.0)
    assert [v.state for v in first] == ["arriving"]

    # Two minutes later the second segment lands. The measurement had gone
    # quiet by then, and the new file must restart the clock -- otherwise the
    # watcher fits two segments of three.
    quiet = watch.assess(tmp_path, "S1", state, settle_seconds=60, now=1100.0)
    assert [v.state for v in quiet] == ["ready"]

    write_segment(directory, 218386, 2, 218387, at=1120.0)
    # The poll that first sees the new file is deliberately the most
    # conservative one: the fingerprint moved, so the clock restarts whatever
    # the timestamps say.
    after = watch.assess(tmp_path, "S1", state, settle_seconds=60, now=1121.0)
    assert [v.state for v in after] == ["arriving"]

    still = watch.assess(tmp_path, "S1", state, settle_seconds=60, now=1150.0)
    assert [v.state for v in still] == ["settling"]

    later = watch.assess(tmp_path, "S1", state, settle_seconds=60, now=1200.0)
    assert [v.state for v in later] == ["ready"]


def test_a_settled_measurement_becomes_ready(tmp_path: Path) -> None:
    """Unchanged for long enough, coherent, and not yet fitted."""
    directory = make_sample(tmp_path)
    for segment in (1, 2, 3):
        write_segment(directory, 218386, segment, 218385 + segment, at=1000.0)
    state = watch.WatchState()

    verdicts = watch.assess(tmp_path, "S1", state, settle_seconds=300, now=1400.0)

    assert [(v.run, v.state, v.ready) for v in verdicts] == [(218386, "ready", True)]


def test_one_poll_is_enough_to_see_a_settled_measurement(tmp_path: Path) -> None:
    """`nrw agent watch --dry-run` polls once. Measuring quiet time from what
    previous polls saw would report every measurement as still arriving, which
    is the state a person checking the queue most wants to see through."""
    directory = make_sample(tmp_path)
    write_segment(directory, 218386, 1, 218386, at=1000.0)

    verdicts = watch.assess(
        tmp_path, "S1", watch.WatchState(), settle_seconds=300, now=1400.0
    )

    assert verdicts[0].ready


def test_a_measurement_already_fitted_is_not_started_again(tmp_path: Path) -> None:
    """Keyed on the index, so a fit the scientist ran by hand counts too. A
    daemon repeating finished work is how the output becomes unreadable."""
    directory = make_sample(tmp_path)
    for segment in (1, 2, 3):
        write_segment(directory, 218386, segment, 218385 + segment)
    fit_id = "20260810-120000Z-aaaaaaaa"
    (tmp_path / ".nrw" / "index.jsonl").write_text(
        json.dumps(
            {
                "event": "fit",
                "fit_id": fit_id,
                "sample": "S1",
                "model": "ocv1",
                "status": "ok",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    # The run number is only recoverable from the input paths: the index
    # carries digests, and a digest does not say which measurement it was.
    results = directory / "results" / fit_id
    results.mkdir(parents=True)
    # A directory with no manifest is an orphan, and `provenance.lookup` will
    # not resolve it -- which is right, and means the fixture has to write one.
    (results / "manifest.json").write_text("{}", encoding="utf-8")
    (results / "inputs.json").write_text(
        json.dumps(
            {
                "inputs": [
                    {"role": "script", "path": "samples/S1/models/ocv1.py"},
                    {
                        "role": "data:steady:REFL_218386_1_218386_partial",
                        "path": (
                            "samples/S1/data/steady/REFL_218386_1_218386_partial.txt"
                        ),
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    state = watch.WatchState()

    verdicts = watch.assess(tmp_path, "S1", state, settle_seconds=1, now=1e12)

    assert [v.state for v in verdicts] == ["done"]


def test_the_neighbouring_run_is_not_marked_fitted_by_its_neighbours_subrun(
    tmp_path: Path,
) -> None:
    """`REFL_218386_2_218387_partial.txt` holds two six-digit numbers: run
    218386 and its subrun 218387. Reading both marks run 218387 -- a separate,
    real measurement, since the beamline numbers consecutively -- as already
    analysed, and the daemon then skips it without saying so.

    This is the failure mode with no symptom: the morning status board says
    the run was handled.
    """
    directory = make_sample(tmp_path)
    for segment in (1, 2, 3):
        write_segment(directory, 218386, segment, 218385 + segment, at=1000.0)
    # A separate measurement, never fitted, whose run number collides with a
    # subrun of the one above.
    write_segment(directory, 218387, 1, 218387, at=1000.0)

    fit_id = "20260810-120000Z-aaaaaaaa"
    (tmp_path / ".nrw" / "index.jsonl").write_text(
        json.dumps({"event": "fit", "fit_id": fit_id, "sample": "S1", "status": "ok"})
        + "\n",
        encoding="utf-8",
    )
    results = directory / "results" / fit_id
    results.mkdir(parents=True)
    # A directory with no manifest is an orphan, and `provenance.lookup` will
    # not resolve it -- which is right, and means the fixture has to write one.
    (results / "manifest.json").write_text("{}", encoding="utf-8")
    (results / "inputs.json").write_text(
        json.dumps(
            {
                "inputs": [
                    {
                        "role": f"data:steady:REFL_218386_{seg}_{218385 + seg}",
                        "path": (
                            f"samples/S1/data/steady/"
                            f"REFL_218386_{seg}_{218385 + seg}_partial.txt"
                        ),
                    }
                    for seg in (1, 2, 3)
                ]
            }
        ),
        encoding="utf-8",
    )

    verdicts = watch.assess(
        tmp_path, "S1", watch.WatchState(), settle_seconds=1, now=1e12
    )
    states = {v.run: v.state for v in verdicts}

    assert states[218386] == "done"
    assert states[218387] == "ready", "a run nobody fitted must not read as done"


# --------------------------------------------------------------------------
# Quarantine: each fingerprint on its own
# --------------------------------------------------------------------------


def test_a_run_filed_as_both_steady_and_series_is_quarantined(tmp_path: Path) -> None:
    """The real one. In the reference experiment a whole-run reduction of a
    time-resolved measurement was written into data/steady/; a daemon reacting
    to file arrival would have fitted it that night."""
    directory = make_sample(tmp_path)
    write_segment(directory, 218389, 1, 218389)
    slices = directory / "data" / "tnr"
    for t in (0, 60, 120):
        (slices / f"r218389_t{t}.txt").write_text(
            HEADER.format(theta=0.6), encoding="utf-8"
        )

    reason = watch.quarantine_reason(scan_sample(tmp_path, "S1"), 218389)

    assert "both a steady-state measurement and a time-resolved series" in reason


def test_non_contiguous_segments_are_quarantined(tmp_path: Path) -> None:
    """Segments 1 and 3 with no 2: either one is missing or a file from
    another run landed here. Either way, not something to fit unattended."""
    directory = make_sample(tmp_path)
    write_segment(directory, 218386, 1, 218386)
    write_segment(directory, 218386, 3, 218388)

    reason = watch.quarantine_reason(scan_sample(tmp_path, "S1"), 218386)

    assert "not contiguous from 1" in reason


def test_a_subrun_that_does_not_follow_the_run_is_quarantined(tmp_path: Path) -> None:
    """Segment 2 of run 218386 is subrun 218387. Anything else means these
    files are not all the same measurement."""
    directory = make_sample(tmp_path)
    write_segment(directory, 218386, 1, 218386)
    write_segment(directory, 218386, 2, 218399)

    reason = watch.quarantine_reason(scan_sample(tmp_path, "S1"), 218386)

    assert "218399" in reason and "218387" in reason


def test_a_sound_measurement_is_not_quarantined(tmp_path: Path) -> None:
    """The check must not fire on the ordinary case, or it stops meaning
    anything."""
    directory = make_sample(tmp_path)
    for segment in (1, 2, 3):
        write_segment(directory, 218386, segment, 218385 + segment)

    assert watch.quarantine_reason(scan_sample(tmp_path, "S1"), 218386) == ""


def test_a_quarantined_run_never_becomes_ready(tmp_path: Path) -> None:
    """However long it sits there. Settling and quarantine are independent,
    and the wrong order would let time launder a bad measurement."""
    directory = make_sample(tmp_path)
    write_segment(directory, 218386, 1, 218386)
    write_segment(directory, 218386, 3, 218388)
    state = watch.WatchState()

    verdicts = watch.assess(tmp_path, "S1", state, settle_seconds=1, now=1e12)

    assert [(v.state, v.ready) for v in verdicts] == [("quarantined", False)]


# --------------------------------------------------------------------------
# The time-resolved series
# --------------------------------------------------------------------------


def test_a_series_waits_for_the_reduction_sidecar(tmp_path: Path) -> None:
    """Slices arrive one at a time over the electrochemistry, so counting them
    says nothing about whether the series is finished."""
    directory = make_sample(tmp_path)
    slices = directory / "data" / "tnr"
    for t in (0, 60):
        (slices / f"r218389_t{t}.txt").write_text(
            HEADER.format(theta=0.6), encoding="utf-8"
        )

    scan = scan_sample(tmp_path, "S1")

    assert "no reduction JSON yet" in watch.series_complete(tmp_path, scan.series[0])


def test_a_series_is_incomplete_until_every_named_interval_has_a_file(
    tmp_path: Path,
) -> None:
    """The sidecar is the only thing that knows how many slices to expect."""
    directory = make_sample(tmp_path)
    slices = directory / "data" / "tnr"
    for t in (0, 60):
        (slices / f"r218389_t{t}.txt").write_text(
            HEADER.format(theta=0.6), encoding="utf-8"
        )
    (slices / "r218389_eis_reduction.json").write_text(
        json.dumps({"intervals": [{"name": f"i{i}"} for i in range(5)]}),
        encoding="utf-8",
    )

    scan = scan_sample(tmp_path, "S1")
    reason = watch.series_complete(tmp_path, scan.series[0])

    assert "2 of 5 slices" in reason


def test_a_complete_series_is_ready(tmp_path: Path) -> None:
    directory = make_sample(tmp_path)
    slices = directory / "data" / "tnr"
    for t in (0, 60, 120):
        (slices / f"r218389_t{t}.txt").write_text(
            HEADER.format(theta=0.6), encoding="utf-8"
        )
    (slices / "r218389_eis_reduction.json").write_text(
        json.dumps({"intervals": [{"name": f"i{i}"} for i in range(3)]}),
        encoding="utf-8",
    )

    scan = scan_sample(tmp_path, "S1")

    assert watch.series_complete(tmp_path, scan.series[0]) == ""


def test_an_unreadable_sidecar_is_treated_as_still_being_written(
    tmp_path: Path,
) -> None:
    """A half-written JSON file is the normal state during reduction, not an
    error to report."""
    directory = make_sample(tmp_path)
    slices = directory / "data" / "tnr"
    for t_s in (0, 60):
        (slices / f"r218389_t{t_s}.txt").write_text(
            HEADER.format(theta=0.6), encoding="utf-8"
        )
    (slices / "r218389_eis_reduction.json").write_text('{"inter', encoding="utf-8")

    scan = scan_sample(tmp_path, "S1")

    assert "not readable yet" in watch.series_complete(tmp_path, scan.series[0])


# --------------------------------------------------------------------------
# The output budget
# --------------------------------------------------------------------------


def test_the_output_budget_is_measured(tmp_path: Path) -> None:
    directory = make_sample(tmp_path)
    (directory / "reports" / "summary.md").write_text("word " * 100, encoding="utf-8")

    assert watch.output_budget(tmp_path, "S1") == (1, 100)


def test_an_eight_hour_night_of_reports_is_reported_as_unreadable(
    tmp_path: Path,
) -> None:
    """The failure the whole design is most likely to hit: not a wrong answer
    but forty defensible ones, at which point the rational response is to read
    none of them. Asserted as an invariant of the loop rather than left as a
    setting.
    """
    directory = make_sample(tmp_path)
    for hour in range(8):
        (directory / "reports" / f"session-{hour}.md").write_text(
            "word " * 300, encoding="utf-8"
        )

    crowded = watch.over_budget(tmp_path, "S1")

    assert "over the" in crowded and "Consolidate" in crowded


def test_one_page_is_within_budget(tmp_path: Path) -> None:
    """The intended shape: one page per sample, rewritten each session."""
    directory = make_sample(tmp_path)
    (directory / "reports" / "summary.md").write_text("word " * 800, encoding="utf-8")

    assert watch.over_budget(tmp_path, "S1") == ""


# --------------------------------------------------------------------------
# The loop itself
# --------------------------------------------------------------------------


def test_the_loop_starts_nothing_when_nothing_has_settled(
    tmp_path: Path, monkeypatch
) -> None:
    """A dry night is the common case, and it must cost nothing."""
    directory = make_sample(tmp_path)
    write_segment(directory, 218386, 1, 218386)

    def never(*args: object, **kwargs: object) -> None:
        raise AssertionError("started a session for an unsettled measurement")

    monkeypatch.setattr("nr_workbench.agent.session.run", never)

    started = watch.watch(
        tmp_path,
        ["S1"],
        settle_seconds=300,
        poll_seconds=0,
        max_sessions=1,
        dry_run=True,
        on_event=lambda _: None,
    )

    assert started == 0
