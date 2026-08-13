"""Which bumps fitters this tool will run, and why it is only two.

bumps ships a dozen optimisers. This tool accepts two of them, and the reason is
not that the others are bad -- it is that a *choice* of optimiser is a decision
an analysis almost never needs to make, and offering one invites the failure
this constant exists to prevent.

The failure, from a real unattended session: a spec edit made the model
physically impossible, chi-squared went 1.31 -> 16.6, and the session responded
by changing the fitter. de, then a larger population, then more steps, then
dream, then amoeba, then de again -- twelve fits, five of them differing from
their predecessor in nothing but the optimiser. Every one of them was a correct
answer to the wrong question. The model was broken; no optimiser was going to
find a good minimum, because there wasn't one.

A fitter menu makes that loop feel like progress. Two fitters, with the roles
named, make it obvious that the next thing to change is the model:

    amoeba  explores. Fast enough to keep pace with iterating on a model, and
            it carries no posterior, so nothing quotable can come out of it.
    dream   samples. Slower, and the only thing here that can produce an
            uncertainty, a significance, or a comparison between two states.

That is the whole decision: are you still changing the model, or are you
quoting a number? Anything else -- lm, de, newton, pt -- is reachable by
running bumps directly on the generated script, which is a deliberate speed
bump, and puts the result outside the provenance record where an off-menu
choice belongs until someone argues it onto the menu.
"""

from __future__ import annotations

#: The only fitters `nrw fit run` and `fit.method` accept.
FITTERS = ("amoeba", "dream")

#: What each is for, in the one sentence that decides between them.
FITTER_ROLES = {
    "amoeba": "explores a model; no posterior, so nothing quotable",
    "dream": "samples a settled model; the only source of an uncertainty",
}

#: Fitters bumps offers that this tool refuses, mapped to what to use instead.
#: Named explicitly so the error can be specific rather than a bare list.
REFUSED = {
    "de": "amoeba",
    "dream_de": "dream",
    "lm": "amoeba",
    "newton": "amoeba",
    "pt": "dream",
    "rl": "amoeba",
    "ps": "amoeba",
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
    menu = " or ".join(f"`{f}` ({FITTER_ROLES[f]})" for f in FITTERS)
    head = f"fitter {method!r} is not available: use {menu}."
    if instead:
        head += f" For what {method!r} was doing, use `{instead}`."
    return (
        head + " Two fitters is deliberate -- changing the optimiser is not a "
        "way to fix a model, and a menu makes it look like one. If a fit will "
        "not converge, the thing to change is the model."
    )
