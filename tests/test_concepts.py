"""Choosing which concepts a report has to explain, from what the analysis hit.

The temptation is a fixed primer, which is wrong in both directions at once: it
explains ideas the analysis never used and omits the one that decided the
answer. So selection is mechanical, driven by what `nrw assess` already found.

What is *not* here is the explanations themselves. An earlier version shipped
fourteen ready-made paragraphs; they were deleted, because every one of those
ideas is already covered in the skills that get read before any fit runs, and a
paragraph written about this sample beats a canned one with an addendum.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nr_workbench.reporting import concepts

pytestmark = pytest.mark.integration


def test_every_concept_is_complete() -> None:
    library = concepts.library()

    assert len(library) >= 12
    for slug, concept in library.items():
        assert concept.name == slug
        assert concept.title and not concept.title.endswith(".")
        assert concept.order < 999, f"{slug} has no reading order"
        assert concept.must_cover, f"{slug} says nothing the explanation must cover"


def test_the_briefs_carry_the_points_a_writer_would_drop() -> None:
    """The reason to record anything at all: the specifics that a fluent
    explanation still leaves out."""
    library = concepts.library()

    assert any(
        "equal fitting effort" in point
        for point in library["bic-model-comparison"].must_cover
    )
    assert any(
        "FWHM" in point for point in library["resolution-and-dq-fwhm"].must_cover
    )
    assert any(
        "tail fraction" in point
        for point in library["posterior-tails-vs-sigma"].must_cover
    )


def test_they_are_returned_in_reading_order_not_alphabetical() -> None:
    """These build on each other: what chi-squared is has to precede why the
    intervals get widened. Alphabetical leads with a background caveat."""
    slugs = list(concepts.library())

    assert slugs[0] == "chi-squared-reduced"
    assert slugs.index("interval-inflation") < slugs.index("bic-model-comparison")


def test_every_trigger_names_a_real_assessment_finding() -> None:
    """A trigger with a typo in it fires for nothing and nobody notices."""
    synthetic = {"model-comparison", "mixed-methods", "background-free"}
    known = {
        "bound",
        "chisq-concentrated",
        "coherent-residual",
        "correlated",
        "fringe-damping",
        "intervals-need-inflation",
        "layer-swallowed",
        "no-uncertainty",
        "posterior-bound",
        "skewed",
        "unconstrained",
        "uneven-fit",
    }

    for trigger in concepts.by_trigger():
        assert trigger in known | synthetic, f"{trigger} matches no finding kind"


def test_the_rendered_block_is_a_brief_not_an_explanation() -> None:
    """It asks for the writing; it does not do it. The writer has just used the
    idea to reach the answer, so their paragraph beats a canned one."""
    rendered = "\n".join(
        concepts.render("parameter-correlation", "`correlated` reported by abcd1234")
    )

    assert "### " in rendered
    assert "nrw:concept parameter-correlation" in rendered
    assert "abcd1234" in rendered, "the writer should see why it was selected"
    assert "Cover:" in rendered
    # The whole block is a comment, so nothing here reaches the reader as prose.
    body = rendered.split("### ", 1)[1]
    assert body.count("<!--") == 1
    assert body.rstrip().endswith("-->")


def test_an_unknown_slug_renders_nothing() -> None:
    assert concepts.render("no-such-idea") == []


# --------------------------------------------------------------------------
# Detection
# --------------------------------------------------------------------------


def _record_fit(project: Path, fit_id: str, *, model: str, method: str) -> Path:
    """Put a minimal fit on disk and in the index."""
    from nr_workbench.provenance.index import FitIndex

    directory = project / "samples" / "Sample1" / "results" / fit_id
    directory.mkdir(parents=True, exist_ok=True)
    # The lookup keys off manifest.json, not the directory name.
    (directory / "manifest.json").write_text(
        json.dumps({"provenance": {"sample": "Sample1", "model": model}}),
        encoding="utf-8",
    )
    FitIndex(project / ".nrw" / "index.jsonl").append(
        {
            "fit_id": fit_id,
            "sample": "Sample1",
            "model": model,
            "method": method,
            "path": f"samples/Sample1/results/{fit_id}",
        }
    )
    return directory


def test_findings_select_their_explainers(project: Path) -> None:
    from nr_workbench.project.layout import ProjectLayout

    directory = _record_fit(
        project, "20260812-191122Z-e75f6745", model="f3", method="dream"
    )
    (directory / "assessment.json").write_text(
        json.dumps(
            {
                "findings": [
                    {"kind": "correlated", "message": "x"},
                    {"kind": "skewed", "message": "y"},
                ]
            }
        ),
        encoding="utf-8",
    )

    found = concepts.detect(ProjectLayout(project), "Sample1")

    assert "parameter-correlation" in found.slugs
    assert "posterior-tails-vs-sigma" in found.slugs
    assert found.assessed == 1


def test_findings_are_recovered_from_an_older_note(project: Path) -> None:
    """Every project assessed before assessment.json existed has the same
    information in prose, and those are the projects most in need of this."""
    from nr_workbench.notes import GENERATED_CLOSE, GENERATED_OPEN
    from nr_workbench.project.layout import ProjectLayout

    directory = _record_fit(
        project, "20260812-191122Z-aaaaaaaa", model="f3", method="dream"
    )
    (directory / "NOTES.md").write_text(
        f"# a fit\n\n{GENERATED_OPEN}\n## Assessment\n\n"
        "- **bound**: D2O rho sits on its lower bound.\n"
        "- **layer-swallowed**: CuOx interfaces exceed its thickness.\n"
        f"{GENERATED_CLOSE}\n",
        encoding="utf-8",
    )

    found = concepts.detect(ProjectLayout(project), "Sample1")

    assert "parameter-on-a-bound" in found.slugs
    assert "thin-layer-limits" in found.slugs


def test_prose_outside_the_generated_block_is_not_read_as_a_finding(
    project: Path,
) -> None:
    """A sentence an analyst wrote that happens to start with a hyphen is not
    an assessment finding."""
    from nr_workbench.project.layout import ProjectLayout

    directory = _record_fit(
        project, "20260812-191122Z-bbbbbbbb", model="f3", method="dream"
    )
    (directory / "NOTES.md").write_text(
        "# a fit\n\n- correlated: I thought about this but did not check.\n",
        encoding="utf-8",
    )

    found = concepts.detect(ProjectLayout(project), "Sample1")

    assert "parameter-correlation" not in found.slugs


def test_two_models_select_the_comparison_explainer(project: Path) -> None:
    from nr_workbench.project.layout import ProjectLayout

    _record_fit(project, "20260812-100000Z-11111111", model="f3", method="dream")
    _record_fit(project, "20260812-110000Z-22222222", model="f3b", method="amoeba")

    found = concepts.detect(ProjectLayout(project), "Sample1")

    assert "bic-model-comparison" in found.slugs
    assert "optimiser-vs-posterior" in found.slugs


def test_chi_squared_is_always_included(project: Path) -> None:
    """Every tier quotes it, so it is never the one crowded out."""
    from nr_workbench.project.layout import ProjectLayout

    found = concepts.detect(ProjectLayout(project), "Sample1")

    assert "chi-squared-reduced" in found.slugs
    chosen, _ = found.top(1)
    assert chosen == ("chi-squared-reduced",)


def test_the_budget_keeps_the_best_supported_in_reading_order(
    project: Path,
) -> None:
    """Chosen by weight so the document reflects the analysis; ordered by
    reading order so it still flows."""
    from nr_workbench.project.layout import ProjectLayout

    for index, kinds in enumerate(
        [["correlated", "skewed"], ["correlated"], ["correlated"]]
    ):
        directory = _record_fit(
            project,
            f"20260812-12000{index}Z-cccccc0{index}",
            model="f3",
            method="dream",
        )
        (directory / "assessment.json").write_text(
            json.dumps({"findings": [{"kind": k, "message": ""} for k in kinds]}),
            encoding="utf-8",
        )

    found = concepts.detect(ProjectLayout(project), "Sample1")
    chosen, remainder = found.top(3)

    assert "chi-squared-reduced" in chosen
    # `correlated` was seen three times and `skewed` once.
    assert "parameter-correlation" in chosen
    assert "posterior-tails-vs-sigma" in remainder
    order = list(concepts.library())
    assert list(chosen) == sorted(chosen, key=order.index)
