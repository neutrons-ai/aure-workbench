"""Marking a spec as abandoned, without disturbing what it already produced.

A sample keeps every spec it ever fitted, because the fits are part of the
record and a result whose model has vanished is not reproducible. The cost is
that an abandoned spec looks exactly like a live one. In the reference
experiment the first two specs had the layer stack inverted --- the error that
cost the most --- and both are still on disk, still named after the task, with
nothing in the file to say so. The evidence lives in a `NOTES.md` three
directories away, which is not where anyone opening the spec is looking.

So deprecation is written *into the spec*, at the top, where it cannot be
missed.

**The identity hash has to ignore it.** Every generated script records the
sha256 of the spec it came from, and `nrw check` compares that against the file
to detect a script older than its model. A single added comment line trips it
--- measured, not assumed --- which would mean labelling an abandoned spec
reports a stale script for every fit it ever produced, on exactly the specs
whose history is being preserved. That turns the one command you are supposed to
run before calling a result final into a source of false alarms.

The invariant that resolves it: **a spec's identity is the model it describes,
and deprecation is a statement about the spec's status rather than its model.**
So the banner is fenced, and every hash of a spec is taken with the fence
removed. Deprecating and un-deprecating are therefore both hash-neutral, and no
existing result changes state because of a label.
"""

from __future__ import annotations

import hashlib
import re
import textwrap
from datetime import UTC, datetime
from pathlib import Path

#: Opening fence. Unambiguous and machine-findable, and a YAML comment so the
#: spec still parses with the banner in place.
BEGIN = "# nrw:deprecated"

#: Closing fence.
END = "# nrw:/deprecated"

#: Matches the whole banner, including the blank line that follows it, so
#: stripping restores the original bytes exactly.
_BANNER_RE = re.compile(
    rf"^{re.escape(BEGIN)}$.*?^{re.escape(END)}$\n?\n?",
    re.MULTILINE | re.DOTALL,
)

#: Width the reason is wrapped to, matching the comment style of a spec header.
_WRAP = 74


def strip(text: str) -> str:
    """Return a spec's text with any deprecation banner removed.

    Args:
        text: The spec file's contents.

    Returns:
        The text as it was before deprecation, byte-for-byte when the banner was
        written by :func:`banner`.
    """
    return _BANNER_RE.sub("", text, count=1)


def is_deprecated(text: str) -> bool:
    """Whether a spec carries a deprecation banner."""
    return _BANNER_RE.search(text) is not None


def reason_of(text: str) -> str:
    """The recorded reason a spec was deprecated, or an empty string.

    Args:
        text: The spec file's contents.

    Returns:
        The reason as one line, reassembled from the wrapped comment.
    """
    match = _BANNER_RE.search(text)
    if match is None:
        return ""
    collected: list[str] = []
    for line in match.group(0).splitlines():
        body = line.lstrip("#").strip()
        if body.startswith("reason:"):
            collected.append(body[len("reason:") :].strip())
        elif collected and body and not body.endswith(":") and ":" not in body[:12]:
            collected.append(body)
        elif collected:
            break
    return " ".join(part for part in collected if part).strip()


def banner(reason: str, *, when: str | None = None) -> str:
    """Render the banner to prepend to a deprecated spec.

    Args:
        reason: Why the spec was abandoned, in the author's words.
        when: ISO date to record; today's UTC date if omitted.

    Returns:
        The banner text, ending in a blank line.
    """
    stamp = when or datetime.now(UTC).strftime("%Y-%m-%d")
    flattened = " ".join(str(reason).split())
    wrapped = textwrap.wrap(flattened, width=_WRAP - len("#   reason: ")) or [""]
    lines = [
        BEGIN,
        f"#   since:  {stamp}",
        f"#   reason: {wrapped[0]}",
    ]
    lines += [f"#           {line}" for line in wrapped[1:]]
    lines += [
        "#",
        "# This spec is kept because the fits it produced are part of the record.",
        "# Do not fit it and do not copy from it: `nrw model generate` refuses while",
        "# this banner is present. `nrw model deprecate <spec> --undo` removes it.",
        END,
        "",
        "",
    ]
    return "\n".join(lines)


def identity_hash(path: Path) -> str:
    """The sha256 of a spec, ignoring any deprecation banner.

    This is *the* spec digest: what a generated script records and what
    ``nrw check`` compares against. Taken over the stripped text so that
    labelling a spec never makes a script look older than its model.

    Args:
        path: The spec file.

    Returns:
        Hex digest.
    """
    text = Path(path).read_text(encoding="utf-8")
    return hashlib.sha256(strip(text).encode("utf-8")).hexdigest()
