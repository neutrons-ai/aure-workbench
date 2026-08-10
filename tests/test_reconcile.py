"""Comparing what the files record against what sample.md claims.

The cases here are the real ones. `jen-apr2025/cu-thf-expt11` lost most of a
week to two errors that were sitting in the file headers before the first fit
ran, and both are reproduced below from that project's own record.

The `sample.md` in that project has since been corrected, so these use the
historical text. That is the point of the check: it would have fired then.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from nr_workbench.reconcile import documented_runs, read_table, reconcile


@dataclass
class Header:
    """Enough of a ReducedHeader for the reconciler."""

    path: Path = Path("REFL_1_1_1_partial.txt")
    theta: float | None = 0.45
    run: int | None = None
    norm_run: int | None = None
    sequence_number: int | None = 1
    sequence_id: int | None = None
    run_title: str | None = None


#: The measurement table as it stood when the errors were live: 218393 listed
#: as OCV though its own title says CA-realigned, and 218397 absent entirely.
HISTORICAL = """# expt11

## Measurements

| Run    | Type   | Condition   |
|--------|--------|-------------|
| 218386 | full Q | OCV         |
| 218389 | tNR    | -0.5 mA/cm2 |
| 218393 | full Q | OCV         |
"""


# --------------------------------------------------------------------------
# Reading the table
# --------------------------------------------------------------------------


def test_the_measurement_table_is_read_by_its_own_headers() -> None:
    rows = read_table(HISTORICAL)

    assert rows[0]["run"] == "218386"
    assert rows[0]["condition"] == "OCV"
    assert len(rows) == 3, "the underline is not a row"


def test_runs_are_keyed_by_number() -> None:
    assert sorted(documented_runs(HISTORICAL)) == [218386, 218389, 218393]


def test_prose_with_no_table_documents_nothing() -> None:
    assert documented_runs("Deposited 50 nm copper on titanium.") == {}


# --------------------------------------------------------------------------
# The two errors that cost the week
# --------------------------------------------------------------------------


def test_a_run_labelled_ocv_whose_title_says_otherwise_is_flagged() -> None:
    """The finding says this one "sent five fits down the wrong path". The
    table said OCV; the run's own title says CA-realigned --- chronoamperometry,
    i.e. under applied potential."""
    headers = [
        Header(
            sequence_id=218393,
            run_title="CuPt_d8-THF-fullQ_CA-realigned-218393-1.",
            norm_run=218274,
        )
    ]

    found = reconcile("expt11", headers, HISTORICAL)

    flagged = [f for f in found.findings if f.kind == "state-contradicts-notes"]
    assert [f.run for f in flagged] == [218393]
    assert "under applied potential" in flagged[0].message
    assert "CA-realigned" in flagged[0].from_file


def test_a_run_on_disk_that_the_notes_never_mention_is_flagged() -> None:
    """218397 was measured and never written up."""
    headers = [Header(sequence_id=218397, run_title="CuPt_d8-THF_OCV-after")]

    found = reconcile("expt11", headers, HISTORICAL)

    flagged = [f for f in found.findings if f.kind == "undocumented-run"]
    assert [f.run for f in flagged] == [218397]


def test_one_angle_normalised_against_two_direct_beams_is_flagged() -> None:
    """ "a different direct beam, not physics" --- the scale difference that
    otherwise reads as structure."""
    headers = [
        Header(sequence_id=218386, sequence_number=1, norm_run=218274),
        Header(sequence_id=218393, sequence_number=1, norm_run=218277),
    ]

    found = reconcile("expt11", headers, HISTORICAL)

    flagged = [f for f in found.findings if f.kind == "direct-beam-differs"]
    assert len(flagged) == 1
    assert "218274" in flagged[0].from_file and "218277" in flagged[0].from_file


def test_a_time_resolved_run_filed_as_a_steady_state_is_a_blocker() -> None:
    """ "Its living under data/steady/ is a filing accident." Fitting it as a
    state pins the model to a smearing artefact."""
    headers = [Header(sequence_id=218389, sequence_number=4, theta=0.5997)]

    found = reconcile("expt11", headers, HISTORICAL, series_runs={218389})

    flagged = [f for f in found.findings if f.kind == "series-run-in-steady"]
    assert [f.severity for f in flagged] == ["blocker"]
    assert found.worst == "blocker"


# --------------------------------------------------------------------------
# Precision: the reason anyone will read the output
# --------------------------------------------------------------------------


def test_segments_of_one_run_using_different_beams_is_not_a_finding() -> None:
    """Every REF_L run does this --- one direct beam per angle. Flagging it
    would fire on every measurement ever made, and a check that cries wolf is
    a check nobody reads."""
    headers = [
        Header(sequence_id=218386, sequence_number=1, norm_run=218274),
        Header(sequence_id=218386, sequence_number=2, norm_run=218275),
        Header(sequence_id=218386, sequence_number=3, norm_run=218338),
    ]

    found = reconcile("expt11", headers, HISTORICAL)

    assert [f.kind for f in found.findings if "direct-beam" in f.kind] == []


def test_a_title_that_merely_words_things_differently_is_not_a_finding() -> None:
    """A run title is a filename-shaped label and a table cell is prose. Only
    a contradiction in the electrochemical state counts."""
    headers = [Header(sequence_id=218386, run_title="CuPt_d8-THF_FullQ-218386-1.")]

    found = reconcile("expt11", headers, HISTORICAL)

    assert [f for f in found.findings if f.kind == "state-contradicts-notes"] == []


def test_a_title_that_states_no_condition_is_not_a_contradiction() -> None:
    """Absence of evidence. Most run titles say nothing about potential."""
    headers = [Header(sequence_id=218386, run_title="scan_3")]

    found = reconcile("expt11", headers, HISTORICAL)

    assert [f for f in found.findings if f.kind == "state-contradicts-notes"] == []


def test_agreeing_records_produce_nothing() -> None:
    one_run = "| Run | Condition |\n|---|---|\n| 218386 | OCV |\n"
    headers = [Header(sequence_id=218386, run_title="CuPt_OCV-before", norm_run=218274)]

    found = reconcile("expt11", headers, one_run)

    assert found.findings == []
    assert found.worst == "ok"


def test_a_documented_run_with_no_data_yet_is_only_information() -> None:
    """During a beamtime the notes routinely run ahead of the reduction."""
    found = reconcile("expt11", [], HISTORICAL)

    kinds = {f.kind for f in found.findings}
    assert kinds == {"documented-run-absent"}
    assert found.worst == "ok", "waiting for data is not a problem"


def test_an_unusual_angle_is_mentioned_but_not_alarming() -> None:
    headers = [Header(sequence_id=218386, theta=0.5997, run_title="CuPt_OCV")]

    found = reconcile("expt11", headers, HISTORICAL, standard_thetas=[0.45, 1.2, 3.5])

    flagged = [f for f in found.findings if f.kind == "unusual-angle"]
    assert [f.severity for f in flagged] == ["info"]


def test_a_standard_angle_within_tolerance_is_not_flagged() -> None:
    """Reduction records theta to four decimals; 3.5003 is 3.5."""
    headers = [Header(sequence_id=218386, theta=3.5003, run_title="CuPt_OCV")]

    found = reconcile("expt11", headers, HISTORICAL, standard_thetas=[0.45, 1.2, 3.5])

    assert [f for f in found.findings if f.kind == "unusual-angle"] == []
