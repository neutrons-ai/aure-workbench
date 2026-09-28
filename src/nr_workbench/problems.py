"""A problem, stated plainly enough to act on.

Shared by the web layer and the experiment catalog. It used to live in
:mod:`nr_workbench.web.project`, which is fine while only the web layer reports
problems -- but the experiment package reports them too, and importing them
from ``web`` would make the data layer depend on the view layer.
"""

from __future__ import annotations

from dataclasses import dataclass


def one_line(name: str) -> str:
    """A name from a folder anyone can write, safe to print or to put in a comment.

    Itself when every character is printable; otherwise its ``ascii()`` form,
    quoted and escaped. A directory named with a line break -- ``\\n``, ``\\r``,
    U+0085, U+2028 or U+2029 all end a YAML comment -- would otherwise put
    whatever follows it into a model spec as keys of its own, and an escape
    sequence can rewrite a terminal.
    """
    return name if name.isprintable() else ascii(name)


@dataclass(frozen=True)
class Problem:
    """Something that could not be done, stated plainly enough to act on.

    Attributes:
        scope: What was being read, e.g. ``series:218389``.
        message: What went wrong, in terms the reader can fix.
    """

    scope: str
    message: str

    def as_dict(self) -> dict[str, str]:
        """Return the JSON form."""
        return {"scope": self.scope, "message": self.message}
