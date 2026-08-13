"""Which ideas an analysis actually used, so the report can explain those.

A report written for someone who does not fit reflectivity has to explain the
concepts it leans on. The temptation is a fixed primer -- a paragraph on
chi-squared, one on roughness, one on Bayes -- which is wrong in both
directions at once: it explains ideas this analysis never used and misses the
one that decided the answer.

So what this module supplies is **selection**, not prose. Which concepts a
particular analysis ran into is mechanical: `nrw assess` already detects
correlated pairs, parameters on bounds, unconstrained and skewed posteriors,
and layers swallowed by their own roughness. Each maps to a concept. A few more
are read off the fit chain -- two models compared, two optimisers used, a free
background fitted.

**The explanation itself is written by whoever writes the report.** An earlier
version of this shipped fourteen ready-made paragraphs; they were deleted. The
analyst -- or the assistant -- has just used these ideas to reach the answer,
and every one of them is already covered in the skills that get read before any
fit runs. A canned paragraph followed by a bolted-on "and here is what it meant
here" reads worse than one paragraph written about this sample, and it is 3,400
words of the package to maintain besides.

What is worth pinning down is the part a generic explanation drops: that a BIC
comparison means nothing unless both models got equal fitting effort, that dQ
is FWHM rather than sigma, that a tail fraction and a sigma answer different
questions. Those survive here as ``must_cover`` -- requirements on the
explanation, not the explanation.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Concept:
    """One idea a report may have to explain.

    Attributes:
        name: Slug, used in the marker and by ``--concept``.
        order: Reading order. These build on each other -- what chi-squared is
            has to precede why the intervals were widened.
        title: The heading the explanation goes under.
        triggers: Assessment finding kinds, or synthetic markers, that select
            it.
        must_cover: The points the explanation has to make. Not a summary of
            the idea -- the specific things a fluent writer still leaves out.
        core: Always included, whatever the triggers say.
        see_also: Related slugs.
    """

    name: str
    order: int
    title: str
    triggers: tuple[str, ...] = ()
    must_cover: tuple[str, ...] = ()
    core: bool = False
    see_also: tuple[str, ...] = ()


#: Every concept, in reading order.
#:
#: Two carry no triggers on purpose: back reflection and the null hypothesis
#: are properties of an experiment's design rather than of a fit record, so
#: they are offered by `nrw report --concepts` and added deliberately.
_CONCEPTS: tuple[Concept, ...] = (
    Concept(
        name="chi-squared-reduced",
        order=1,
        title="What chi-squared is, and what a good value means",
        triggers=("chisq-concentrated", "uneven-fit"),
        core=True,
        must_cover=(
            "chi-squared counts each miss in units of that point's own error bar",
            "reduced chi-squared near 1 means the model matches to within the noise",
            "well above 1 means a missing model OR understated errors on the data, "
            "and chi-squared alone cannot separate those",
            "well below 1 usually means overstated errors, not an unusually good fit",
            "a lower chi-squared is NOT automatically a better model, because "
            "adding free parameters always lowers it",
        ),
        see_also=("interval-inflation", "bic-model-comparison"),
    ),
    Concept(
        name="interval-inflation",
        order=2,
        title="Why the error bars were widened before they were quoted",
        triggers=("intervals-need-inflation",),
        must_cover=(
            "the raw uncertainties assume the model is right and the data errors "
            "are right; a chi-squared above 1 says one of those is false",
            "the repair is multiplying every interval by sqrt(chi2_red) -- give "
            "the actual factor used here",
            "it assumes the discrepancy is spread evenly, which is often not true; "
            "say so rather than presenting it as exact",
            "a difference that survives inflation is a result; one that only "
            "exists before it is not",
        ),
        see_also=("chi-squared-reduced", "posterior-tails-vs-sigma"),
    ),
    Concept(
        name="parameter-correlation",
        order=3,
        title="Why two of these numbers can only be known together",
        triggers=("correlated",),
        must_cover=(
            "the fit finds the combination that reproduces the curve, not each "
            "number separately",
            "two parameters can trade against each other with no change to the "
            "predicted data -- give the actual pair and its r",
            "their error bars are misleading read one at a time, because the "
            "errors are not independent",
            "the fix is to quote the combination that IS determined, or to add a "
            "measurement that breaks the trade",
        ),
        see_also=("degeneracy-and-invariants",),
    ),
    Concept(
        name="degeneracy-and-invariants",
        order=4,
        title="Why a combination is quoted instead of a layer",
        triggers=("correlated", "layer-swallowed"),
        must_cover=(
            "layers are how we describe the depth profile; they are not what the "
            "neutrons see",
            "name the invariant this analysis quotes -- a thickness times a "
            "contrast, or a total -- and why it is the determined thing",
            "say plainly that the individual slab numbers are a parametrisation "
            "rather than a measurement, or the reader will believe their error bars",
        ),
        see_also=("parameter-correlation", "thin-layer-limits"),
    ),
    Concept(
        name="thin-layer-limits",
        order=5,
        title="What can honestly be said about a very thin layer",
        triggers=("layer-swallowed",),
        must_cover=(
            "below roughly 30 A thickness and SLD stop being separately determined",
            "if the interfaces are broad the layer's nominal density may be "
            "reached nowhere in the real profile",
            "a model comparison can reject a layer that is really there, so "
            "'not required by the data' and 'not present' are different claims",
            "a feature of the profile can be far more robust than any slab "
            "parameter -- e.g. a region below the SLD of anything it could be "
            "made of cannot be a mixture of those ingredients",
        ),
        see_also=("degeneracy-and-invariants", "null-hypothesis-fit"),
    ),
    Concept(
        name="posterior-tails-vs-sigma",
        order=6,
        title="When a large sigma and a real chance of zero are both true",
        triggers=("skewed",),
        must_cover=(
            "a sampler returns a cloud of consistent values; 'value +- sigma' is "
            "a summary that assumes that cloud is symmetric",
            "when it is skewed, the sigma and the tail fraction answer different "
            "questions -- give both numbers for the parameter in question",
            "quote the tail fraction, because it is the one that answers 'could "
            "this be absent?'",
            "where they disagree, quote the more conservative and say why",
        ),
        see_also=("interval-inflation",),
    ),
    Concept(
        name="parameter-on-a-bound",
        order=7,
        title="What it means that a value sits exactly on its limit",
        triggers=("bound", "posterior-bound"),
        must_cover=(
            "a value on its bound is not a measurement -- the fit wanted to go "
            "further and was not allowed",
            "two causes: the bound is too tight, or the parameter is "
            "unconstrained and drifted until something stopped it. Widening the "
            "bound distinguishes them",
            "its uncertainty is meaningless, so it must never be quoted as a "
            "measurement with one",
        ),
        see_also=("unconstrained-posterior",),
    ),
    Concept(
        name="unconstrained-posterior",
        order=8,
        title="A number the measurement does not actually determine",
        triggers=("unconstrained",),
        must_cover=(
            "the posterior fills most of the allowed range, so the answer came "
            "from the bounds rather than from the data",
            "it still gets a plausible-looking value and error bar, and looks "
            "exactly like the numbers that were measured",
            "either fix it at a justified value and say so, or keep it free and "
            "call it conditional -- never list it beside constrained parameters "
            "unmarked",
        ),
        see_also=("parameter-on-a-bound", "degeneracy-and-invariants"),
    ),
    Concept(
        name="optimiser-vs-posterior",
        order=9,
        title="Why the fits were run twice, with two different algorithms",
        triggers=("no-uncertainty", "mixed-methods"),
        must_cover=(
            "amoeba finds a best point quickly and cannot say how well "
            "determined it is",
            "DREAM explores the whole consistent region and is the only one of "
            "the two that can produce an uncertainty, a correlation or a "
            "probability",
            "so no number with an error bar on it may come from an amoeba run, "
            "and two amoeba results cannot be compared for significance",
        ),
        see_also=("posterior-tails-vs-sigma",),
    ),
    Concept(
        name="bic-model-comparison",
        order=10,
        title="How the competing models were compared fairly",
        triggers=("model-comparison",),
        must_cover=(
            "a model with more parameters always fits better, so raw chi-squared "
            "cannot choose between models of different complexity",
            "BIC charges k*ln(n) for that -- write the actual arithmetic, not "
            "just the verdict",
            "only differences mean anything; give the scale you are reading them on",
            "the comparison is void unless both models got equal fitting effort. "
            "Say that they did",
        ),
        see_also=("chi-squared-reduced", "null-hypothesis-fit"),
    ),
    Concept(
        name="null-hypothesis-fit",
        order=11,
        title="Deliberately fitting the model we thought was wrong",
        triggers=(),
        must_cover=(
            "a model containing a layer fitting well does not show the layer is "
            "there; something has to show the model without it fits worse",
            "both models must get the same starting strategy, optimiser and "
            "sampler settings",
            "where the damage falls is the informative half: localised to the "
            "state and Q range the layer should matter in means the layer is "
            "real; smeared evenly means the null was optimised badly",
        ),
        see_also=("bic-model-comparison", "thin-layer-limits"),
    ),
    Concept(
        name="background-can-manufacture-a-layer",
        order=12,
        title="How an unmodelled background can invent surface structure",
        triggers=("background-free",),
        must_cover=(
            "reflectivity falls by orders of magnitude, and at high Q it can "
            "reach the instrument background",
            "a background present in the data but absent from the model is "
            "explained by the fit inventing a thin surface layer",
            "bound it physically: left loose it stops being a background and "
            "starts absorbing coherent fringe mismatch, which produces the "
            "lowest chi-squared and the least trustworthy fit",
            "only the highest-angle data can see it, so check whether the "
            "no-layer model failed where a background could never have helped",
        ),
        see_also=("null-hypothesis-fit",),
    ),
    Concept(
        name="resolution-and-dq-fwhm",
        order=13,
        title="Why the fringes are blurrier than the model expects",
        triggers=("fringe-damping", "coherent-residual"),
        must_cover=(
            "each point averages over a spread of Q, which washes out fine fringes",
            "the dQ column at this beamline is FWHM, not sigma -- a factor of "
            "2.355, and getting it wrong rescales every resolution",
            "the sample adds its own smearing beyond the instrument's, worst at "
            "the lowest angle where the footprint is longest",
            "smearing and roughness both damp fringes, so an underestimated "
            "resolution reappears as a fitted roughness that is really a "
            "description of the beam",
        ),
    ),
    Concept(
        name="back-reflection",
        order=14,
        title="Measuring through the substrate, and why it changes the picture",
        triggers=(),
        must_cover=(
            "the beam enters through the substrate rather than the outer medium, "
            "which is standard for an electrochemical cell",
            "the layer order is reversed relative to how the sample is drawn",
            "the critical edge belongs to the BURIED interface, not the surface, "
            "so a change there is evidence about the buried structure -- the "
            "opposite of the usual instinct",
        ),
        see_also=("resolution-and-dq-fwhm",),
    ),
)


def library() -> dict[str, Concept]:
    """Every concept, keyed by slug, in reading order.

    Returns:
        Mapping of slug to :class:`Concept`.
    """
    return {concept.name: concept for concept in _CONCEPTS}


def by_trigger() -> dict[str, list[str]]:
    """Map each trigger to the slugs it selects.

    Returns:
        Mapping of trigger name to concept slugs.
    """
    mapping: dict[str, list[str]] = {}
    for concept in _CONCEPTS:
        for trigger in concept.triggers:
            mapping.setdefault(trigger, []).append(concept.name)
    return mapping


#: A finding line inside the generated block: ``- **kind**: message``, or the
#: same without emphasis for an informational one.
_NOTE_FINDING = re.compile(r"^\s*-\s+\*{0,2}([a-z][a-z0-9-]+)\*{0,2}\s*:", re.MULTILINE)


def _assessment_kinds(fit_dir: Path) -> set[str]:
    """Finding kinds recorded for one fit.

    Prefers ``assessment.json``. Falls back to parsing the generated block in
    ``NOTES.md``, because every project assessed before that file existed has
    the same information sitting in prose -- and without the fallback those
    projects get only the coarse triggers, which is precisely the case where a
    plain-language report is most needed.
    """
    path = fit_dir / "assessment.json"
    if path.is_file():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        findings = payload.get("findings")
        if isinstance(findings, list):
            return {
                str(finding.get("kind"))
                for finding in findings
                if isinstance(finding, dict) and finding.get("kind")
            }

    return _kinds_from_notes(fit_dir / "NOTES.md")


def _kinds_from_notes(notes: Path) -> set[str]:
    """Finding kinds recovered from the generated block of a note.

    Scoped to the fenced block on purpose. Anywhere else in a note is prose the
    analyst wrote, and a sentence there that happens to start with a hyphen and
    a word would otherwise be read as a finding.
    """
    if not notes.is_file():
        return set()
    try:
        text = notes.read_text(encoding="utf-8")
    except OSError:
        return set()

    from nr_workbench.notes import GENERATED_CLOSE, GENERATED_OPEN

    known = set(by_trigger())
    found: set[str] = set()
    for block in re.findall(
        re.escape(GENERATED_OPEN) + r"(.*?)" + re.escape(GENERATED_CLOSE),
        text,
        flags=re.DOTALL,
    ):
        found.update(match for match in _NOTE_FINDING.findall(block) if match in known)
    return found


@dataclass(frozen=True)
class Detection:
    """What was detected, and what it rests on.

    Attributes:
        slugs: Concept slugs to explain, in reading order.
        reasons: Slug to the human-readable evidence that selected it.
        weights: Slug to how many fits support it, for choosing which to keep
            when there are more than a short document can carry.
        assessed: How many of the sample's fits had an assessment to read.
        total: How many fits the sample has.
    """

    slugs: tuple[str, ...]
    reasons: dict[str, str]
    weights: dict[str, int] = field(default_factory=dict)
    assessed: int = 0
    total: int = 0

    def top(self, limit: int) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Split into the ones worth explaining and the rest.

        Chosen by weight -- a concept five fits ran into matters more to this
        analysis than one a single fit brushed -- then returned in reading
        order, so the document still flows.

        Args:
            limit: How many to keep.

        Returns:
            ``(chosen, remainder)``, both in reading order.
        """
        ranked = sorted(self.slugs, key=lambda s: (-self.weights.get(s, 0), s))
        chosen = set(ranked[:limit])
        order = {slug: i for i, slug in enumerate(self.slugs)}
        return (
            tuple(sorted(chosen, key=lambda s: order.get(s, 999))),
            tuple(s for s in self.slugs if s not in chosen),
        )


def detect(layout: Any, sample: str) -> Detection:
    """Work out which concepts this sample's analysis actually ran into.

    Reads what is on disk rather than recomputing: an assessment is expensive
    (it reads DREAM chains) and has already been run for any fit worth citing.
    A sample with no assessments yields only the synthetic triggers, and says
    so through :attr:`Detection.assessed`.

    Args:
        layout: The project layout.
        sample: Sample identifier.

    Returns:
        The detection, including why each concept was selected.
    """
    from nr_workbench.provenance.index import FitIndex
    from nr_workbench.provenance.lookup import fit_dir as find_fit_dir

    index = FitIndex(layout.index_file)
    rows = index.fits(sample=sample)

    kinds: dict[str, list[str]] = {}
    methods: set[str] = set()
    models: set[str] = set()
    assessed = 0

    for row in rows:
        method = str(row.get("method") or "").strip().lower()
        if method:
            methods.add(method)
        model = str(row.get("model") or "").strip()
        if model:
            models.add(model)

        directory = find_fit_dir(layout, row)
        if directory is None:
            continue
        found = _assessment_kinds(directory)
        if found:
            assessed += 1
        for kind in found:
            kinds.setdefault(kind, []).append(str(row.get("fit_id", "")))

    triggers: dict[str, str] = {}
    strength: dict[str, int] = {}
    for kind, fit_ids in kinds.items():
        shown = ", ".join(fit_ids[:2])
        more = f" and {len(fit_ids) - 2} more" if len(fit_ids) > 2 else ""
        triggers[kind] = f"`{kind}` reported by {shown}{more}"
        strength[kind] = len(fit_ids)

    # Synthetic triggers: true of the chain rather than of any one fit.
    if len(models) > 1:
        triggers["model-comparison"] = (
            f"{len(models)} distinct models were fitted for this sample"
        )
    if len(methods) > 1:
        triggers["mixed-methods"] = f"both {' and '.join(sorted(methods))} were used"
    if _has_free_background(layout, sample):
        triggers["background-free"] = "a spec here fits a free background"

    mapping = by_trigger()
    reasons: dict[str, str] = {}
    weights: dict[str, int] = {}
    for trigger, evidence in triggers.items():
        for slug in mapping.get(trigger, []):
            reasons.setdefault(slug, evidence)
            weights[slug] = max(weights.get(slug, 0), strength.get(trigger, 1))

    for slug, concept in library().items():
        if concept.core:
            reasons.setdefault(slug, "always included")
            # Above anything a trigger can reach, so the idea every other
            # explanation depends on is never the one crowded out.
            weights[slug] = 10_000

    ordered = tuple(slug for slug in library() if slug in reasons)
    return Detection(
        slugs=ordered,
        reasons=reasons,
        weights=weights,
        assessed=assessed,
        total=len(rows),
    )


def _has_free_background(layout: Any, sample: str) -> bool:
    """Whether any spec for this sample leaves a background free.

    A textual test rather than a schema one: specs are small, the key is
    unambiguous, and a parse failure on one malformed spec must not decide the
    question for the whole sample.
    """
    models_dir = layout.sample(sample) / "models"
    if not models_dir.is_dir():
        return False
    for path in sorted(models_dir.glob("*.yaml")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            if "background" not in line:
                continue
            if "fixed: true" in line:
                continue
            if re.search(r"(pm|range|bounds)\s*:", line):
                return True
    return False


def render(slug: str, reason: str = "") -> list[str]:
    """Render the brief for one concept: a heading and what to cover.

    The explanation is not written here. Whoever writes the report has just
    used this idea to reach the answer, so a paragraph from them about *this*
    sample beats a canned one with a bolted-on addendum -- and the canned one
    would be package mass duplicating what the skills already say.

    Args:
        slug: Concept slug.
        reason: The evidence that selected it, shown so the writer can see
            whether the detection was right.

    Returns:
        Markdown lines, or an empty list if the slug is unknown.
    """
    concept = library().get(slug)
    if concept is None:
        return []

    lines = [
        f"### {concept.title}",
        "",
        f"<!-- nrw:concept {concept.name}",
    ]
    if reason:
        lines.append(f"     Selected because: {reason}")
    lines += [
        "",
        "     Explain this in plain language for a reader who does not fit",
        "     reflectivity -- roughly 150-250 words -- and end with what it",
        "     meant for THIS sample, with the numbers. Cover:",
    ]
    lines += [f"       - {point}" for point in concept.must_cover]
    if concept.see_also:
        lines.append(f"     See also: {', '.join(concept.see_also)}.")
    lines += ["-->", ""]
    return lines
