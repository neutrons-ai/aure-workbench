"""Tests for the one-line fit descriptions used by `nrw ls` and the web UI."""

from __future__ import annotations

from typing import Any

from nr_workbench.provenance.summary import annotate, annotation_for, compare, describe


def entry(**overrides: Any) -> dict[str, Any]:
    """A plausible index entry, with the fields the summary reads."""
    base: dict[str, Any] = {
        "fit_id": "20260806-120000Z-aaaaaaaa",
        "sample": "S1",
        "model": "cu-thf",
        "chisq": 2.0,
        "n_free": 21,
        "method": "dream",
        "settings": {"method": "dream", "samples": 100000, "burn": 10000, "seed": 1},
        "run_key": "key-a",
        "inputs_digest": "in-a",
        "data_digest": "data-a",
        "script_sha256": "script-a",
        "started_at": "2026-08-06T12:00:00Z",
        "note": None,
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------------------
# describe
# --------------------------------------------------------------------------


def test_describe_prefers_the_users_own_note() -> None:
    """Nothing generated competes with what the scientist wrote."""
    assert describe(entry(note="first co-refinement")) == "first co-refinement"


def test_describe_falls_back_to_how_the_fit_was_run() -> None:
    assert describe(entry()) == "dream, 100k samples, 10k burn, 21 free"


def test_describe_truncates_a_long_note_to_one_line() -> None:
    """A table cell and a terminal column are both finite."""
    note = "why " * 40
    result = describe(entry(note=note + "\nsecond paragraph"))

    assert len(result) <= 72
    assert result.endswith("…")
    assert "second paragraph" not in result


def test_describe_never_returns_an_empty_string() -> None:
    """A blank line in the listing is worse than a redundant one."""
    assert describe({"fit_id": "x", "model": "m"}) == "m"


# --------------------------------------------------------------------------
# compare
# --------------------------------------------------------------------------


def test_compare_says_when_a_model_has_no_predecessor() -> None:
    assert compare(entry(), None) == "first run of this model"


def test_compare_names_the_setting_that_moved() -> None:
    """ "Settings changed" is useless; "samples 100k -> 200k" is the answer."""
    later = entry(settings={**entry()["settings"], "samples": 200000}, chisq=1.5)

    result = compare(later, entry())

    assert "samples 100k -> 200k" in result
    assert "chisq 2 -> 1.5" in result


def test_compare_reports_data_and_model_changes_together() -> None:
    """They are independent facts, and hearing only one of them misleads."""
    later = entry(
        inputs_digest="in-b",
        data_digest="data-b",
        script_sha256="script-b",
        run_key="key-b",
    )

    result = compare(later, entry())

    assert "data changed" in result
    assert "model changed" in result


def test_compare_does_not_call_a_script_edit_a_data_change() -> None:
    """`inputs_digest` counts the script among the inputs, so reading it as
    "the data" turns every model edit into a false claim about provenance --
    and "the data changed" is the one sentence that invalidates a comparison.
    """
    later = entry(inputs_digest="in-b", script_sha256="script-b", run_key="key-b")

    result = compare(later, entry())

    assert "data changed" not in result
    assert result.startswith("model changed")


def test_compare_says_only_that_inputs_moved_when_it_cannot_tell() -> None:
    """Fits recorded before `data_digest` existed know only that some input
    changed. Say the weaker true thing rather than the stronger false one."""
    old_a = {k: v for k, v in entry().items() if k != "data_digest"}
    old_b = {**old_a, "inputs_digest": "in-b"}

    assert "inputs changed" in compare(old_a, old_b)


def test_compare_calls_an_identical_rerun_a_replicate() -> None:
    assert compare(entry(fit_id="later"), entry()) == "replicate; chisq unchanged"


def test_compare_detects_a_difference_it_cannot_name() -> None:
    """Older entries have no `settings`, but the run key still covers them.

    Saying "replicate" about two runs that differ in something unrecorded
    would be a false statement about provenance, which is the one thing this
    tool must not do.
    """
    old = {k: v for k, v in entry().items() if k != "settings"}
    older = {**old, "run_key": "key-b"}

    assert compare(old, older).startswith("settings or environment changed")


def test_compare_ignores_a_field_one_side_never_recorded() -> None:
    """A missing field is absence of evidence, not evidence of change --
    otherwise adding a field to the index makes the whole history look
    churned."""
    without = {k: v for k, v in entry().items() if k != "inputs_digest"}

    assert "data changed" not in compare(entry(), without)


def test_compare_caps_a_long_list_of_settings_changes() -> None:
    base = entry(
        settings={"method": "dream", "samples": 1, "burn": 2, "seed": 3, "pop": 4}
    )
    later = entry(
        settings={"method": "dream", "samples": 9, "burn": 8, "seed": 7, "pop": 6}
    )

    result = compare(later, base)

    assert "and 1 more setting" in result
    assert "settings" not in result, "one change left over, not two"


def test_compare_omits_knobs_a_method_change_made_meaningless() -> None:
    """amoeba counts `steps`, dream counts `samples`. Switching between them
    is one decision, and rendering the knobs that stopped applying as
    `steps 2000 -> None` buries it under bookkeeping."""
    amoeba = entry(settings={"method": "amoeba", "steps": 2000, "seed": 1})
    dream = entry(settings={"method": "dream", "samples": 100000, "seed": 1})

    result = compare(dream, amoeba)

    assert result.startswith("method amoeba -> dream")
    assert "None" not in result
    assert "steps" not in result


def test_compare_reports_a_knob_added_under_an_unchanged_method() -> None:
    """Here the appearance of a setting is a real decision, not a side effect
    of a different fitter's vocabulary."""
    before = entry(settings={"method": "dream", "samples": 100000})
    after = entry(settings={"method": "dream", "samples": 100000, "pop": 20})

    assert "pop 20 added" in compare(after, before)
    assert "pop dropped" in compare(before, after)


def test_compare_puts_the_method_first_among_settings() -> None:
    """Of everything that can move, the method changes the answer most."""
    later = entry(settings={**entry()["settings"], "method": "amoeba", "seed": 2})

    result = compare(later, entry())

    assert result.index("method") < result.index("seed")


def test_compare_falls_back_to_the_method_when_settings_are_unrecorded() -> None:
    old_a = {k: v for k, v in entry(method="amoeba").items() if k != "settings"}
    old_b = {k: v for k, v in entry().items() if k != "settings"}

    assert "method dream -> amoeba" in compare(old_a, old_b)


# --------------------------------------------------------------------------
# annotate
# --------------------------------------------------------------------------


def test_annotate_compares_against_the_previous_run_of_the_same_model() -> None:
    """Comparing against the row above would usually cross models, which
    describes the listing rather than the work."""
    rows = [
        entry(fit_id="c", model="cu-thf", settings={"method": "amoeba"}),
        entry(fit_id="b", model="something-else"),
        entry(fit_id="a", model="cu-thf", settings={"method": "dream"}),
    ]

    annotated = annotate(rows)

    assert annotated[0]["compared_to"] == "a"
    assert "method dream -> amoeba" in annotated[0]["change"]
    assert annotated[1]["change"] == "first run of this model"
    assert annotated[2]["change"] == "first run of this model"


def test_annotate_keeps_the_newest_first_ordering() -> None:
    rows = [entry(fit_id="c"), entry(fit_id="b"), entry(fit_id="a")]

    assert [r["fit_id"] for r in annotate(rows)] == ["c", "b", "a"]


def test_annotate_separates_the_same_model_name_in_two_samples() -> None:
    """Two samples routinely carry a model of the same name, and pairing them
    would invent a change that never happened."""
    rows = [
        entry(fit_id="b", sample="S2", inputs_digest="in-b"),
        entry(fit_id="a", sample="S1"),
    ]

    assert annotate(rows)[0]["change"] == "first run of this model"


def test_annotate_leaves_the_original_entries_alone() -> None:
    """The index rows are shared with other callers."""
    rows = [entry(fit_id="a")]

    annotate(rows)

    assert "change" not in rows[0]


def test_annotation_for_finds_one_fit() -> None:
    rows = [entry(fit_id="b", chisq=1.0), entry(fit_id="a", chisq=2.0)]

    assert annotation_for(rows, "b")["change"] == "replicate; chisq 2 -> 1"
    assert annotation_for(rows, "missing") is None
