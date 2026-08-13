"""Who is reading this project, recorded in ``nrw.toml``.

Every session that has ever worked in one of these projects has guessed at
this, and written for whoever it imagined. The guess is usually "a competent
generalist", which is nobody: it over-explains reflectometry to a beamline
scientist and under-explains statistics to everyone.

Four axes, deliberately independent. The reference user is the reason: a
reflectometry expert who wanted the Bayesian model comparison spelled out in
full. A single novice/expert dial cannot express that, and a project that only
had one would have got both halves wrong.

The values are advice to an assistant, not a permission system. Nothing here
refuses anything; it changes how much is explained, and in which order.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

#: Ordered axes, their allowed values, and what each axis means. The order is
#: the order they are asked about and printed in.
AXES: dict[str, tuple[tuple[str, ...], str]] = {
    "reflectometry": (
        ("newcomer", "practitioner", "expert"),
        "Reflectometry itself: SLD profiles, fringes, roughness, resolution.",
    ),
    "statistics": (
        ("newcomer", "practitioner", "expert"),
        "Fitting and inference: posteriors, correlation, model comparison.",
    ),
    "domain": (
        ("newcomer", "practitioner", "expert"),
        "The science the sample is for: the electrochemistry, the polymer, the cell.",
    ),
    "role": (
        ("drives", "collaborates", "delegates"),
        "How this person wants to work with an assistant.",
    ),
}

#: What a project gets when nobody has said. `practitioner` because it is the
#: least-wrong guess in both directions, and `collaborates` because proposing
#: and then checking is recoverable whichever way the truth lies.
DEFAULTS: dict[str, str] = {
    "reflectometry": "practitioner",
    "statistics": "practitioner",
    "domain": "practitioner",
    "role": "collaborates",
}

_SECTION = "[audience]"

#: The block appended to an ``nrw.toml`` that predates this feature.
TEMPLATE = """
# Who reads what comes out of this project. These change how much an assistant
# explains and in what order -- they refuse nothing. Set them with
# `nrw audience --ask`, or edit here.
#
#   reflectometry / statistics / domain : newcomer | practitioner | expert
#   role                                : drives | collaborates | delegates
#
# The three knowledge axes are independent on purpose: a reflectometry expert
# who wants the statistics spelled out is a real and common reader, and a
# single novice/expert dial gets that reader wrong twice.
[audience]
reflectometry = "practitioner"
statistics = "practitioner"
domain = "practitioner"
role = "collaborates"
notes = ""
"""


@dataclass(frozen=True)
class Audience:
    """Who the analysis is being written for.

    Attributes:
        reflectometry: Fluency with reflectometry itself.
        statistics: Fluency with fitting and inference.
        domain: Fluency with the science the sample is for.
        role: How this person wants to work with an assistant.
        notes: Anything the axes cannot express.
        declared: False when the block is absent or still entirely at its
            defaults -- i.e. nobody has actually said.
    """

    reflectometry: str = DEFAULTS["reflectometry"]
    statistics: str = DEFAULTS["statistics"]
    domain: str = DEFAULTS["domain"]
    role: str = DEFAULTS["role"]
    notes: str = ""
    declared: bool = False

    def as_dict(self) -> dict[str, str]:
        """The four axes plus notes, in display order."""
        return {
            "reflectometry": self.reflectometry,
            "statistics": self.statistics,
            "domain": self.domain,
            "role": self.role,
            "notes": self.notes,
        }


def load(root: Path) -> Audience:
    """Read the ``[audience]`` block, falling back to defaults.

    Args:
        root: Project root.

    Returns:
        The declared audience, or the defaults with ``declared=False``.
    """
    from nr_workbench.project.config import ProjectConfigError, load_config

    try:
        config = load_config(Path(root))
    except ProjectConfigError:
        return Audience()

    block = config.raw.get("audience")
    if not isinstance(block, dict) or not block:
        return Audience()

    values = {}
    for axis, (allowed, _) in AXES.items():
        raw = str(block.get(axis, DEFAULTS[axis])).strip().lower()
        values[axis] = raw if raw in allowed else DEFAULTS[axis]
    notes = str(block.get("notes", "") or "").strip()

    # "Present but untouched" is not a declaration. A session told that the
    # audience is `practitioner` across the board should know whether a person
    # chose that or a template did, because the two justify different amounts
    # of asking.
    declared = bool(notes) or any(values[a] != DEFAULTS[a] for a in AXES)

    return Audience(**values, notes=notes, declared=declared)


def validate(axis: str, value: str) -> str:
    """Check one axis assignment.

    Args:
        axis: Axis name.
        value: Proposed value.

    Returns:
        The normalised value.

    Raises:
        ValueError: If the axis or the value is not one of the allowed ones.
    """
    if axis not in AXES:
        raise ValueError(f"No audience axis {axis!r}. Known: {', '.join(AXES)}.")
    allowed, _ = AXES[axis]
    normalised = value.strip().lower()
    if normalised not in allowed:
        raise ValueError(f"{axis} must be one of {', '.join(allowed)}, not {value!r}.")
    return normalised


def write(root: Path, audience: Audience) -> None:
    """Update the ``[audience]`` block in ``nrw.toml`` in place.

    A line-oriented edit rather than a re-serialisation: ``nrw.toml`` is mostly
    comments explaining why the instrument conventions are what they are, and a
    TOML round-trip through the standard library would silently delete all of
    them. Only the assignment lines inside ``[audience]`` are touched.

    Args:
        root: Project root.
        audience: Values to write.

    Raises:
        OSError: If ``nrw.toml`` cannot be read or written.
    """
    path = Path(root) / "nrw.toml"
    text = path.read_text(encoding="utf-8")
    values = audience.as_dict()

    if _SECTION not in text:
        path.write_text(text.rstrip("\n") + "\n" + TEMPLATE, encoding="utf-8")
        text = path.read_text(encoding="utf-8")

    lines = text.splitlines(keepends=True)
    out: list[str] = []
    inside = False
    seen: set[str] = set()

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("["):
            if inside and stripped != _SECTION:
                # Leaving the block: emit anything the file did not already have.
                out.extend(
                    f'{key} = "{_escape(values[key])}"\n'
                    for key in values
                    if key not in seen
                )
                inside = False
            elif stripped == _SECTION:
                inside = True
            out.append(line)
            continue

        if inside:
            match = re.match(r"^(\s*)([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
            if match and match.group(2) in values:
                key = match.group(2)
                seen.add(key)
                out.append(f'{match.group(1)}{key} = "{_escape(values[key])}"\n')
                continue
        out.append(line)

    if inside:
        out.extend(
            f'{key} = "{_escape(values[key])}"\n' for key in values if key not in seen
        )

    path.write_text("".join(out), encoding="utf-8")


def _escape(value: str) -> str:
    """Escape a value for a TOML basic string."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def guidance(audience: Audience) -> list[str]:
    """How to work with this person, as instructions an assistant can follow.

    Stated here rather than improvised per session, so that the behaviour is
    reviewable and changing it is an edit to one function.

    Args:
        audience: The declared audience.

    Returns:
        Instruction lines, most load-bearing first.
    """
    lines: list[str] = []

    role = {
        "drives": (
            "They drive. Propose and act; do not ask permission for a fit you "
            "can justify -- they will redirect you, and that is cheaper for "
            "them than a question. Report in one or two sentences after each "
            "fit."
        ),
        "collaborates": (
            "They collaborate. Give a short itemized plan before a sequence of "
            "fits, then work through it, saying which step you are on. Ask "
            "when a choice would change the conclusion, not when it would "
            "change the wall time."
        ),
        "delegates": (
            "They delegate. Give the itemized plan up front and confirm it "
            "before starting. Confirm before each new fit sequence. Every "
            "number you report gets a plain-language gloss in the same "
            "sentence."
        ),
    }[audience.role]
    lines.append(role)

    if audience.statistics == "newcomer":
        lines.append(
            "Statistics: never quote a sigma without saying what it means for "
            "this claim. Where a sigma and a posterior tail fraction disagree, "
            "quote the tail and say why it is the honest one."
        )
    elif audience.statistics == "expert":
        lines.append(
            "Statistics: show the arithmetic. Write out the BIC terms, the "
            "sqrt(chi2_red) inflation factor and the correlation coefficients "
            "rather than their conclusions."
        )

    if audience.reflectometry == "newcomer":
        lines.append(
            "Reflectometry: name the invariant before the slab parameter -- "
            "total metal thickness before a Cu/CuOx split that sits on a "
            "ridge. Show the SLD profile before the reflectivity curve; the "
            "profile is the picture of the sample, R(Q) is the measurement."
        )
    elif audience.reflectometry == "expert":
        lines.append(
            "Reflectometry: the vocabulary is shared. Spend the words on what "
            "is specific to this sample, not on what a fringe is."
        )

    if audience.domain == "expert":
        lines.append(
            "The science is theirs. State what the data constrain and hand "
            "over the interpretation; do not narrate a mechanism the "
            "reflectivity cannot distinguish."
        )
    elif audience.domain == "newcomer":
        lines.append(
            "Connect each structural number back to the chemistry it implies, "
            "in one clause, every time you quote it."
        )

    if audience.notes:
        lines.append(f"They also said: {audience.notes}")

    if not audience.declared:
        lines.append(
            "Nobody has actually set this -- these are defaults. If how much "
            "to explain starts to matter, ask, then record it with "
            "`nrw audience --ask`."
        )
    return lines
