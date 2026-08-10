"""What the files say about themselves, against what the notes say about them.

Every reduced REF_L file carries a ``# Meta:`` block recording the run title
the operator typed, the direct beam it was divided by, the scaling factor the
reduction applied, and the angle. ``sample.md`` carries a table of what the
scientist believes those runs were. Nothing has ever compared the two.

That gap is the most expensive one measured in this project. From a real
beamtime's own record:

* ``sample.md`` listed run 218393 as ``OCV`` and did not mention 218397 at all.
  Both are wrong --- the run titles say ``CA-realigned`` and ``OCV-after`` ---
  and the finding notes it *"sent five fits down the wrong path"*. The
  discrepancy is a string comparison between a header field and a table cell.
* Run 218389's segment was normalised against direct beam 218277 where the
  others used 218274, which is why its scale sat at ~0.85. The finding is
  titled *"a different direct beam, not physics"*. It is a join on one field.

Both were available before the first fit ran. Neither needed a model, a fit, or
a language model --- only for something to look.

Nothing here decides anything. It reports disagreements between two records
that are supposed to describe the same runs, and says which side each fact came
from, because the resolution is a scientific judgement: sometimes the notes are
wrong, and sometimes the filing is.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: A markdown table row: ``| 218393 | full Q | -0.5 mA/cm2 |``.
_ROW = re.compile(r"^\s*\|(.+)\|\s*$")

#: A cell that is only dashes and colons is the header underline.
_RULE = set("-: ")

#: How far two angles may differ and still count as the same setting, in
#: degrees. Reduction records theta to four decimals and the motor repeats to
#: better than this; a real difference is tenths.
THETA_TOLERANCE = 0.02


@dataclass
class Finding:
    """One disagreement, with both sides and where each came from.

    Attributes:
        kind: A short slug, e.g. ``undocumented-run``.
        severity: ``info``, ``warn``, or ``blocker``.
        run: The run it concerns, when it concerns one.
        message: What disagrees, in a sentence.
        from_file: What the header says.
        from_notes: What ``sample.md`` says.
    """

    kind: str
    severity: str
    message: str
    run: int | None = None
    from_file: str | None = None
    from_notes: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return the JSON form."""
        return {
            "kind": self.kind,
            "severity": self.severity,
            "run": self.run,
            "message": self.message,
            "from_file": self.from_file,
            "from_notes": self.from_notes,
        }


@dataclass
class Reconciliation:
    """Everything the comparison found.

    Attributes:
        sample: The sample compared.
        runs: Run number to the headers of its segments.
        documented: Run number to the notes' row for it.
        findings: The disagreements.
    """

    sample: str
    runs: dict[int, list[Any]] = field(default_factory=dict)
    documented: dict[int, dict[str, str]] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """Return the JSON form."""
        return {
            "schema": "nrw-reconcile/1",
            "sample": self.sample,
            "runs": sorted(self.runs),
            "documented": sorted(self.documented),
            "findings": [f.as_dict() for f in self.findings],
        }

    @property
    def worst(self) -> str:
        """The highest severity present, or ``ok``."""
        for level in ("blocker", "warn"):
            if any(f.severity == level for f in self.findings):
                return level
        return "ok"


def read_table(markdown: str) -> list[dict[str, str]]:
    """Read every markdown table row as a mapping keyed by its header.

    Args:
        markdown: The prose to scan.

    Returns:
        One mapping per data row, lowercased keys. Rows from different tables
        are returned together; a row is only useful if it carries a ``run``.
    """
    rows: list[dict[str, str]] = []
    header: list[str] = []
    for line in markdown.splitlines():
        match = _ROW.match(line)
        if not match:
            header = []  # a table ended
            continue
        cells = [c.strip() for c in match.group(1).split("|")]
        if set("".join(cells)) <= _RULE:
            continue
        if not header:
            header = [c.lower() for c in cells]
            continue
        rows.append(dict(zip(header, cells, strict=False)))
    return rows


def documented_runs(markdown: str) -> dict[int, dict[str, str]]:
    """The runs ``sample.md`` mentions in a table, keyed by run number.

    Args:
        markdown: The sample's prose.

    Returns:
        Run number to its row.
    """
    found: dict[int, dict[str, str]] = {}
    for row in read_table(markdown):
        raw = row.get("run") or row.get("run number") or ""
        digits = re.fullmatch(r"\s*(\d{4,})\s*", raw)
        if digits:
            found[int(digits.group(1))] = row
    return found


def reconcile(
    sample: str,
    headers: list[Any],
    markdown: str,
    *,
    series_runs: set[int] | None = None,
    standard_thetas: list[float] | None = None,
) -> Reconciliation:
    """Compare what the files record against what the notes claim.

    Args:
        sample: The sample being checked.
        headers: A :class:`ReducedHeader` per steady-state file.
        markdown: The sample's ``sample.md``.
        series_runs: Runs that also exist as a time-resolved series.
        standard_thetas: The project's usual angles.

    Returns:
        The disagreements found.
    """
    by_run: dict[int, list[Any]] = {}
    for header in headers:
        run = header.sequence_id or header.run
        if run is not None:
            by_run.setdefault(int(run), []).append(header)

    documented = documented_runs(markdown)
    result = Reconciliation(sample=sample, runs=by_run, documented=documented)

    result.findings.extend(_membership(by_run, documented))
    result.findings.extend(_titles(by_run, documented))
    result.findings.extend(_direct_beams(by_run))
    result.findings.extend(_series_in_steady(by_run, series_runs or set()))
    result.findings.extend(_angles(by_run, standard_thetas or []))
    return result


def _membership(
    by_run: dict[int, list[Any]], documented: dict[int, dict[str, str]]
) -> list[Finding]:
    """Runs on disk the notes do not mention, and the reverse."""
    findings = []
    for run in sorted(set(by_run) - set(documented)):
        findings.append(
            Finding(
                kind="undocumented-run",
                severity="warn",
                run=run,
                message=(
                    f"Run {run} has reduced data but no row in sample.md's "
                    "measurement table. A run nobody wrote up is a run whose "
                    "conditions are not recorded anywhere."
                ),
                from_file=f"{len(by_run[run])} segment(s) on disk",
                from_notes="absent",
            )
        )
    for run in sorted(set(documented) - set(by_run)):
        findings.append(
            Finding(
                kind="documented-run-absent",
                severity="info",
                run=run,
                message=f"sample.md lists run {run}, which has no reduced data yet.",
                from_file="absent",
                from_notes=_row_text(documented[run]),
            )
        )
    return findings


def _titles(
    by_run: dict[int, list[Any]], documented: dict[int, dict[str, str]]
) -> list[Finding]:
    """The operator's run title against how the notes describe that run.

    Only the electrochemical state is compared, not the wording. A run title
    is a filename-shaped label (``CuPt_d8-THF-fullQ_CA-realigned-218393-1``)
    and a table cell is prose; a general word-overlap test between them fires
    on almost every row, and a check that cries wolf is a check nobody reads.

    The state is the thing that actually got mislabelled: a table listing a run
    as ``OCV`` when its own title says ``CA`` is the error that *"sent five
    fits down the wrong path"*.
    """
    findings = []
    for run, headers in sorted(by_run.items()):
        row = documented.get(run)
        title = next((h.run_title for h in headers if h.run_title), None)
        if not row or not title:
            continue
        described = _row_text(row)
        from_title = _electrochemical_state(title)
        from_notes = _electrochemical_state(described)
        if from_title is None or from_notes is None or from_title == from_notes:
            continue
        findings.append(
            Finding(
                kind="state-contradicts-notes",
                severity="warn",
                run=run,
                message=(
                    f"Run {run}'s own title says it was measured "
                    f"{from_title.replace('_', ' ')}, but sample.md describes "
                    f"it as {from_notes.replace('_', ' ')}. The header is what "
                    "the instrument recorded; the table is what somebody typed."
                ),
                from_file=title,
                from_notes=described,
            )
        )
    return findings


#: Markers for the two electrochemical states a REF_L run is usually in. Kept
#: deliberately small: a term that appears here must be unambiguous, because a
#: false contradiction costs more attention than a missed one.
#:
#: The boundaries are spelled out rather than using ``\b``. Run titles are
#: underscore-delimited (``CuPt_d8-THF-fullQ_CA-realigned``) and ``_`` is a
#: word character, so ``\bCA\b`` does not match ``_CA-`` --- which is exactly
#: the title that carried the error this check exists to catch.
_EDGE = r"(?<![A-Za-z0-9])"
_EDGE_AFTER = r"(?![A-Za-z0-9])"

_OCV = re.compile(rf"{_EDGE}OC[VP]{_EDGE_AFTER}|open[\s_-]?circuit", re.IGNORECASE)
_APPLIED = re.compile(
    rf"[+\-\u2212]?\d+(?:\.\d+)?\s*(?:V|mV|mA|uA){_EDGE_AFTER}"
    rf"|{_EDGE}C[AP]{_EDGE_AFTER}"
    r"|chrono|galvanostat|potentiostat|potential",
    re.IGNORECASE,
)


def _electrochemical_state(text: str) -> str | None:
    """``open_circuit``, ``under_applied_potential``, or None if unstated."""
    if not text:
        return None
    ocv = bool(_OCV.search(text))
    applied = bool(_APPLIED.search(text))
    if ocv and not applied:
        return "open_circuit"
    if applied and not ocv:
        return "under_applied_potential"
    return None


def _direct_beams(by_run: dict[int, list[Any]]) -> list[Finding]:
    """One angle normalised against different direct beams in different runs.

    Segments of one run legitimately use *different* direct beams --- one per
    angle --- so comparing within a run flags every measurement ever made. The
    real signal is the same angle disagreeing across runs, which puts a
    different beam's intensity into one of them and reads as a scale
    difference that is instrumental rather than structural.
    """
    by_angle: dict[int, dict[int, int]] = {}
    for run, headers in by_run.items():
        for header in headers:
            if header.sequence_number is None or header.norm_run is None:
                continue
            by_angle.setdefault(int(header.sequence_number), {})[run] = int(
                header.norm_run
            )

    findings = []
    for segment, per_run in sorted(by_angle.items()):
        beams = set(per_run.values())
        if len(beams) < 2 or len(per_run) < 2:
            continue
        findings.append(
            Finding(
                kind="direct-beam-differs",
                severity="warn",
                message=(
                    f"Segment {segment} was normalised against different direct "
                    "beams in different runs. Co-refining these shares a "
                    "structure across measurements whose intensity scales are "
                    "not comparable -- give that segment its own "
                    "probe.intensity rather than letting a layer absorb it."
                ),
                from_file="; ".join(
                    f"run {r}: beam {b}" for r, b in sorted(per_run.items())
                ),
            )
        )
    return findings


def _series_in_steady(by_run: dict[int, list[Any]], series: set[int]) -> list[Finding]:
    """A time-resolved run whose whole-run reduction is filed as a steady state.

    The ground truth is unambiguous: *"Its living under `data/steady/` is a
    filing accident, not a statement that it is a steady state."* Its R(Q) is a
    counting-time-weighted average over a moving fringe pattern, so no single
    structure describes it, and fitting it as a state pins the model to a
    smearing artefact.
    """
    findings = []
    for run in sorted(set(by_run) & series):
        findings.append(
            Finding(
                kind="series-run-in-steady",
                severity="blocker",
                run=run,
                message=(
                    f"Run {run} has a steady-state file and a time-resolved "
                    "series. The steady file is the reduction of the whole run "
                    "-- the run during which the structure changed -- so no "
                    "single structure describes it. Do not fit it as a state."
                ),
                from_file="present in data/steady/ and data/tnr/",
            )
        )
    return findings


def _angles(by_run: dict[int, list[Any]], standard: list[float]) -> list[Finding]:
    """Angles that are not one of the project's usual settings."""
    if not standard:
        return []
    findings = []
    for run, headers in sorted(by_run.items()):
        for header in headers:
            if header.theta is None:
                continue
            if any(abs(header.theta - s) <= THETA_TOLERANCE for s in standard):
                continue
            findings.append(
                Finding(
                    kind="unusual-angle",
                    severity="info",
                    run=run,
                    message=(
                        f"Run {run} has a segment at {header.theta:.4f} deg, "
                        "which is not one of this project's standard angles. "
                        "Worth confirming it is the measurement you think."
                    ),
                    from_file=f"{header.theta:.4f} deg ({Path(header.path).name})",
                    from_notes=", ".join(f"{s:g}" for s in standard),
                )
            )
    return findings


def _row_text(row: dict[str, str]) -> str:
    """The descriptive cells of a table row, run number excluded."""
    return " ".join(
        value
        for key, value in row.items()
        if value and key not in {"run", "run number"}
    ).strip()
