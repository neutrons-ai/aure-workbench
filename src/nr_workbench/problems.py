"""A problem, stated plainly enough to act on.

Shared by the web layer and the experiment catalog. It used to live in
:mod:`nr_workbench.web.project`, which is fine while only the web layer reports
problems -- but the experiment package reports them too, and importing them
from ``web`` would make the data layer depend on the view layer.
"""

from __future__ import annotations

from dataclasses import dataclass


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
