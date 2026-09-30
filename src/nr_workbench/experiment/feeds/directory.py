"""The feed that notices a run when its reduced files appear in the folder.

Built on the data source's own listing rather than listing the folder again:
the folder is on NFS and is polled continuously while someone is watching, so
two listings per poll would be twice the load for the same answer.
"""

from __future__ import annotations

from typing import Any

from nr_workbench.experiment.inventory import Announcement, FeedUpdate, Inventory


class DirectoryFeed:
    """Announce every run the data source can see."""

    kind = "directory"

    def describe(self) -> dict[str, Any]:
        """What the page shows about this feed."""
        return {
            "kind": self.kind,
            "detail": "new runs are noticed when their reduced files appear",
        }

    def poll(self, inventory: Inventory) -> FeedUpdate:
        """The runs in this poll's listing, with what their headers said."""
        return FeedUpdate(
            announcements=tuple(
                Announcement(run=key.run, title=run.title, start_time=run.start_time)
                for key, run in sorted(inventory.runs.items())
            )
        )
