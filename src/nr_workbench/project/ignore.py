"""The managed ``.gitignore`` block, and why this one template is merged.

Every other template in this package is all-or-nothing. ``nrw init`` installs
it, upgrades it while it is still untouched, refuses to overwrite it once the
user has edited it (``Outcome.DRIFTED``), and leaves a file it has never seen
strictly alone (``Outcome.UNTRACKED``). That policy is what makes `init` safe
to re-run on top of a scientist's live working directory.

It was wrong for exactly one file. A project is very often created on GitHub
with a language ``.gitignore`` already in it, or inherits one from the folder
of scripts that preceded it. The nr-workbench ``.gitignore`` was then
classified ``UNTRACKED`` and silently skipped -- the run reported only "left
alone N pre-existing file(s) not installed by nrw", without naming them, and
no ``.nrw-new`` copy was written either.

What made that a bug rather than a debatable default: ``nrw init`` goes on to
write ``.nrw/bin/nrw`` and ``.claude/settings.local.json``, both of which
carry the absolute path of the ``nrw`` executable *on this machine*, and both
of whose own header comments say they are gitignored and must not be
committed. The only thing that made that sentence true was the ``.gitignore``
we had just declined to install. Provenance rule 3 -- no absolute paths in
committed files -- was enforced by a file `init` skipped whenever the user had
brought their own.

The observed consequence, on a project shared between two analysts: both files
were committed, and the second analyst's ``nrw init`` refreshed ``NRW_BIN`` for
them but deliberately left the committed ``PATH`` entry alone, so their
assistant sessions ran with another person's virtualenv first on ``PATH``. It
resolved rather than failing, because the two accounts were on a shared
filesystem, which is worse than an error.

So this file is merged instead. The nr-workbench rules live between two
markers; everything outside them is the user's and is never touched. Inside
them we own the content outright -- the markers say so -- and an upgrade
replaces the whole block rather than trying to reconcile it line by line.

The block goes at the *end* of the file on first insertion, and stays wherever
the user has moved it thereafter. Last match wins in ``.gitignore``, so
appending is what lets the negations at the bottom of the block (the provenance
skeleton, which must stay tracked) survive a broad rule someone wrote above.
"""

from __future__ import annotations

#: Start of the managed region. Deliberately short and prose-free: this string
#: is the matching key on every subsequent run, so anything that invites
#: reformatting -- a long line a formatter might wrap, a version number, a
#: timestamp -- would orphan the block and cause a duplicate to be appended.
#: The explanation lives *inside* the block, where it is content we maintain.
BEGIN_MARKER = "# >>> nr-workbench managed >>>"

#: End of the managed region. See :data:`BEGIN_MARKER`.
END_MARKER = "# <<< nr-workbench managed <<<"

#: Preamble written inside the block, immediately after the begin marker, so
#: that a person who opens ``.gitignore`` and finds rules they did not write
#: learns who owns them and how to opt out.
_PREAMBLE = """\
# Maintained by `nrw init`. Edits inside this block are replaced on the next
# run; add your own rules outside it. Delete the markers to take ownership.
"""


def wrap(body: str) -> str:
    """Wrap a block body in the managed markers.

    Args:
        body: The rule text to manage, typically the packaged ``.gitignore``
            template. A trailing newline is added if absent.

    Returns:
        The full managed block, marker to marker, ending in a newline.
    """
    if not body.endswith("\n"):
        body += "\n"
    return f"{BEGIN_MARKER}\n{_PREAMBLE}{body}{END_MARKER}\n"


def find_block(text: str) -> tuple[int, int] | None:
    """Locate the managed block's line span in an existing file.

    Args:
        text: Current file contents.

    Returns:
        ``(begin_index, end_index)`` as 0-based line indices, both inclusive,
        or None if there is no begin marker.

    Raises:
        DamagedBlockError: If a begin marker has no matching end marker after
            it.
    """
    lines = text.splitlines()
    begin = next(
        (i for i, line in enumerate(lines) if line.strip() == BEGIN_MARKER), None
    )
    if begin is None:
        return None
    end = next(
        (i for i in range(begin + 1, len(lines)) if lines[i].strip() == END_MARKER),
        None,
    )
    if end is None:
        raise DamagedBlockError(
            f"'{BEGIN_MARKER}' at line {begin + 1} has no matching '{END_MARKER}'."
        )
    return (begin, end)


class DamagedBlockError(Exception):
    """Raised when a managed block is opened but never closed.

    Almost always a half-resolved merge conflict or a truncating hand-edit.
    We cannot tell how far the block was meant to extend, and guessing would
    either delete rules the user added below it or leave ours duplicated, so
    the caller is expected to refuse and report rather than repair.
    """


def merge(existing: str, block: str) -> str:
    """Insert or refresh the managed block in an existing file.

    Args:
        existing: Current file contents.
        block: The full managed block, as from :func:`wrap`.

    Returns:
        The file contents with the block present and current. Content outside
        the block is preserved byte-for-byte, including its order.

    Raises:
        DamagedBlockError: If ``existing`` holds an unterminated block.
    """
    span = find_block(existing)
    if span is None:
        return _append(existing, block)

    begin, end = span
    lines = existing.splitlines(keepends=True)
    return "".join(lines[:begin]) + block + "".join(lines[end + 1 :])


def _append(existing: str, block: str) -> str:
    """Append the block to a file that does not yet have one.

    Separated by one blank line, and only one: `init` is idempotent, so a file
    that gains a blank line per run would show a diff forever.
    """
    if not existing:
        return block
    text = existing if existing.endswith("\n") else existing + "\n"
    if not text.endswith("\n\n"):
        text += "\n"
    return text + block


def force_merge(existing: str, block: str) -> str:
    """Merge, resolving a damaged block instead of refusing.

    What ``--force`` means for every other file in the scaffold is "overwrite
    it with the template, I have a backup". Taken literally here that would
    replace the user's whole ``.gitignore`` with the managed block and silently
    drop every rule they wrote -- recoverable from ``.nrw/backups/``, but only
    by someone who realises they need to look.

    So ``--force`` resolves the ambiguity the narrowest way that can be right:
    an unterminated block is taken to run from its begin marker to the end of
    the file, that region is replaced, and everything above the marker -- which
    is unambiguously the user's -- is kept.

    Args:
        existing: Current file contents.
        block: The full managed block, as from :func:`wrap`.

    Returns:
        The file contents with the block present and current.
    """
    try:
        return merge(existing, block)
    except DamagedBlockError:
        lines = existing.splitlines(keepends=True)
        begin = next(i for i, line in enumerate(lines) if line.strip() == BEGIN_MARKER)
        return _append("".join(lines[:begin]), block)


def is_current(existing: str, block: str) -> bool:
    """Report whether the file already holds this exact block.

    Args:
        existing: Current file contents.
        block: The full managed block, as from :func:`wrap`.

    Returns:
        True if merging would be a no-op.

    Raises:
        DamagedBlockError: If ``existing`` holds an unterminated block.
    """
    return merge(existing, block) == existing
