"""The analysis notebook: prose tied to the fits it is about.

Two homes, because there are two kinds of note and they have different
lifetimes:

``samples/<id>/results/<fit_id>/NOTES.md``
    What *this run* was for and what it showed. It lives inside the immutable
    result directory --- the one file there that may be edited --- so it cannot
    drift away from the numbers it describes, and it travels in a bundle.

``samples/<id>/reports/*.md``
    How the fits relate: the argument, the rejected models, the conclusion.
    This is sample-scoped rather than project-scoped because that is the level
    the thinking actually happens at, and it is the level a collaborator
    receives.

Both are ordinary markdown. That is deliberate. The evidence from a real
beamtime is a 609-line file of dated findings that a scientist wrote fluently
and a set of 25 structured note stubs that nobody filled in once --- prose was
never the problem. What was missing was that nothing could *find* the prose:
one entry opens "Read this before using that fit for anything" about a named
fit, in a file that ``nrw promote`` and ``nrw pack`` never open.

So the only structure imposed here is the link. A note is attached to a fit by
naming its id --- in optional frontmatter, or simply anywhere in the text,
which is how people already write. :func:`fits_mentioned` finds both, so an
existing findings file becomes a linked notebook by being moved, with no
edits.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: A fit id: ``20260807-163359Z-0103d9c7``, optionally ``-2`` for a collision.
FIT_ID_PATTERN = re.compile(r"\b\d{8}-\d{6}Z-[0-9a-f]{8}(?:-\d+)?\b")

#: Marks the end of a YAML frontmatter block.
_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)

NOTES_FILENAME = "NOTES.md"

#: Heading `nrw assess` writes under. Used to tell generated prose from a
#: person's, which matters when only one of the two can be shown.
ASSESSMENT_HEADING = "## Assessment"


def _first_prose(text: str) -> str:
    """The first line that is neither blank, a heading, nor a comment."""
    for line in re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL).splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith(("#", "-", "*", "|", ">")):
            return stripped[:120]
    return ""


#: What `nrw fit run` writes into a new result directory. Unlike the two HTML
#: comments it replaces, this asks questions. A blank page with a heading is
#: an invitation; a blank page with no heading is a file you close again.
NOTES_TEMPLATE = """\
# {fit_id}

<!-- Quoted, not stated: this is what the run was launched as, echoed back so
     the file identifies itself. A blockquote so it never wins the one-line
     summary over something you actually wrote. -->
> {description}

<!-- The only file in this directory you may edit. Everything else records
     what ran. Delete any heading you have nothing to say under. -->

## Why this run

<!-- What were you testing? What changed since the last one, and what did you
     expect it to do? -->

## What it showed

<!-- The result in words. Numbers are already in the record; what do they mean? -->

## Caveats

<!-- What would you warn someone against concluding from this fit? A parameter
     that is conditional rather than measured, a bound it sits on, a
     degeneracy you broke by choosing rather than by measuring. -->
"""


def fits_mentioned(text: str) -> list[str]:
    """Return every fit id referenced by a note, in order of first appearance.

    Looks in the frontmatter ``fits:`` list *and* in the body, because people
    cite fits in prose and a system that only read frontmatter would find
    nothing in anything written before it existed.

    Args:
        text: The note's full source.

    Returns:
        Fit ids, de-duplicated, first occurrence first.
    """
    seen: dict[str, None] = {}
    for match in FIT_ID_PATTERN.finditer(text):
        seen.setdefault(match.group(0), None)
    return list(seen)


def frontmatter(text: str) -> dict[str, Any]:
    """Parse a note's YAML frontmatter, or return an empty mapping.

    Args:
        text: The note's full source.

    Returns:
        The parsed mapping. Malformed frontmatter reads as absent rather than
        raising --- a note is prose first, and a YAML slip must not make it
        unreadable.
    """
    match = _FRONTMATTER.match(text)
    if not match:
        return {}
    try:
        import yaml

        value = yaml.safe_load(match.group(1))
    except Exception:  # noqa: BLE001 - a bad header must not lose the prose
        return {}
    return value if isinstance(value, dict) else {}


def body(text: str) -> str:
    """Return the note without its frontmatter."""
    match = _FRONTMATTER.match(text)
    return text[match.end() :] if match else text


def is_blank(text: str | None) -> bool:
    """Whether a note says nothing.

    A template nobody filled in is not a note, and treating it as one is how
    a UI ends up displaying HTML comments to every visitor. Headings, comments
    and whitespace are all scaffolding; what counts is a line of prose.

    Args:
        text: The note's source, or ``None``.

    Returns:
        True if there is no prose under the scaffolding.
    """
    if not text:
        return True
    without_comments = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    for line in without_comments.splitlines():
        stripped = line.strip()
        # Headings, comments and the quoted run note are all scaffolding the
        # template put there. `>` in particular: the template echoes the
        # `--note` back as a blockquote, and counting that as content would
        # make every untouched file look written-in.
        if not stripped or stripped.startswith(("#", ">")) or stripped == "---":
            continue
        return False
    return True


@dataclass(frozen=True)
class Note:
    """One note on disk.

    Attributes:
        path: Where it lives, relative to the project root.
        title: Its first heading, or the filename.
        text: The full source.
        fits: Fit ids it mentions.
        scope: ``"fit"`` for a result's NOTES.md, ``"sample"`` for a report.
        sample: The sample it belongs to.
        fit_id: For a fit note, the fit it is attached to.
    """

    path: str
    title: str
    text: str
    fits: list[str] = field(default_factory=list)
    scope: str = "sample"
    sample: str | None = None
    fit_id: str | None = None

    @property
    def blank(self) -> bool:
        """Whether this note is an unfilled template."""
        return is_blank(self.text)

    @property
    def summary(self) -> str:
        """The first line of prose, for a listing.

        What a person wrote wins over the generated assessment, which would
        otherwise always be first simply because `nrw assess` tends to run
        before anyone types anything. A listing that shows a chi-squared back
        to you is not telling you what you thought.
        """
        text = body(self.text)
        human, _, generated = text.partition(ASSESSMENT_HEADING)
        return _first_prose(human) or _first_prose(generated)

    def as_dict(self) -> dict[str, Any]:
        """Return the JSON form, without the full text."""
        return {
            "path": self.path,
            "title": self.title,
            "scope": self.scope,
            "sample": self.sample,
            "fit_id": self.fit_id,
            "fits": self.fits,
            "blank": self.blank,
            "summary": self.summary,
        }


def read_note(
    path: Path,
    root: Path,
    *,
    scope: str,
    sample: str | None = None,
    fit_id: str | None = None,
) -> Note | None:
    """Read one note from disk.

    Args:
        path: The file.
        root: Project root, for the relative path.
        scope: ``"fit"`` or ``"sample"``.
        sample: The owning sample.
        fit_id: The fit a fit-note is attached to.

    Returns:
        The note, or ``None`` if it cannot be read.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    meta = frontmatter(text)
    declared = meta.get("fits")
    mentioned = fits_mentioned(text)
    if isinstance(declared, str):
        declared = [declared]
    if isinstance(declared, list):
        for value in declared:
            if isinstance(value, str) and value not in mentioned:
                mentioned.append(value)

    # A fit note is about its own fit whether or not it says so.
    if fit_id and fit_id not in mentioned:
        mentioned.insert(0, fit_id)

    return Note(
        path=_relative(path, root),
        title=_title(text, path, meta),
        text=text,
        fits=mentioned,
        scope=scope,
        sample=sample,
        fit_id=fit_id,
    )


def _relative(path: Path, root: Path) -> str:
    """Path relative to the root where possible, else absolute."""
    try:
        return path.resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _title(text: str, path: Path, meta: dict[str, Any]) -> str:
    """A note's title: frontmatter, else first heading, else filename."""
    declared = meta.get("title")
    if isinstance(declared, str) and declared.strip():
        return declared.strip()
    for line in body(text).splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return path.stem


def sample_notes(root: Path, sample: str) -> list[Note]:
    """Every report written about one sample, newest filename last.

    Args:
        root: Project root.
        sample: The sample id.

    Returns:
        Notes found in ``samples/<sample>/reports/``, sorted by path.
    """
    directory = Path(root) / "samples" / sample / "reports"
    if not directory.is_dir():
        return []
    found = []
    for path in sorted(directory.rglob("*.md")):
        note = read_note(path, root, scope="sample", sample=sample)
        if note is not None:
            found.append(note)
    return found


def fit_note(root: Path, fit_dir: Path, fit_id: str, sample: str | None) -> Note | None:
    """Read the NOTES.md of one fit.

    Args:
        root: Project root.
        fit_dir: The result directory.
        fit_id: The fit id.
        sample: The owning sample.

    Returns:
        The note, or ``None`` if there is no file.
    """
    path = Path(fit_dir) / NOTES_FILENAME
    if not path.is_file():
        return None
    return read_note(path, root, scope="fit", sample=sample, fit_id=fit_id)


def notes_about(notes: list[Note], fit_id: str) -> list[Note]:
    """Filter to the notes that mention a fit.

    Args:
        notes: Notes to search.
        fit_id: The fit id.

    Returns:
        Those mentioning it, blank ones excluded --- an unfilled template
        mentions its own fit and would otherwise appear as evidence of
        thinking that did not happen.
    """
    return [n for n in notes if fit_id in n.fits and not n.blank]
