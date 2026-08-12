"""Do the automatic checks reproduce what a week of expert analysis found?

`apr2025/cu-thf-expt11` is a labelled test set that already exists: 17
dated findings written by hand, 25 fits and 5.8 GB of matching artifacts, with
the right answers recorded in the project's own `docs/ground_truths.md`. Each
case below names a finding and asserts that the corresponding check fires on
the corresponding fit.

This is the gate the plan puts in front of fitting autonomy. An agent that
cannot re-derive what a human already derived from the same files has no
business deciding what to fit next --- and the only honest way to learn whether
it can is to try it against the answers.

The corpus lives outside the repository (5.8 GB, in Dropbox), so every test
skips when it is absent. That makes this a benchmark you run deliberately
rather than a gate CI enforces, which is the correct trade: the alternative is
committing gigabytes or asserting nothing.

Run with:

    pytest tests/test_benchmark_expt11.py -v
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

CORPUS = Path.home() / "Dropbox-ORNL/experiments/apr2025/cu-thf-expt11"

pytestmark = pytest.mark.skipif(
    not (CORPUS / ".nrw" / "index.jsonl").is_file(),
    reason=f"the expt11 corpus is not present at {CORPUS}",
)

#: The fit whose oxide the analyst found was an artefact. Ground truth:
#: "OCV1's oxide in ... is an artefact -- dTHF.roughness was never fitted".
ARTEFACT_OXIDE = "20260806-205951Z-78c5736e"

#: The promoted results. Both are *worse* in chi-squared than the best fit in
#: their arm, which is why chi-squared alone cannot be the score.
PROMOTED_TNR = "20260807-163359Z-0103d9c7"
PROMOTED_STEADY = "20260807-155810Z-ec6d0134"

#: The spec that produced the artefact, named in the ground truth as the cause:
#: it "never declares dTHF.roughness, so it stayed at the value nrw model new
#: scaffolded: 20 A. Nobody chose it."
CULPRIT_SPEC = "cu-thf-coref-ocv1-ocv2"


def fit_dir(fit_id: str) -> Path:
    """A recorded fit's directory in the corpus."""
    return CORPUS / "samples" / "expt11" / "results" / fit_id


def assess(fit_id: str):
    """Run the automatic checks over one recorded fit."""
    from nr_workbench.fitting.assess import check
    from nr_workbench.provenance.record import FitDirectory

    directory = fit_dir(fit_id)
    return check(directory, FitDirectory(directory).read_manifest())


def kinds(fit_id: str) -> set[str]:
    """The finding kinds one fit produces."""
    return {f.kind for f in assess(fit_id).findings}


# --------------------------------------------------------------------------
# F7 -- the swallowed oxide, and its cause
# --------------------------------------------------------------------------


def test_the_artefact_oxide_is_detected() -> None:
    """Ground truth: "that rho pinned at its 4.0 floor: a layer that does not
    exist has nothing to hold its SLD anywhere", with the table giving
    sigma_top + sigma_bot = 32.99 against t = 21.29, a ratio of 1.55.
    """
    swallowed = [
        f for f in assess(ARTEFACT_OXIDE).findings if f.kind == "layer-swallowed"
    ]

    assert swallowed, "the finding that invalidated a session's worth of fits"
    oxide = [f for f in swallowed if "CuOx" in (f.parameter or "")]
    assert oxide, [f.parameter for f in swallowed]
    assert "1.55x" in oxide[0].message, oxide[0].message


def test_the_cause_of_the_artefact_is_named() -> None:
    """The roughness responsible was never declared, so it sat at the value
    the scaffold wrote. `_check_unfitted` tested whole layers and said nothing
    because `dTHF.rho` *was* declared."""
    from nr_workbench.spec.models import load_spec
    from nr_workbench.spec.validate import validate_spec

    spec_path = CORPUS / "samples" / "expt11" / "models" / f"{CULPRIT_SPEC}.yaml"
    report = validate_spec(load_spec(spec_path), CORPUS)

    held = " ".join(report.info)
    assert "dTHF.roughness=20" in held, held


def test_the_ranges_that_allowed_it_are_flagged_before_any_fit() -> None:
    """The earlier catch: "a range permitting sigma > t/4 guarantees the fit
    can go there, and it will"."""
    from nr_workbench.contradictions import check as contradictions
    from nr_workbench.spec.models import load_spec

    spec_path = CORPUS / "samples" / "expt11" / "models" / f"{CULPRIT_SPEC}.yaml"
    found = contradictions(load_spec(spec_path)).contradictions

    swallowing = [c for c in found if c.kind == "roughness-swallows-layer"]
    assert [c.subject for c in swallowing] == ["CuOx"]


# --------------------------------------------------------------------------
# The degeneracies the analyst leaned on hardest
# --------------------------------------------------------------------------


def test_the_rho_thickness_ridge_is_found() -> None:
    """`thin-layer-degeneracy` says to look for the (rho, t) ridge, and the
    ground truths turn on r = -0.93 and r = +0.97 pairs. Fixing one side is
    what broke it: "Cu growth is reliable here because fixing dTHF.roughness
    removes the r=-0.93 pair"."""
    correlated = [f for f in assess(PROMOTED_TNR).findings if f.kind == "correlated"]

    assert correlated, "the discriminator the analyst used most"
    message = correlated[0].message
    assert "CuOx rho" in message and "CuOx thickness" in message


# --------------------------------------------------------------------------
# F10 -- three pinned parameters, three different correct actions
# --------------------------------------------------------------------------


def test_the_pinned_parameters_are_all_reported() -> None:
    """ "Three parameters, three walls." The check reports them; which of the
    three should be widened is the judgement it deliberately does not make --
    Ti.rho at -2.0 is bulk titanium and widening it would have destroyed the
    result."""
    railed = [
        f for f in assess("20260807-132517Z-40f6a405").findings if f.kind == "bound"
    ]

    assert len(railed) >= 3, [f.parameter for f in railed]
    assert all(f.severity == "warn" for f in railed)
    assert not any("widen" in f.message.lower() for f in railed), (
        "the check reports the pin; it must not prescribe the response"
    )


# --------------------------------------------------------------------------
# The stray partial
# --------------------------------------------------------------------------


def test_the_time_resolved_run_filed_as_a_steady_state_is_a_blocker() -> None:
    """ "Its living under data/steady/ is a filing accident." A daemon reacting
    to file arrival would fit it."""
    from nr_workbench.instrument.header import read_header
    from nr_workbench.project.scan import scan_sample
    from nr_workbench.reconcile import reconcile

    scan = scan_sample(CORPUS, "expt11")
    headers = [
        read_header(CORPUS / path)
        for measurement in scan.steady.values()
        for path in measurement.partials.values()
    ]
    notes = (CORPUS / "samples" / "expt11" / "sample.md").read_text(encoding="utf-8")

    found = reconcile(
        "expt11", headers, notes, series_runs={s.run for s in scan.series if s.run}
    )

    blockers = [f for f in found.findings if f.severity == "blocker"]
    assert [f.run for f in blockers] == [218389]


# --------------------------------------------------------------------------
# What the benchmark must NOT claim
# --------------------------------------------------------------------------


def test_the_promoted_fits_are_not_the_lowest_chi_squared() -> None:
    """The premise of the whole approach. Both promoted fits are worse in
    chi-squared than the best in their arm, so a loop scored on chi-squared
    gets both of the decisions that produced the paper wrong.
    """
    rows = [
        json.loads(line)
        for line in (CORPUS / ".nrw" / "index.jsonl").read_text().splitlines()
        if line.strip()
    ]
    fits = {r["fit_id"]: r for r in rows if r.get("event", "fit") == "fit"}

    tnr = [f for f in fits.values() if "tnr" in f.get("model", "")]
    best_tnr = min(f["chisq"] for f in tnr if f.get("chisq"))
    assert fits[PROMOTED_TNR]["chisq"] > best_tnr


def test_finding_count_is_not_a_score() -> None:
    """Recorded so nobody builds a ranking on it. The promoted tNR fit has the
    fewest findings of all twenty; the promoted steady fit has one of the most
    -- because it co-refines three states and the count scales with the
    parameter count, not with the quality of the answer.
    """
    tnr = len(assess(PROMOTED_TNR).findings)
    steady = len(assess(PROMOTED_STEADY).findings)

    assert tnr < steady, (
        "both are promoted; a count that separates them is measuring size"
    )


def test_every_check_runs_on_every_recorded_fit() -> None:
    """Coverage, not correctness: a check that raises on a real directory is
    worse than one that finds nothing, because it takes the others down with
    it."""
    from nr_workbench.provenance.record import FitDirectory

    rows = [
        json.loads(line)
        for line in (CORPUS / ".nrw" / "index.jsonl").read_text().splitlines()
        if line.strip()
    ]
    recorded = [r for r in rows if r.get("event", "fit") == "fit"]

    assessed = 0
    for row in recorded:
        directory = fit_dir(row["fit_id"])
        if not (directory / "manifest.json").is_file():
            continue  # an orphan; `nrw check` reports those separately
        from nr_workbench.fitting.assess import check

        check(directory, FitDirectory(directory).read_manifest())
        assessed += 1

    assert assessed >= 20, f"only {assessed} of {len(recorded)} fits were assessable"
