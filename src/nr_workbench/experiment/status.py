"""When is a run's data ready to use?

A run is reduced one angle segment at a time, as each angle finishes being
measured: in the reference corpus, 218386's three segments landed 15 and then
52 minutes apart. A file still being written is never used, so a run whose
files changed recently is *arriving* or *settling*. Once its files have been
unchanged for ``settle_seconds`` it is *complete*, and what is there is used.

A segment that lands later is more data for the same run: the run is arriving
again, settles again, and the new file is copied beside the others (see
:mod:`~nr_workbench.experiment.apply`). A fit made before it landed is not
flagged, so it is fitted again once the new data is there.

The header's plan (:attr:`~nr_workbench.instrument.header.ReducedHeader.
n_segments`), when it has one, only shapes the reason: "2 of 3 planned
segments so far".
"""

from __future__ import annotations

from dataclasses import dataclass

from nr_workbench.arrival import settle_state
from nr_workbench.experiment.inventory import SourceRun

#: Every state a run can be in, in the order the page lists them.
STATES = (
    "awaiting",
    "arriving",
    "settling",
    "complete",
    "quarantined",
    "clock-skew",
)

#: How far in the future a file may be dated before the clocks are suspect.
#: File times come from the file server, "now" from this machine; a minute or
#: two of drift is ordinary and not worth a warning.
CLOCK_SKEW_TOLERANCE = 120.0


@dataclass(frozen=True)
class RunStatus:
    """What can be said about a run's arrival right now.

    Attributes:
        state: One of :data:`STATES`.
        reason: Why, in a sentence for the page.
        complete: Whether its files may be copied: settled, and nothing wrong
            with them.
        settled: Whether its files have stopped changing.
        quiet_for: Seconds since its files last changed.
    """

    state: str
    reason: str
    complete: bool = False
    settled: bool = False
    quiet_for: float = 0.0

    def as_dict(self) -> dict[str, object]:
        """Return the JSON form."""
        return {
            "state": self.state,
            "reason": self.reason,
            "complete": self.complete,
            "settled": self.settled,
            "quiet_for": round(self.quiet_for, 1),
        }


def judge(
    run: SourceRun | None,
    *,
    previous_fingerprint: str | None,
    now: float,
    settle_seconds: float,
) -> RunStatus:
    """Judge one run.

    Args:
        run: What the source lists for it, or ``None`` if only a feed has
            announced it.
        previous_fingerprint: Its fingerprint on the previous poll.
        now: This machine's clock.
        settle_seconds: How long its files must be unchanged.

    Returns:
        The status.
    """
    if run is None:
        return RunStatus(
            "awaiting",
            "announced, but no reduced files have appeared yet",
        )
    if run.problems:
        return RunStatus("quarantined", run.problems[0])
    if run.changed_at is not None and run.changed_at > now + CLOCK_SKEW_TOLERANCE:
        ahead = run.changed_at - now
        return RunStatus(
            "clock-skew",
            f"its files are dated {ahead:.0f}s in the future by the file server's "
            "clock, so nrw cannot tell when they stopped changing. Check this "
            "machine's clock against the data server's.",
        )

    state, quiet = settle_state(
        run.changed_at,
        run.fingerprint,
        previous_fingerprint,
        now=now,
        settle_seconds=settle_seconds,
    )
    if state != "settled":
        return RunStatus(
            state,
            f"unchanged for {max(quiet, 0.0):.0f}s of {settle_seconds:.0f}s",
            quiet_for=max(quiet, 0.0),
        )

    planned = run.n_segments
    if planned is not None and run.segments != tuple(range(1, planned + 1)):
        reason = (
            f"{len(run.segments)} of {planned} planned segments so far; the rest "
            "are added when they arrive"
        )
    elif planned is not None:
        reason = f"settled, with all {planned} planned segment(s)"
    else:
        reason = "settled"
    return RunStatus("complete", reason, complete=True, settled=True, quiet_for=quiet)
