"""`nrw report`: the closing narrative, with the fit sequence generated for you.

A sample ends as a dozen immutable result directories and no file saying what
the sequence was *for*. The individual notes cannot supply it -- each is written
looking forward, without knowing which branch mattered -- so a deliberate
negative control reads exactly like a mistake.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from nr_workbench.cli import main
from nr_workbench.commands.report import _first_line, _short

pytestmark = pytest.mark.integration


def run(root: Path, monkeypatch: pytest.MonkeyPatch, *args: str):
    monkeypatch.chdir(root)
    return CliRunner().invoke(main, list(args))


# --------------------------------------------------------------------------
# Pulling a usable line out of a note
# --------------------------------------------------------------------------

TEMPLATE = """\
# 20260810-215920Z-b949dfd2

<!-- Quoted, not stated: this is what the run was launched as, echoed back so
     the file identifies itself. A blockquote so it never wins the one-line
     summary over something you actually wrote. -->
> dream fit of backrefl3.

## Why this run

<!-- What were you testing? What changed since the last one? -->

<!-- nrw:generated -->
## Assessment

chi-squared 2.94, 26 free, 2292 points, BIC 2673.0

- **bound**: D2O rho sits on its lower bound.

<!-- /nrw:generated -->

DREAM, chisq 2.94, converged. Un-railed the per-state intensity.
"""


def test_the_line_taken_is_the_one_a_person_wrote() -> None:
    """Not the template's prompts, and not the generated assessment.

    Both traps are real: prompt text lives inside multi-line comments, so a
    line-wise filter reads it back out as prose; and the assessment's
    "chi-squared 2.94, 26 free..." line reads exactly like a written sentence
    once its fences have been stripped as ordinary comments.
    """
    assert _first_line(TEMPLATE).startswith("DREAM, chisq 2.94, converged.")


def test_a_note_that_is_still_the_template_yields_nothing() -> None:
    template_only = TEMPLATE.split("<!-- nrw:generated -->")[0]

    assert _first_line(template_only) == ""


def test_the_short_id_is_the_hash_not_the_date() -> None:
    """Every fit of one session shares its leading characters."""
    assert _short("20260810-215920Z-b949dfd2") == "b949dfd2"
    assert _short("not-an-id-shaped-thing") == "not-an-id-shaped-thing"


# --------------------------------------------------------------------------
# The command
# --------------------------------------------------------------------------


def test_a_sample_with_no_fits_still_scaffolds(project: Path, monkeypatch) -> None:
    """The prose prompts are the point; the table can be empty."""
    result = run(project, monkeypatch, "report", "Sample1", "--stdout")

    assert result.exit_code == 0, result.output
    assert "No fits recorded for this sample yet" in result.output
    assert "## The question" in result.output
    assert "## The sequence, and every branch that was abandoned" in result.output


def test_stdout_writes_no_file(project: Path, monkeypatch) -> None:
    run(project, monkeypatch, "report", "Sample1", "--stdout")

    assert not list((project / "samples/Sample1/reports").glob("*.md"))


def test_it_writes_all_three_tiers(project: Path, monkeypatch) -> None:
    """A beamtime result is read by a mixed team, so all three are always written."""
    result = run(project, monkeypatch, "report", "Sample1")

    assert result.exit_code == 0, result.output
    written = sorted(p.name for p in (project / "samples/Sample1/reports").glob("*.md"))
    assert written == [
        "sample1-what-the-fits-show-plain.md",
        "sample1-what-the-fits-show-si.md",
        "sample1-what-the-fits-show-technical.md",
    ]


def test_the_tiers_differ_in_altitude_not_in_findings(
    project: Path, monkeypatch
) -> None:
    """Same headline, different apparatus. That is the whole design."""
    run(project, monkeypatch, "report", "Sample1")
    reports = project / "samples/Sample1/reports"

    technical = (reports / "sample1-what-the-fits-show-technical.md").read_text()
    plain = (reports / "sample1-what-the-fits-show-plain.md").read_text()

    assert "**In one line:**" in technical
    assert "**In one line:**" in plain
    # The record shows the arithmetic; the plain tier explains the ideas.
    assert "The statistics, with the arithmetic shown" in technical
    assert "The ideas you need to read this" in plain
    assert "The ideas you need to read this" not in technical


def test_only_one_tier_when_asked(project: Path, monkeypatch) -> None:
    result = run(project, monkeypatch, "report", "Sample1", "--tier", "plain")

    assert result.exit_code == 0, result.output
    written = list((project / "samples/Sample1/reports").glob("*.md"))
    assert [p.name for p in written] == ["sample1-what-the-fits-show-plain.md"]


def test_an_unknown_tier_is_refused(project: Path, monkeypatch) -> None:
    result = run(project, monkeypatch, "report", "Sample1", "--tier", "nope")

    assert result.exit_code != 0
    assert "No tier" in result.output


def test_it_refuses_to_overwrite_a_report(project: Path, monkeypatch) -> None:
    """Reports are prose someone wrote; clobbering one is not a convenience."""
    run(project, monkeypatch, "report", "Sample1")

    again = run(project, monkeypatch, "report", "Sample1")

    assert again.exit_code != 0
    assert "already exist" in again.output
    # The refusal has to name the way forward, or it just blocks the work.
    assert "--topic" in again.output


def test_a_topic_starts_a_separate_report(project: Path, monkeypatch) -> None:
    """New work never has to overwrite a finished report."""
    run(project, monkeypatch, "report", "Sample1")

    result = run(project, monkeypatch, "report", "Sample1", "--topic", "buried-change")

    assert result.exit_code == 0, result.output
    names = {p.name for p in (project / "samples/Sample1/reports").glob("*.md")}
    assert "sample1-buried-change-technical.md" in names
    assert "sample1-what-the-fits-show-technical.md" in names


def test_force_refreshes_the_table_and_keeps_the_prose(
    project: Path, monkeypatch
) -> None:
    """The table is derivable and the prose is not, so only one may be rewritten."""
    run(project, monkeypatch, "report", "Sample1")
    path = project / "samples/Sample1/reports/sample1-what-the-fits-show-technical.md"
    path.write_text(
        path.read_text(encoding="utf-8") + "\nMy own conclusion, hard won.\n",
        encoding="utf-8",
    )

    result = run(project, monkeypatch, "report", "Sample1", "--force")

    assert result.exit_code == 0, result.output
    assert "My own conclusion, hard won." in path.read_text(encoding="utf-8")


def test_an_unknown_sample_is_refused(project: Path, monkeypatch) -> None:
    result = run(project, monkeypatch, "report", "nope")

    assert result.exit_code != 0
    assert "No sample" in result.output


# --------------------------------------------------------------------------
# Keeping the three from drifting apart
# --------------------------------------------------------------------------


def test_freshly_scaffolded_tiers_are_consistent(project: Path, monkeypatch) -> None:
    run(project, monkeypatch, "report", "Sample1")

    result = run(project, monkeypatch, "report", "--check", "Sample1")

    assert result.exit_code == 0, result.output
    assert "consistent" in result.output


def test_a_missing_tier_is_caught(project: Path, monkeypatch) -> None:
    """ "Always three" is a suggestion unless something checks."""
    run(project, monkeypatch, "report", "Sample1")
    (project / "samples/Sample1/reports/sample1-what-the-fits-show-si.md").unlink()

    result = run(project, monkeypatch, "report", "--check", "Sample1")

    assert result.exit_code != 0
    assert "missing tier(s) si" in result.output


def test_tiers_that_give_different_answers_are_caught(
    project: Path, monkeypatch
) -> None:
    """Three documents disagreeing is worse than one, and it is silent."""
    run(project, monkeypatch, "report", "Sample1")
    reports = project / "samples/Sample1/reports"
    placeholder = (
        "**In one line:** _the answer, before the reasoning. Write this last._"
    )
    for name, answer in (
        ("technical", "**In one line:** The oxide is partly reduced."),
        ("si", "**In one line:** The oxide is partly reduced."),
        ("plain", "**In one line:** The oxide is completely gone."),
    ):
        path = reports / f"sample1-what-the-fits-show-{name}.md"
        path.write_text(
            path.read_text(encoding="utf-8").replace(placeholder, answer),
            encoding="utf-8",
        )

    result = run(project, monkeypatch, "report", "--check", "Sample1")

    assert result.exit_code != 0
    assert "different one-line answers" in result.output


def test_a_summary_citing_an_uncited_fit_is_caught(project: Path, monkeypatch) -> None:
    """A summary cannot rest on evidence the full record omits."""
    run(project, monkeypatch, "report", "Sample1")
    path = project / "samples/Sample1/reports/sample1-what-the-fits-show-plain.md"
    path.write_text(
        path.read_text(encoding="utf-8") + "\nThe answer rests on `deadbeef`.\n",
        encoding="utf-8",
    )

    result = run(project, monkeypatch, "report", "--check", "Sample1")

    assert result.exit_code != 0
    assert "deadbeef" in result.output


def test_nrw_check_enforces_the_tiers(project: Path, monkeypatch) -> None:
    """The guard has to be in `nrw check`, or nothing makes anyone run it."""
    run(project, monkeypatch, "report", "Sample1")
    (project / "samples/Sample1/reports/sample1-what-the-fits-show-plain.md").unlink()

    result = run(project, monkeypatch, "check")

    assert result.exit_code != 0
    assert "report-tiers" in result.output


# --------------------------------------------------------------------------
# Superseding, which is not deleting
# --------------------------------------------------------------------------


def test_superseding_banners_every_tier_and_keeps_the_prose(
    project: Path, monkeypatch
) -> None:
    run(project, monkeypatch, "report", "Sample1")
    run(project, monkeypatch, "report", "Sample1", "--topic", "later")

    result = run(
        project,
        monkeypatch,
        "supersede",
        "Sample1",
        "sample1-what-the-fits-show",
        "--by",
        "sample1-later",
        "--reason",
        "The 0.45 segment was a real change, not bad data.",
    )

    assert result.exit_code == 0, result.output
    reports = project / "samples/Sample1/reports"
    for tier in ("technical", "si", "plain"):
        text = (reports / f"sample1-what-the-fits-show-{tier}.md").read_text()
        assert "Superseded on" in text
        assert "The 0.45 segment was a real change" in text
        # Not deleted: the reasoning is the reason it is kept, so the
        # document's own sections have to survive the banner.
        assert text.count("\n## ") >= 3
        assert text.startswith("# ")


def test_superseding_twice_does_not_stack_banners(project: Path, monkeypatch) -> None:
    run(project, monkeypatch, "report", "Sample1")
    run(project, monkeypatch, "report", "Sample1", "--topic", "later")
    args = (
        "supersede",
        "Sample1",
        "sample1-what-the-fits-show",
        "--by",
        "sample1-later",
        "--reason",
        "Because.",
    )
    run(project, monkeypatch, *args)
    run(project, monkeypatch, *args)

    text = (
        project / "samples/Sample1/reports/sample1-what-the-fits-show-technical.md"
    ).read_text(encoding="utf-8")

    assert text.count("Superseded on") == 1


def test_superseding_an_absent_report_is_refused(project: Path, monkeypatch) -> None:
    result = run(
        project,
        monkeypatch,
        "supersede",
        "Sample1",
        "nothing-here",
        "--by",
        "also-nothing",
        "--reason",
        "x",
    )

    assert result.exit_code != 0
    assert "No report with stem" in result.output
