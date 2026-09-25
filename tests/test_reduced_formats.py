"""One place knows what a reduced file is called, and AuRE decides what it is.

These conventions used to be spelled out in five modules that did not know
about each other. When `_autoreduction.dat` arrived, `project/scan` was taught
about it first, which made the files visible and let them flow into a
`nrw model new` that wrote `dq_is_fwhm: true` for a file whose header says
`sigma`. Making one layer smarter made the failure less visible, not more.
"""

from pathlib import Path

import pytest

from nr_workbench.instrument import reduced

PARTIAL = "REFL_234277_2_234278_partial.txt"
AUTORED = "REFL_234277_2_234278_autoreduction.dat"
COMBINED = "REFL_234277_combined_data_auto.txt"


# ---------------------------------------------------------------------------
# What a file is called
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,dialect",
    [(PARTIAL, reduced.PARTIAL_DIALECT), (AUTORED, reduced.AUTOREDUCTION_DIALECT)],
)
def test_both_dialects_are_parsed(name, dialect):
    parsed = reduced.parse_segment_name(name)

    assert parsed is not None
    assert (parsed.run, parsed.segment, parsed.subrun) == (234277, 2, 234278)
    assert parsed.dialect == dialect


def test_the_segment_comes_from_the_name():
    """The only place it is recorded for the `new_reduction` dialect.

    That header describes the whole run and is byte-identical in every one of
    its files, so it cannot say which segment it is attached to.
    """
    assert reduced.parse_segment_name(AUTORED).segment == 2


def test_a_combined_curve_is_not_a_segment():
    assert reduced.parse_segment_name(COMBINED) is None
    assert reduced.parse_combined_name(COMBINED) == 234277


def test_a_full_path_parses_the_same_as_a_bare_name():
    assert reduced.parse_segment_name(f"/data/steady/{AUTORED}") is not None


@pytest.mark.parametrize("name", ["notes.txt", "REFL_234277.txt", "r1_t000010.txt"])
def test_an_unrelated_name_is_not_a_segment(name):
    assert reduced.parse_segment_name(name) is None


def test_globs_name_both_dialects():
    """An error that names only one sends someone looking for a file their
    reduction never writes."""
    patterns = reduced.segment_globs(234277, 3)

    assert any(p.endswith("_partial.txt") for p in patterns)
    assert any(p.endswith("_autoreduction.dat") for p in patterns)
    assert all("234277_3_" in p for p in patterns)


def test_dat_is_a_steady_suffix():
    """The extension filter dropped .dat before any pattern ran, so a
    directory of three valid files looked empty and the scan said "no data
    found" without naming the files or the extension as the reason."""
    assert {".txt", ".dat"} <= reduced.STEADY_SUFFIXES


def test_find_segments_returns_both_dialects(tmp_path):
    (tmp_path / PARTIAL).write_text("")
    (tmp_path / "REFL_234277_3_234279_autoreduction.dat").write_text("")
    (tmp_path / "unrelated.txt").write_text("")

    found = [p.name for p in reduced.find_segments(tmp_path, 234277)]

    assert len(found) == 2
    assert "unrelated.txt" not in found


def test_find_segments_can_pick_one_segment(tmp_path):
    for seg, sub in ((1, 234277), (2, 234278)):
        (tmp_path / f"REFL_234277_{seg}_{sub}_autoreduction.dat").write_text("")

    found = reduced.find_segments(tmp_path, 234277, 2)

    assert [p.name for p in found] == ["REFL_234277_2_234278_autoreduction.dat"]


# ---------------------------------------------------------------------------
# What a file *is* — AuRE's answer, because AuRE builds the fit
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", [PARTIAL, AUTORED])
def test_both_dialects_are_angle_segments(name):
    assert reduced.role(name) == "partial"


def test_a_combined_curve_is_one_curve():
    assert reduced.role(COMBINED) == "combined"


@pytest.mark.parametrize("name", [PARTIAL, AUTORED, COMBINED])
def test_nrw_and_aure_agree_about_every_file_we_produce(name):
    """The check that would have caught this beamtime.

    Before AuRE learned the second dialect, nrw called these files angle
    segments and AuRE read each as a complete curve with dQ as FWHM and no
    incident angle. Both tools were internally consistent and one of them was
    wrong about what the other would do — which is invisible from either side.
    """
    assert reduced.disagreement(name) is None


def test_a_disagreement_names_both_readings_and_what_to_do(monkeypatch):
    """Simulated by making AuRE deny it knows the file."""

    class _Blind:
        name = "generic"

        def file_role(self, path):
            return "unknown"

        def resolve_by_name(self, path):
            return self

    monkeypatch.setattr(
        reduced, "_aure_roles", lambda: (_Blind(), "partial", "combined")
    )

    message = reduced.disagreement(AUTORED)

    assert message is not None
    assert "'partial'" in message and "'unknown'" in message
    assert "aure formats" in message  # says how to check


def test_an_absent_aure_is_not_a_disagreement(monkeypatch):
    """Reporting one would send the reader after a phantom."""
    monkeypatch.setattr(reduced, "_aure_roles", lambda: None)

    assert reduced.disagreement(AUTORED) is None


def test_the_local_patterns_still_answer_without_aure(monkeypatch):
    """A broken or absent install must degrade, not crash."""
    monkeypatch.setattr(reduced, "_aure_roles", lambda: None)

    assert reduced.role(AUTORED) == "partial"
    assert reduced.role(COMBINED) == "combined"
    assert reduced.role("notes.txt") == "unknown"


# ---------------------------------------------------------------------------
# No module keeps its own copy any more
# ---------------------------------------------------------------------------


def test_no_other_module_spells_out_these_filenames():
    """The duplication is the bug, so its absence is what gets pinned.

    Five modules used to carry these patterns and none knew the others
    existed. Comments and docstrings may still *mention* the names — that is
    how the two dialects get explained — so this looks for the patterns in
    code: a glob or a regex built from them.
    """
    src = Path(reduced.__file__).resolve().parents[1]
    offenders = []
    for path in sorted(src.rglob("*.py")):
        if path.name == "reduced.py":
            continue
        for number, line in enumerate(path.read_text().splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith("*"):
                continue
            names_a_suffix = "_partial.txt" in line or "_autoreduction.dat" in line
            builds_a_pattern = (
                "glob(" in line or "re.compile" in line or 'f"REFL_' in line
            )
            if names_a_suffix and builds_a_pattern:
                offenders.append(f"{path.name}:{number}: {stripped}")

    assert not offenders, "filename patterns outside reduced.py:\n" + "\n".join(
        offenders
    )
