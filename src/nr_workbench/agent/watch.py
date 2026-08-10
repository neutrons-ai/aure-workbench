"""Decide *when* a session should start, and over what.

This is a scheduler, not a second brain. It answers three questions and no
others: is this measurement finished arriving, is it trustworthy, and has
anybody looked at it yet. Everything after that is
:mod:`nr_workbench.agent.session` handing the harness one task.

The three questions each exist because of something that actually went wrong.

**Finished arriving.** A steady-state measurement is three angle segments
written minutes apart. A daemon reacting to file *creation* fits the first
segment alone, gets a plausible answer from a third of the data, and records
it. So: the segments must be contiguous from 1, their subruns must be
consecutive from the run number, and nothing in the directory may have changed
for ``settle_seconds``. Polling, not ``watchdog`` --- these land on NFS, where
inotify is a suggestion, and reduction takes minutes anyway.

**Trustworthy.** In the reference experiment a whole-run reduction of a
time-resolved measurement was written into ``data/steady/``. A human reading
the directory saw it eventually; a daemon would have fitted it that night. Any
of three fingerprints quarantines a run, because a false quarantine costs one
morning's attention and a false pass costs a night.

**Nobody has looked yet.** Keyed on the recorded fits, not on a state file. A
state file is a second source of truth that goes stale the first time somebody
fits by hand, and then the daemon repeats work the scientist already did.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: How long a measurement's files must be unchanged before it counts as
#: complete. Reduction writes segments minutes apart, so this is generous ---
#: waiting five minutes too long costs nothing, and starting one minute too
#: early costs a fit on partial data that looks perfectly reasonable.
DEFAULT_SETTLE_SECONDS = 300

#: Seconds between polls. Nothing here is urgent: the beam is slower than this.
DEFAULT_POLL_SECONDS = 60

#: How long one session may run before it is killed. Two hours is longer than
#: any single task should need and short enough that a wedged session costs
#: one measurement rather than the night.
DEFAULT_SESSION_TIMEOUT = 7200

#: Steady-state measurements have three angle segments at BL-4B. A run with
#: fewer is not necessarily wrong --- some are measured at one angle --- so
#: this is not a completeness test, only the shape the subrun rule assumes.
CONSECUTIVE_SUBRUNS = True


@dataclass(frozen=True)
class Verdict:
    """Whether one measurement is ready to be worked on.

    A run can appear twice --- once as a steady-state measurement and once as
    a time-resolved series --- which is itself the mis-filing fingerprint, so
    ``kind`` is part of the identity rather than a decoration.

    Attributes:
        run: The run number.
        ready: Whether a session may be started for it.
        state: ``ready``, ``arriving``, ``settling``, ``quarantined`` or
            ``done``.
        kind: ``steady`` or ``series``.
        reason: Why, in a sentence a scientist reads in the morning.
    """

    run: int
    ready: bool
    state: str
    kind: str = "steady"
    reason: str = ""


@dataclass
class WatchState:
    """What the watcher has seen, within one process.

    Held in memory on purpose. Persisting it would create a second source of
    truth about what has been analysed, and the index is already that.

    Attributes:
        quiet_since: Run number to the time its files last looked unchanged.
        fingerprints: Run number to the digest of its file listing.
    """

    quiet_since: dict[int, float] = field(default_factory=dict)
    fingerprints: dict[int, str] = field(default_factory=dict)


def quarantine_reason(scan: Any, run: int) -> str:
    """Why this run must not be fitted unattended, or an empty string.

    Any one fingerprint is enough. They are cheap and independent, and the
    asymmetry is deliberate: a run wrongly held back is a question in the
    morning, a run wrongly fitted is a night of results built on the wrong
    data.

    Args:
        scan: A :class:`nr_workbench.project.scan.ScanResult`.
        run: The run to judge.

    Returns:
        The reason, or an empty string when the run looks sound.
    """
    measurement = scan.steady.get(run)
    if measurement is None:
        return ""

    series_runs = {s.run for s in scan.series if s.run is not None}
    if run in series_runs:
        return (
            f"run {run} appears as both a steady-state measurement and a "
            "time-resolved series. One of the two is a filing accident, and "
            "which one is a question for a person."
        )

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
    from nr_workbench.project.scan import PARTIAL_RE

    found: list[tuple[int, int, int]] = []
    for segment, path in sorted(measurement.partials.items()):
        match = PARTIAL_RE.match(Path(path).name)
        if not match:
            continue
        subrun = int(match.group("subrun"))
        expected = measurement.run + segment - 1
        if subrun != expected:
            found.append((segment, subrun, expected))
    return found


def series_complete(root: Path, series: Any) -> str:
    """Why a time-resolved series is not finished, or an empty string.

    Slices appear one at a time over the length of the electrochemistry, so
    counting files says nothing. The reduction sidecar is the only thing that
    knows how many there should be: it names the intervals, and the series is
    complete when every named interval has a file.

    Args:
        root: Project root.
        series: A :class:`nr_workbench.project.scan.SeriesMeasurement`.

    Returns:
        The reason it is incomplete, or an empty string.
    """
    import json

    if not series.reduction_json:
        return (
            "no reduction JSON yet, so there is no way to know how many "
            "slices to expect"
        )

    sidecar = root / series.reduction_json
    try:
        reduction = json.loads(sidecar.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return f"{sidecar.name} is not readable yet ({type(exc).__name__})"

    if not isinstance(reduction, dict):
        # A list or a bare string parses fine and then has no `.get`. Not
        # hypothetical: reduction scripts vary, and an exception here ends the
        # night's run for every other sample too.
        return f"{sidecar.name} is not a JSON object yet"

    expected = _interval_count(reduction)
    if expected is None:
        return ""
    if series.n_slices < expected:
        return (
            f"{series.n_slices} of {expected} slices reduced so far, per {sidecar.name}"
        )
    return ""


def _interval_count(reduction: dict[str, Any]) -> int | None:
    """How many slices a reduction JSON says there are, if it says.

    The sidecar's shape varies with the reduction script, so this looks for a
    list of intervals under any of the names it has been seen to use and gives
    up quietly rather than guessing.
    """
    for key in ("intervals", "time_intervals", "slices", "bins"):
        value = reduction.get(key)
        if isinstance(value, list) and value:
            return len(value)
    return None


def fingerprint(root: Path, paths: list[str]) -> str:
    """A digest of the files' names, sizes and modification times.

    Content hashing would be more certain and is not worth it: a reduction
    rewriting a file always changes its mtime, and these are megabytes each,
    polled every minute.
    """
    import hashlib

    digest = hashlib.sha256()
    for relative in sorted(paths):
        path = root / relative
        try:
            stat = path.stat()
        except OSError:
            digest.update(f"{relative}:missing".encode())
            continue
        digest.update(f"{relative}:{stat.st_size}:{stat.st_mtime_ns}".encode())
    return digest.hexdigest()


def _last_change(root: Path, paths: list[str]) -> float | None:
    """When any of these files last changed, as a wall-clock timestamp.

    Returns:
        The newest modification time, or ``None`` if none of them can be read.
    """
    times = []
    for relative in paths:
        try:
            times.append((root / relative).stat().st_mtime)
        except OSError:
            continue
    return max(times) if times else None


def assess(
    root: Path,
    sample: str,
    state: WatchState,
    *,
    settle_seconds: float = DEFAULT_SETTLE_SECONDS,
    now: float | None = None,
) -> list[Verdict]:
    """Judge every measurement in one sample.

    Args:
        root: Project root.
        sample: Sample identifier.
        state: The watcher's memory, updated in place.
        settle_seconds: How long files must be unchanged.
        now: Wall-clock reference, for tests. Compared against file
            modification times, so it is a real timestamp, not a counter.

    Returns:
        One verdict per steady-state run and per series, in run order.
    """
    from nr_workbench.project.layout import ProjectLayout
    from nr_workbench.project.scan import scan_sample
    from nr_workbench.provenance.index import FitIndex

    moment = time.time() if now is None else now
    scan = scan_sample(root, sample)

    fitted = _runs_already_fitted(root, sample, ProjectLayout, FitIndex)

    verdicts: list[Verdict] = []
    for run in sorted(scan.steady):
        verdicts.append(
            _guarded(
                run,
                "steady",
                lambda r=run: _judge_steady(
                    root,
                    scan,
                    r,
                    state,
                    fitted,
                    settle_seconds=settle_seconds,
                    now=moment,
                ),
            )
        )

    for series in scan.series:
        if series.run is None:
            continue
        verdicts.append(
            _guarded(
                series.run, "series", lambda s=series: _judge_series(root, s, fitted)
            )
        )

    return verdicts


def _guarded(run: int, kind: str, judge: Any) -> Verdict:
    """Judge one measurement, turning a crash into a held measurement.

    The daemon runs overnight with nobody watching, so an exception anywhere
    in here would end the run for every other sample as well. Whatever the
    failure was, the safe reading of it is the same: this measurement is not
    something to hand an agent.
    """
    try:
        return judge()
    except Exception as exc:  # noqa: BLE001 - see the docstring
        return Verdict(
            run,
            ready=False,
            state="quarantined",
            kind=kind,
            reason=(
                f"could not be assessed ({type(exc).__name__}: {exc}). "
                "Held back rather than fitted; this is a bug worth reporting."
            ),
        )


def _judge_steady(
    root: Path,
    scan: Any,
    run: int,
    state: WatchState,
    fitted: set[int],
    *,
    settle_seconds: float,
    now: float,
) -> Verdict:
    """Judge one steady-state measurement."""
    held = quarantine_reason(scan, run)
    if held:
        return Verdict(run, ready=False, state="quarantined", reason=held)

    measurement = scan.steady[run]
    paths = list(measurement.partials.values())
    if measurement.combined:
        paths.append(measurement.combined)

    # How long since anything changed, from the files themselves rather than
    # from what previous polls saw. A cross-poll counter cannot answer this on
    # the first poll, which would make `--dry-run` report every measurement as
    # "arriving" no matter how old it is --- the state a person checking the
    # queue most wants to see through.
    changed_at = _last_change(root, paths)
    quiet_for = now - changed_at if changed_at is not None else 0.0

    current = fingerprint(root, paths)
    if state.fingerprints.get(run) not in (None, current):
        # Changed between two polls. The mtime says the same thing, but a
        # filesystem with coarse timestamps may not, and a rewrite mid-poll is
        # exactly the case worth being conservative about.
        quiet_for = 0.0
    state.fingerprints[run] = current
    state.quiet_since[run] = changed_at if changed_at is not None else now

    if quiet_for < settle_seconds:
        state_name = "arriving" if quiet_for < 1.0 else "settling"
        return Verdict(
            run,
            ready=False,
            state=state_name,
            reason=f"unchanged for {max(quiet_for, 0.0):.0f}s of {settle_seconds:.0f}s",
        )

    if run in fitted:
        return Verdict(run, ready=False, state="done", reason="already fitted")

    return Verdict(
        run,
        ready=True,
        state="ready",
        reason=f"{len(measurement.partials)} segment(s), settled, not yet fitted",
    )


def _judge_series(root: Path, series: Any, fitted: set[int]) -> Verdict:
    """Judge one time-resolved series."""
    incomplete = series_complete(root, series)
    if incomplete:
        return Verdict(
            series.run, ready=False, state="arriving", kind="series", reason=incomplete
        )
    if series.run in fitted:
        return Verdict(
            series.run,
            ready=False,
            state="done",
            kind="series",
            reason="already fitted",
        )
    return Verdict(
        series.run,
        ready=True,
        state="ready",
        kind="series",
        reason=f"{series.n_slices} slices, complete",
    )


def _runs_already_fitted(
    root: Path, sample: str, layout_cls: Any, index_cls: Any
) -> set[int]:
    """Run numbers that a recorded fit already used as input.

    Read from each fit's ``inputs.json``, which lists the measurement files by
    path --- the index entry deliberately carries only digests, and a run
    number is not recoverable from a digest. The files are immutable and there
    are tens of them, against a poll once a minute.

    From the record rather than a state file, so a fit the scientist ran by
    hand counts. The daemon repeating work somebody already did is not a safety
    problem, but it is the fastest way to make the output unreadable.

    The run number comes from `project.scan`'s filename grammar, never from a
    regex over the whole path. `REFL_218386_2_218387_partial.txt` holds two
    six-digit numbers: the run and its subrun. Reading both marks run 218387
    --- a real, separate, neighbouring measurement at a beamline that numbers
    consecutively --- as already analysed, and the daemon then skips it in
    silence. That is the worst shape a bug can take here.
    """
    from nr_workbench.project.scan import COMBINED_RE, PARTIAL_RE, SLICE_RE
    from nr_workbench.provenance.lookup import fit_dir
    from nr_workbench.provenance.record import FitDirectory

    layout = layout_cls(root)
    runs: set[int] = set()
    for entry in index_cls(layout.index_file).fits(sample=sample):
        directory = fit_dir(layout, entry)
        if directory is None or not directory.is_dir():
            continue  # an orphan; `nrw check` reports those separately
        try:
            inputs = FitDirectory(directory).read_inputs()
        except (OSError, ValueError):
            continue
        for item in inputs:
            name = Path(str(item.get("path", ""))).name
            for pattern in (COMBINED_RE, PARTIAL_RE, SLICE_RE):
                if match := pattern.match(name):
                    runs.add(int(match.group("run")))
                    break
    return runs


# --------------------------------------------------------------------------
# The output budget
# --------------------------------------------------------------------------

#: One page per sample, and one escalations file. Not a setting: forty
#: individually defensible records are collectively unreadable, and once the
#: scientist stops reading, every other safety property here is a formality.
#: A session that wants to say more must say it in the same page.
MAX_REPORT_WORDS = 1200


def output_budget(root: Path, sample: str) -> tuple[int, int]:
    """How much prose exists for one sample, as (files, words).

    Args:
        root: Project root.
        sample: Sample identifier.

    Returns:
        The number of report files and the total word count.
    """
    reports = Path(root) / "samples" / sample / "reports"
    if not reports.is_dir():
        return (0, 0)
    files = sorted(p for p in reports.rglob("*.md") if p.is_file())
    words = sum(
        len(p.read_text(encoding="utf-8", errors="replace").split()) for p in files
    )
    return (len(files), words)


def over_budget(root: Path, sample: str) -> str:
    """Why this sample's output is unreadable, or an empty string.

    Checked after each session rather than enforced during one. A hard stop
    mid-session would leave a half-written page, which is worse than a long
    one; naming it here means the next session is told to consolidate.
    """
    files, words = output_budget(Path(root), sample)
    if words > MAX_REPORT_WORDS:
        return (
            f"samples/{sample}/reports/ holds {words} words across {files} "
            f"file(s), over the {MAX_REPORT_WORDS}-word budget. Consolidate "
            "into one page before writing more; nobody reads the second one."
        )
    return ""


# --------------------------------------------------------------------------
# The loop
# --------------------------------------------------------------------------


def once(
    root: Path,
    samples: list[str],
    state: WatchState,
    *,
    settle_seconds: float = DEFAULT_SETTLE_SECONDS,
) -> dict[str, list[Verdict]]:
    """Judge every sample once, without starting anything.

    Args:
        root: Project root.
        samples: Samples to consider.
        state: The watcher's memory, updated in place.
        settle_seconds: How long files must be unchanged.

    Returns:
        Sample identifier to its verdicts.
    """
    return {
        sample: assess(Path(root), sample, state, settle_seconds=settle_seconds)
        for sample in samples
    }


def watch(
    root: Path,
    samples: list[str],
    *,
    settle_seconds: float = DEFAULT_SETTLE_SECONDS,
    poll_seconds: float = DEFAULT_POLL_SECONDS,
    max_sessions: int | None = None,
    session_timeout: float | None = DEFAULT_SESSION_TIMEOUT,
    turns: int | None = None,
    model: str | None = None,
    dry_run: bool = False,
    on_event: Any = None,
) -> int:
    """Poll for settled measurements and run one session each.

    One session at a time, and never a second for a sample whose first is
    still running --- concurrency here buys nothing (the beam is slower than
    the fits) and costs the one thing that matters, which is a transcript a
    person can follow.

    Args:
        root: Project root.
        samples: Samples to watch.
        settle_seconds: How long files must be unchanged.
        poll_seconds: Seconds between polls.
        max_sessions: Stop after this many sessions. ``None`` runs until
            interrupted.
        session_timeout: Seconds before a single session is killed. Without
            one, a wedged session stops every later measurement from being
            looked at --- the failure costs the whole night, not one fit.
        turns: Cap on harness turns per session, or ``None`` for the default.
        model: Model to run, or ``None`` for the harness's own.
        dry_run: Report what would start; start nothing.
        on_event: Called with each line of progress, or ``None`` for stdout.

    Returns:
        How many sessions were started.
    """
    from nr_workbench.agent.session import SessionError
    from nr_workbench.agent.session import run as run_session

    say = on_event or print
    state = WatchState()
    started = 0
    seen: set[str] = set()

    while max_sessions is None or started < max_sessions:
        try:
            polled = once(Path(root), samples, state, settle_seconds=settle_seconds)
        except Exception as exc:  # noqa: BLE001 - a poll that raises ends the night
            # A sample directory removed mid-run, or an NFS stat that failed.
            # Both are expected here, and neither is a reason to stop looking.
            say(f"poll failed ({type(exc).__name__}: {exc}); trying again")
            time.sleep(poll_seconds)
            continue

        for sample, verdicts in polled.items():
            for verdict in verdicts:
                key = f"{sample}:{verdict.run}:{verdict.kind}:{verdict.state}"
                if verdict.state in {"quarantined", "ready"} and key not in seen:
                    seen.add(key)
                    say(
                        f"{sample} run {verdict.run} ({verdict.kind}): "
                        f"{verdict.state} -- {verdict.reason}"
                    )

            if not any(v.ready for v in verdicts):
                continue
            if dry_run:
                continue

            try:
                session = run_session(
                    Path(root),
                    sample,
                    timeout=session_timeout,
                    **({"turns": turns} if turns is not None else {}),
                    model=model,
                )
            except SessionError as exc:
                say(f"{sample}: cannot start a session -- {exc}")
                continue
            except Exception as exc:  # noqa: BLE001 - the night must continue
                say(
                    f"{sample}: the session failed ({type(exc).__name__}: {exc}). "
                    "Moving on; the rest of the night still runs."
                )
                continue

            started += 1
            say(f"{sample}: session finished (exit {session.returncode})")

            crowded = over_budget(Path(root), sample)
            if crowded:
                say(f"{sample}: {crowded}")

            if max_sessions is not None and started >= max_sessions:
                break

        if dry_run:
            break
        if max_sessions is None or started < max_sessions:
            time.sleep(poll_seconds)

    return started
