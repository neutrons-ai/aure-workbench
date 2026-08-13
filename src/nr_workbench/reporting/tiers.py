"""Three renderings of one analysis, for three readers who all exist.

A beamtime result is read by a mixed team. The person who will defend the model
in review needs every branch and the arithmetic. The person writing the paper
needs a methods paragraph and a parameter table they can paste. The person who
owns the chemistry and does not fit reflectivity needs the conclusion, and the
two or three ideas required to trust it.

One document cannot be all three. Written for the expert it is unreadable by
the chemist; written for the chemist it is unciteable by the expert. The
reference project's single report ended up as an expert argument with a few
concepts explained in passing and no entry point for anyone else.

So all three are always scaffolded. Not one chosen by audience -- the audience
setting says which one leads, because the *team* is mixed even when the person
who asked is not.

What differs between them is genuinely the writing, not the findings. The same
fit ids, the same numbers, the same headline sentence; different altitude and
different apparatus. :func:`nr_workbench.commands.report.check_tiers` is what
stops them drifting into three different answers.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: How the generated fit table is rendered in each tier.
#:
#: ``full`` -- every fit, in order. ``reportable`` -- only the fits a sampler
#: produced, since an amoeba exploration is not a citable result and a paper's
#: supporting information should not imply it was. ``none`` -- a pointer.
SequenceMode = str


@dataclass(frozen=True)
class Tier:
    """One rendering of a sample's analysis.

    Attributes:
        key: Short name, used in the filename suffix and the tier marker.
        filename_suffix: Appended to the shared stem.
        heading: Document title, formatted with ``sample``.
        audience: Who this is for, in one line, printed in the document.
        blurb: What belongs here and what does not.
        sequence: How to render the generated fit table.
        concepts: Whether to list the concepts this analysis ran into,
            each with a brief for the explanation to be written.
        prompts: ``(section, prompt)`` pairs, in document order.
    """

    key: str
    filename_suffix: str
    heading: str
    audience: str
    blurb: str
    sequence: SequenceMode
    concepts: bool
    prompts: tuple[tuple[str, str], ...] = field(default_factory=tuple)


TECHNICAL = Tier(
    key="technical",
    filename_suffix="-technical",
    heading="{sample}: the full record",
    audience="Someone who will argue with this — a reviewer, or you in a year.",
    blurb=(
        "Everything, including what did not work. This is the tier that has to "
        "survive somebody\nchecking it. Show the arithmetic rather than its "
        "conclusion, and give every abandoned\nbranch the sentence that says "
        "why it was abandoned."
    ),
    sequence="full",
    concepts=False,
    prompts=(
        (
            "The question",
            "What was this sample measured to find out? In the language of the "
            "experiment rather than the model.",
        ),
        (
            "What the checks found before any fitting",
            "The offline checks, the header inconsistencies, the overlap "
            "offsets, the quarantined runs. Everything that was true before a "
            "model existed, and what it ruled out. A reader who does not know "
            "a run was excluded cannot judge the rest.",
        ),
        (
            "The model, and why it is shared the way it is",
            "The stack. Then, for each parameter, the `per:` scope and the "
            "physical argument for it -- what is tied across states because it "
            "cannot have changed, what is free because it can. This is where a "
            "co-refinement is right or wrong, and it is almost never written "
            "down.",
        ),
        (
            "The sequence, and every branch that was abandoned",
            "The table above says what ran. This says why. Which fit answered "
            "a question, which was a control and what it controlled for, which "
            "was abandoned and on what evidence. A fit you abandoned still "
            "needs its sentence -- it is the first thing forgotten and often "
            "the most useful thing here.",
        ),
        (
            "The statistics, with the arithmetic shown",
            "chi2_red and the sqrt(chi2_red) inflation applied to every "
            "interval. Any BIC comparison with k*ln(n) written out, not just "
            "its verdict. Correlation coefficients for every pair above 0.9. "
            "Where a posterior is skewed, both the sigma and the tail "
            "fraction, and which one you are quoting.",
        ),
        (
            "Systematics",
            "Background, resolution, sample broadening, scale offsets between "
            "segments. What was tried, what it changed, and what it did not. "
            "Include the tests that changed nothing -- they are what make the "
            "result robust rather than lucky.",
        ),
        (
            "What the data do not support",
            "The readings someone would plausibly take from these numbers and "
            "should not. A parameter that is conditional rather than measured, "
            "a difference that does not survive inflated intervals, a trend "
            "that was an optimiser artifact.",
        ),
        (
            "What would change the answer",
            "The measurement, the reduction fix, or the contrast that would "
            "settle what is still open.",
        ),
        (
            "Provenance",
            "The fit ids this rests on, the data they consumed, and the "
            "command that reproduces them (`nrw pack <fit_id>`). Anything a "
            "reader would need to check this without asking you.",
        ),
    ),
)

SUPPORTING = Tier(
    key="si",
    filename_suffix="-si",
    heading="{sample}: supporting information",
    audience="A peer reading the paper this belongs to.",
    blurb=(
        "Written to drop into a Supporting Information section with minimal "
        "editing. Complete\nenough to be checked, short enough to be read. "
        "State conventions explicitly -- the\nreader does not work on this "
        "beamline."
    ),
    sequence="reportable",
    concepts=False,
    prompts=(
        (
            "Methods",
            "Instrument and geometry (say if it was back reflection, and why). "
            "Reduction. The resolution convention -- state that dQ is FWHM, "
            "because a reader assuming sigma will be wrong by 2.355. The "
            "fitting software and version, and the sampler settings.",
        ),
        (
            "Model",
            "The layer stack and what was co-refined against what. Which "
            "parameters were shared, which were free per state, and which were "
            "fixed and at what value.",
        ),
        (
            "Fitted parameters",
            "The parameter table: value, uncertainty, and the bound it was "
            "searched within. Say in the caption that intervals are inflated "
            "by sqrt(chi2_red) and give the factor. Mark parameters that are "
            "conditional rather than measured -- a reader cannot tell them "
            "apart otherwise. Generate this with a script in `reports/` rather "
            "than by hand; see `nrw report figure`.",
        ),
        (
            "Results",
            "What the fits establish, with the intervals as quoted in the "
            "table and the fit ids they come from.",
        ),
        (
            "Model comparison",
            "The alternatives tested and their BIC differences, as a table. "
            "State that the compared models were given equal fitting effort, "
            "because the comparison means nothing otherwise.",
        ),
        (
            "Caveats",
            "The limits of what one contrast, or this Q range, can separate. "
            "Written so a referee finds them here rather than deriving them.",
        ),
        (
            "Data and code availability",
            "Where the reduced data are, and how to rerun a fit "
            "(`nrw pack <fit_id>` produces a bundle that needs only refl1d, "
            "bumps and numpy).",
        ),
    ),
)

PLAIN = Tier(
    key="plain",
    filename_suffix="-plain",
    heading="{sample}: what we found, in plain language",
    audience=("A colleague who owns the science but does not fit reflectivity."),
    blurb=(
        "Two to three pages. The conclusion first, then only as much apparatus "
        "as is needed to\ntrust it. No symbol goes in without being said in "
        "words first. The concept sections\nbelow were chosen from what this "
        "analysis actually ran into -- delete any that turn\nout not to carry "
        "weight in the final story."
    ),
    sequence="none",
    concepts=True,
    prompts=(
        (
            "What we wanted to know",
            "The question, as the person who prepared the sample would ask it. "
            "No modelling vocabulary.",
        ),
        (
            "What we found",
            "The answer in one paragraph, with no symbols and no fit ids. If a "
            "number appears, say what it means in the same sentence.",
        ),
        (
            "How to read the picture",
            "Show the SLD profile, not the reflectivity curve -- the profile is "
            "a picture of the sample, R(Q) is a picture of the measurement. "
            "Say what the axes are, which side is the substrate, and what a "
            "step or a dip corresponds to physically.",
        ),
        (
            "How confident we are, and why",
            "What would have to be true for this to be wrong. Name the check "
            "that would have caught it, and say whether that check was run.",
        ),
        (
            "What we still do not know",
            "The honest limits, in the same plain register. What the "
            "measurement cannot distinguish, and what would distinguish it.",
        ),
        (
            "Where to look next",
            "Point at the supporting-information tier for the numbers, and the "
            "technical tier for the reasoning. Name them by filename.",
        ),
    ),
)

#: In document order: the record, the paper, the explanation.
ALL: tuple[Tier, ...] = (TECHNICAL, SUPPORTING, PLAIN)

#: Keyed by short name, for `--tier`.
BY_KEY: dict[str, Tier] = {tier.key: tier for tier in ALL}
