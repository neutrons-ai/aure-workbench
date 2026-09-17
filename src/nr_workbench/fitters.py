"""Which bumps fitters this tool will run, and why it is only three.

bumps ships a dozen optimisers. This tool accepts three of them, and the reason
is not that the others are bad -- it is that a *choice* of optimiser is a
decision an analysis almost never needs to make, and offering one invites the
failure this constant exists to prevent.

The failure, from a real unattended session: a spec edit made the model
physically impossible, chi-squared went 1.31 -> 16.6, and the session responded
by changing the fitter. de, then a larger population, then more steps, then
dream, then amoeba, then de again -- twelve fits, five of them differing from
their predecessor in nothing but the optimiser. Every one of them was a correct
answer to the wrong question. The model was broken; no optimiser was going to
find a good minimum, because there wasn't one.

A fitter menu makes that loop feel like progress. Three fitters, with the roles
named, keep it obvious that the next thing to change is usually the model:

    amoeba  explores, locally. Fast enough to keep pace with iterating on a
            model, and it carries no posterior, so nothing quotable comes out.
    de      explores, globally. Same role as amoeba -- no posterior, nothing
            quotable -- but it searches the whole box instead of walking
            downhill from one point. For when the STARTING POINT, not the
            model, is what amoeba is failing on.
    dream   samples. Slower, and the only thing here that can produce an
            uncertainty, a significance, or a comparison between two states.

`de` was added on evidence rather than preference, and the distinction it rests
on is the one the paragraph above is about. amoeba is a simplex: it walks
downhill from where it starts and stops at the first minimum it reaches, so on
a model with many parameters and a starting point far from the answer it
reports a local minimum with no sign that it is one. That is a different
failure from a broken model, and it has a different fix.

What made the case, on sample2 of the ionomer beamtime: a 23-parameter D2O
model started at chi-squared 1074 and amoeba stopped at 88.5, with the ionomer
and the hydrated layer's SLDs the wrong way round. de on the identical problem
-- same spec, same data, same bounds -- reached 17.7, five times better, three
seeds agreeing to within 0.06. No model change was involved, so no model change
could have been the fix.

**The trap this does not remove.** de reaching a better minimum than amoeba
says the search was the problem. de reaching the SAME bad minimum from every
seed says the model is, and no further optimiser will help -- that is the loop
in the second paragraph, and it is still the loop to stay out of. On that same
sample2, the air run's four-layer model bottomed out near chi-squared 62 under
a 240k-evaluation de search with every bound opened; the answer there was that
the stack was wrong, not that the fitter was.

So: are you still changing the model, or are you quoting a number? And if a
model will not fit, has the search actually been given a chance to find the
basin? Anything else -- lm, newton, pt -- is reachable by running bumps
directly on the generated script, which is a deliberate speed bump, and puts
the result outside the provenance record where an off-menu choice belongs
until someone argues it onto the menu.
"""

from __future__ import annotations

#: The only fitters `nrw fit run` and `fit.method` accept.
FITTERS = ("amoeba", "de", "dream")

#: What each is for, in the one sentence that decides between them.
FITTER_ROLES = {
    "amoeba": "explores a model locally; no posterior, so nothing quotable",
    "de": "explores a model globally, when the starting point is the problem",
    "dream": "samples a settled model; the only source of an uncertainty",
}

#: Fitters bumps offers that this tool refuses, mapped to what to use instead.
#: Named explicitly so the error can be specific rather than a bare list.
REFUSED = {
    "dream_de": "dream",
    "lm": "amoeba",
    "newton": "amoeba",
    "pt": "dream",
    "rl": "de",
    "ps": "de",
}


def refuse(method: str) -> str:
    """Explain why a fitter is not on the menu.

    Args:
        method: The fitter that was asked for.

    Returns:
        The message to raise, naming the replacement when there is an obvious
        one.
    """
    instead = REFUSED.get(method.strip().lower())
    menu = ", ".join(f"`{f}` ({FITTER_ROLES[f]})" for f in FITTERS)
    head = f"fitter {method!r} is not available: use one of {menu}."
    if instead:
        head += f" For what {method!r} was doing, use `{instead}`."
    return (
        head + " A short menu is deliberate -- changing the optimiser is not a "
        "way to fix a model, and a long menu makes it look like one. If `de` "
        "reaches the same minimum from several seeds, the thing to change is "
        "the model."
    )
