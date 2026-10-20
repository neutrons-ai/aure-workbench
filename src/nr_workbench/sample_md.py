"""The parts of sample.md's shape that more than one reader depends on.

``sample.md`` is read by about twenty modules. Four of them read its
measurement table -- ISAAC's conditions (:mod:`nr_workbench.conditions`), the
reconciliation against file headers (:mod:`nr_workbench.reconcile`), adoption
into the experiment catalog, and the catalog's own guard against a second
table -- and each used to decide for itself which column is the run and which
the condition. They disagreed: a ``| Run | Type | Conditions |`` table gave
ISAAC a condition and gave adoption an empty one. What they must agree on
lives here.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from dataclasses import dataclass

#: A markdown table row: ``| 218393 | full Q | -0.5 mA/cm2 |``.
TABLE_ROW_RE = re.compile(r"^\s*\|(.+)\|\s*$")

#: Header cells that name the run column, lowercased.
RUN_HEADERS = ("run", "run number")

#: Header cells that name the condition column, lowercased.
CONDITION_HEADERS = ("condition", "conditions")

#: Header cells that name the measurement-type column, lowercased.
TYPE_HEADERS = ("type",)

#: How one measurement's notes open under *Measurement conditions*, as the
#: catalog writes them: the run named first, so no reader has to guess which
#: measurement a note is about. The note's later lines are indented under it.
RUN_NOTE_PREFIX = "- Run {run}: "
RUN_NOTE_INDENT = "  "

#: A line that opens a run's notes, read back: ``- Run 218386: realigned``.
RUN_NOTE_RE = re.compile(r"^- Run (\d+): ?(.*)$")

#: The sections the experiment's own context is written into, at their head,
#: and the field of the experiment's record each one carries.
EXPERIMENT_SECTIONS = {
    "Description": "description",
    "Details": "details",
    "Measurement conditions": "measurement_conditions",
}

#: The machine token of the experiment's own context. The catalog writes the
#: same block into every sample.md it manages, between two comments: one that
#: opens with this token and a digest of the text, and :data:`EXPERIMENT_CLOSE`.
#: Readers that strip comments read the text and not them, and adoption tells
#: the text from the sample's own by the token alone -- so the sentence the
#: open marker says to a person can be reworded without unreading old files.
EXPERIMENT_TOKEN = "nrw:experiment"
EXPERIMENT_CLOSE = f"<!-- /{EXPERIMENT_TOKEN} -->"

#: What the open marker says to a person reading the file.
_EXPERIMENT_NOTE = (
    "the experiment's context, written into every sample's sample.md. "
    "Edit it on the Experiment page."
)

#: The open marker, read back: the token, then the digest.
_OPEN_RE = re.compile(
    r"<!--\s*" + re.escape(EXPERIMENT_TOKEN) + r"(?![\w:/-])\s*([0-9a-f]{12})?"
)

#: The close marker's text, between ``<!--`` and ``-->``.
_CLOSE_TEXT = f"/{EXPERIMENT_TOKEN}"


def experiment_digest(text: str) -> str:
    """A short digest of the experiment's text, written into its open marker.

    It is how adoption tells a block nobody touched -- merely out of date
    once the experiment's context changed -- from one edited by hand.
    """
    normalized = text.replace("\r\n", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]


def experiment_block(text: str) -> str:
    """The experiment's text as a sample's section carries it; ``""`` for none.

    Nothing at all is written for an empty field, so a catalog whose
    experiment was never described renders every sample.md exactly as before.
    """
    if not text:
        return ""
    digest = experiment_digest(text)
    opening = f"<!-- {EXPERIMENT_TOKEN} {digest}: {_EXPERIMENT_NOTE} -->"
    return f"{opening}\n{text}\n{EXPERIMENT_CLOSE}"


@dataclass(frozen=True)
class ExperimentBlock:
    """One block of the experiment's context, found in a text.

    Attributes:
        start: Where its open marker starts.
        end: Just after its close marker.
        text: What is between the markers, without the line breaks the
            catalog puts around it.
        edited: Whether the text is not what the open marker's digest says
            was written -- edited by hand, or written with no digest.
    """

    start: int
    end: int
    text: str
    edited: bool


def find_experiment(text: str) -> tuple[list[ExperimentBlock], bool]:
    """Every whole block of the experiment's context, and whether any is damaged.

    A block is an open marker and the close marker as the next two comments,
    exactly as readers that strip comments meet them: what lies between holds
    no comment, so a block can never run on past another -- every section's
    own guidance is one. An open marker whose next comment is not its close,
    a close with no open, or an open never closed is damage, said rather than
    guessed around. One pass, in the length of *text*.

    Returns:
        The whole blocks in order, and whether there was damage beside them.
    """
    blocks: list[ExperimentBlock] = []
    damaged = False
    opened: tuple[int, int, str | None] | None = None
    for start, end in _comments(text):
        comment = text[start:end]
        opening = _OPEN_RE.match(comment)
        closing = comment.endswith("-->") and comment[4:-3].strip() == _CLOSE_TEXT
        if opened is not None:
            open_start, open_end, digest = opened
            opened = None
            if closing:
                inner = _between(text[open_end:start])
                blocks.append(
                    ExperimentBlock(
                        start=open_start,
                        end=end,
                        text=inner,
                        edited=digest != experiment_digest(inner),
                    )
                )
                continue
            damaged = True
        if opening:
            opened = (start, end, opening.group(1))
        elif closing:
            damaged = True
    return blocks, damaged or opened is not None


def without_experiment(text: str) -> str:
    """*text* with every whole block of the experiment's context taken out."""
    kept: list[str] = []
    position = 0
    for block in find_experiment(text)[0]:
        kept.append(text[position : block.start])
        position = block.end
    kept.append(text[position:])
    return "".join(kept)


def _comments(text: str) -> Iterator[tuple[int, int]]:
    """Each HTML comment as a reader strips one: from ``<!--`` to the next ``-->``.

    An unclosed comment runs to the end of the text.
    """
    position = 0
    while True:
        start = text.find("<!--", position)
        if start < 0:
            return
        close = text.find("-->", start + 4)
        if close < 0:
            yield (start, len(text))
            return
        position = close + 3
        yield (start, position)


def _between(text: str) -> str:
    """The text between two markers, without the line break after and before each."""
    text = text.replace("\r\n", "\n")
    if text.startswith("\n"):
        text = text[1:]
    if text.endswith("\n"):
        text = text[:-1]
    return text


def table_cells(line: str) -> list[str] | None:
    """The stripped cells of a table row, or ``None`` if *line* is not one."""
    match = TABLE_ROW_RE.match(line)
    if match is None:
        return None
    return [cell.strip() for cell in match.group(1).split("|")]


def column(header: list[str], names: tuple[str, ...]) -> int | None:
    """Index of the first header cell among *names*, or ``None``.

    Args:
        header: Lowercased header cells.
        names: Acceptable names, most preferred first.
    """
    for name in names:
        if name in header:
            return header.index(name)
    return None
