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
    assert "## Why the sequence went the way it did" in result.output


def test_stdout_writes_no_file(project: Path, monkeypatch) -> None:
    run(project, monkeypatch, "report", "Sample1", "--stdout")

    assert not list((project / "samples/Sample1/reports").glob("*.md"))


def test_it_writes_into_the_samples_reports_directory(
    project: Path, monkeypatch
) -> None:
    result = run(project, monkeypatch, "report", "Sample1")

    assert result.exit_code == 0, result.output
    written = list((project / "samples/Sample1/reports").glob("*.md"))
    assert len(written) == 1
    assert "The sequence" in written[0].read_text(encoding="utf-8")


def test_it_refuses_to_overwrite_a_report(project: Path, monkeypatch) -> None:
    """Reports are prose someone wrote; clobbering one is not a convenience."""
    run(project, monkeypatch, "report", "Sample1")

    again = run(project, monkeypatch, "report", "Sample1")

    assert again.exit_code != 0
    assert "already exists" in again.output


def test_force_refreshes_the_table_and_keeps_the_prose(
    project: Path, monkeypatch
) -> None:
    """The table is derivable and the prose is not, so only one may be rewritten."""
    run(project, monkeypatch, "report", "Sample1")
    path = next((project / "samples/Sample1/reports").glob("*.md"))
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
