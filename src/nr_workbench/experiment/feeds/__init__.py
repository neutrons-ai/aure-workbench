"""How we learn that a run exists.

Deliberately separate from where the data comes from. The folder watcher learns
of a run when its reduced files appear, but other feeds know sooner and know
different things: the SNS web monitor (monitor.sns.gov) reports each run as it
is acquired, before any reduction exists, and Tiled can announce runs whatever
their data source. A run a feed announces with no files yet is *awaiting
reduction* -- a state the folder alone can never show -- and a later run the
feed announces is evidence the one before it has finished.

A feed returns a snapshot of every run it currently knows, not a delta:
:class:`~nr_workbench.experiment.live.LiveInventory` works out what changed,
so no feed has to remember what it said last time.
"""

from __future__ import annotations

from typing import Any, Protocol

from nr_workbench.experiment.inventory import FeedUpdate, Inventory


class FeedUnavailableError(Exception):
    """The configured feed cannot be used. The message says why and what to do."""


class RunFeed(Protocol):
    """A source of "run N exists" statements.

    Attributes:
        kind: The name used in ``[experiment.feed] kind``.
    """

    kind: str

    def describe(self) -> dict[str, Any]:
        """What the page shows about the feed."""
        ...

    def poll(self, inventory: Inventory) -> FeedUpdate:
        """Every run the feed knows about now.

        Args:
            inventory: What the data source listed on this same poll. The
                folder feed is built from it; a remote feed may ignore it.

        Returns:
            The feed's snapshot. Never raises for an unreachable feed; the
            problem is returned instead.
        """
        ...


def open_feed(config: Any) -> RunFeed:
    """Build the feed ``[experiment.feed]`` describes.

    Args:
        config: A :class:`~nr_workbench.experiment.config.FeedConfig`.

    Raises:
        FeedUnavailableError: For a kind that is planned but not built, or not
            known at all.
    """
    from nr_workbench.experiment.config import FEED_KINDS, PLANNED_FEED_KINDS

    if config.kind == "directory":
        from nr_workbench.experiment.feeds.directory import DirectoryFeed

        return DirectoryFeed()
    if config.kind in PLANNED_FEED_KINDS:
        raise FeedUnavailableError(
            f"[experiment.feed] kind = {config.kind!r} is planned but not "
            "implemented yet (the web monitor also needs ORNL credentials). "
            "docs/experiment-sources.md describes how it will plug in; until "
            'then use kind = "directory".'
        )
    raise FeedUnavailableError(
        f"[experiment.feed] kind = {config.kind!r} is not known. "
        f"Known: {', '.join(FEED_KINDS)}."
    )
