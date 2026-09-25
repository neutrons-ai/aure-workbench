"""When a run's reduced files have finished arriving, and whether they belong together.

Two things ask. ``nrw agent watch`` asks before it fits a sample's data, and
the Experiment page asks before it copies a run out of the facility's folder.
They must answer by the same rule and in the same words, and neither may reach
into the other for it: the experiment's data layer importing the agent's
scheduler had the dependency pointing the wrong way.

Only the files' names, sizes and times are read here. Whether a *settled* run
is also *complete* needs evidence about the sequence of runs, and that is each
caller's judgement -- see :mod:`nr_workbench.experiment.status`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

#: How long a measurement's files must be unchanged before they count as
#: settled. Reduction writes segments minutes apart, so this is generous ---
#: waiting five minutes too long costs nothing, and starting one minute too
#: early costs a fit on partial data that looks perfectly reasonable. It is
#: still not proof the run has ended; see :func:`settle_state`.
DEFAULT_SETTLE_SECONDS = 300


def segment_problems(measurement: Any) -> str:
    """Why a measurement's segments do not look like one run, or ``""``.

    The part of :func:`nr_workbench.agent.watch.quarantine_reason` that needs
    nothing but the files' names, split out so a check made against a facility folder -- before any
    file has been copied into a sample -- uses the same rule and the same
    words as the one made in the sample afterwards.

    Args:
        measurement: A :class:`nr_workbench.project.scan.SteadyMeasurement`.
            Its paths may be absolute; only the names are read.

    Returns:
        The reason, or an empty string when the segments look like one run.
    """
    run = measurement.run
    segments = sorted(measurement.partials)
    if segments and segments != list(range(1, len(segments) + 1)):
        return (
            f"run {run} has angle segments {segments}, which are not "
            "contiguous from 1. A segment is missing, or a file from another "
            "run landed here."
        )

    mismatched = _subrun_mismatches(measurement)
    if mismatched:
        return (
            f"run {run}: segment {mismatched[0][0]} names subrun "
            f"{mismatched[0][1]}, but consecutive segments of run {run} should "
            f"be subrun {mismatched[0][2]}. These files are probably not all "
            "the same measurement."
        )

    return ""


def _subrun_mismatches(measurement: Any) -> list[tuple[int, int, int]]:
    """Segments whose subrun does not follow from the run number.

    Returns:
        ``(segment, found_subrun, expected_subrun)`` for each mismatch.
    """
    from nr_workbench.instrument.reduced import parse_segment_name

    found: list[tuple[int, int, int]] = []
    for segment, path in sorted(measurement.partials.items()):
        parsed = parse_segment_name(Path(path).name)
        if parsed is None:
            continue
        subrun = parsed.subrun
        expected = measurement.run + segment - 1
        if subrun != expected:
            found.append((segment, subrun, expected))
    return found


def fingerprint_entries(entries: Any) -> str:
    """:func:`nr_workbench.agent.watch.fingerprint`, over files already stat'ed.

    A caller that has just listed a directory has every size and mtime in hand;
    stat'ing each file again to fingerprint it would double the traffic to an
    NFS server that is being polled every minute.

    Args:
        entries: ``(name, size, mtime_ns)`` per file, with ``size`` and
            ``mtime_ns`` both ``None`` for a file that could not be read.

    Returns:
        The same digest `fingerprint` produces for the same files.
    """
    import hashlib

    digest = hashlib.sha256()
    for name, size, mtime_ns in sorted(entries, key=lambda entry: entry[0]):
        if size is None:
            digest.update(f"{name}:missing".encode())
            continue
        digest.update(f"{name}:{size}:{mtime_ns}".encode())
    return digest.hexdigest()


def settle_state(
    changed_at: float | None,
    current: str,
    previous: str | None,
    *,
    now: float,
    settle_seconds: float,
) -> tuple[str, float]:
    """Whether a set of files has stopped changing.

    Settled is not the same as *complete*: segments of one run are reduced
    minutes apart -- 15 and 52 minutes in the reference corpus -- so a run can
    sit settled with only its first segment on disk. That judgement needs
    evidence about the sequence, not about the files, and is the caller's.

    Args:
        changed_at: When any of the files last changed, or ``None`` if none
            could be read.
        current: Fingerprint of the files now.
        previous: Fingerprint from the previous poll, or ``None`` on the first.
        now: Wall-clock reference, comparable with ``changed_at``.
        settle_seconds: How long the files must be unchanged.

    Returns:
        ``(state, quiet_for)``: state is ``arriving``, ``settling`` or
        ``settled``, and ``quiet_for`` is the seconds the files have been
        unchanged (zero when they changed since the previous poll).
    """
    # How long since anything changed, from the files themselves rather than
    # from what previous polls saw. A cross-poll counter cannot answer this on
    # the first poll, which would make `--dry-run` report every measurement as
    # "arriving" no matter how old it is --- the state a person checking the
    # queue most wants to see through.
    quiet_for = now - changed_at if changed_at is not None else 0.0
    if previous not in (None, current):
        # Changed between two polls. The mtime says the same thing, but a
        # filesystem with coarse timestamps may not, and a rewrite mid-poll is
        # exactly the case worth being conservative about.
        quiet_for = 0.0
    if quiet_for < settle_seconds:
        return ("arriving" if quiet_for < 1.0 else "settling", quiet_for)
    return ("settled", quiet_for)
