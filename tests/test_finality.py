"""`nrw check`: a report that names the answer the record does not record.

`nrw promote` is the scientist's decision, and the agent is forbidden from
running it. What that leaves is a report whose prose says "reference fit: <id>"
while the index holds no promotion -- the state the reference experiment ended
in. The designation then lives in one paragraph, and `nrw ls`, `nrw whence` and
`nrw pack` all disagree with it.

This check fails the build, so the tests here are mostly about what it must
*not* fire on. A gate that cries wolf is a gate people stop reading.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nr_workbench.commands.provenance_cmd import check_reported_finality
from nr_workbench.project.layout import ProjectLayout
from nr_workbench.provenance.index import FitIndex

pytestmark = pytest.mark.integration

FIT = "20260810-215920Z-b949dfd2"
OTHER = "20260810-214418Z-231fb17f"


def report(project: Path, body: str, sample: str = "Sample1") -> None:
    reports = project / "samples" / sample / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "findings.md").write_text(body, encoding="utf-8")


def found(project: Path) -> list[dict[str, str]]:
    layout = ProjectLayout(root=project)
    return check_reported_finality(layout, FitIndex(layout.index_file))


# --------------------------------------------------------------------------
# What it catches
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        f"Reference fit: **`{FIT}`** (model backrefl3, DREAM, chi2 = 2.94).",
        f"The final fit is {FIT}.",
        f"We take {FIT} as final.",
        f"{FIT} is the keeper.",
        f"The answer is {FIT}.",
    ],
)
def test_a_claimed_answer_that_is_not_promoted_is_reported(project, line) -> None:
    report(project, f"# Findings\n\n{line}\n")

    problems = found(project)

    assert [p["kind"] for p in problems] == ["unpromoted-reference"]
    assert FIT in problems[0]["detail"]
    assert "nrw promote" in problems[0]["detail"], "the fix has to be named"


def test_each_claim_is_reported_once_per_report(project) -> None:
    """A report naming its reference fit five times is one problem, not five."""
    report(project, f"# F\n\nReference fit {FIT}.\n\nThe answer is {FIT} again.\n")

    assert len(found(project)) == 1


# --------------------------------------------------------------------------
# What it must not catch
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        f"{FIT} is not the reference fit; it was superseded.",
        f"The reference fit is no longer {FIT}.",
        f"We used {OTHER} rather than the final fit {FIT}.",
        f"{FIT} isn't the keeper.",
        f"{FIT} would be the reference fit if it had converged.",
        f"The answer is not yet clear from {FIT}.",
    ],
)
def test_a_negated_claim_is_not_a_claim(project, line) -> None:
    report(project, f"# Findings\n\n{line}\n")

    assert found(project) == []


def test_merely_discussing_a_fit_is_not_a_claim(project) -> None:
    """Most of a report is this, and none of it asserts finality."""
    report(
        project,
        f"# Findings\n\nDREAM on {FIT} gave chi2 2.94 with Cu at 409 A, which is\n"
        f"consistent with {OTHER}. Both show the same structure.\n",
    )

    assert found(project) == []


def test_a_claim_with_no_fit_id_on_the_line_is_ignored(project) -> None:
    """Prose about reference fits in general is not a designation."""
    report(project, "# Findings\n\nA reference fit should always be promoted.\n")

    assert found(project) == []


def test_a_sample_with_no_reports_is_fine(project) -> None:
    assert found(project) == []


def test_a_promoted_fit_named_as_the_answer_is_correct_and_silent(
    project, monkeypatch
) -> None:
    """The whole point: once the record agrees with the prose, nothing to say."""
    from nr_workbench.provenance.index import EVENT_PROMOTE

    layout = ProjectLayout(root=project)
    index = FitIndex(layout.index_file)
    index.append(
        {
            "event": "fit",
            "fit_id": FIT,
            "sample": "Sample1",
            "model": "backrefl3",
            "status": "ok",
        }
    )
    index.append(
        {
            "event": EVENT_PROMOTE,
            "fit_id": FIT,
            "sample": "Sample1",
            "label": "final",
            "reason": "lowest chisq, converged",
        }
    )
    report(project, f"# Findings\n\nReference fit: {FIT}.\n")

    assert found(project) == []
