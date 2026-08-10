"""Automatic checks on a finished fit, and the prose to go with them.

Two halves, deliberately separated by what they need.

The **computable** half runs everywhere, with no language model and no AuRE
import. It reads the bumps problem JSON, the ``.par`` file and the DREAM
``-err.json`` and reports what those say about whether the answer is trustable:
parameters pressed against their bounds, posteriors that span most of their
prior, a maximum-likelihood point sitting outside its own credible interval,
per-model chi-squared spread, and BIC against the sibling fits of the same
sample. None of that is a matter of opinion, and all of it is the kind of thing
that gets missed at 2am.

The **judgement** half calls AuRE's ``analyze_fit_quality_with_llm``, which asks
a model whether the numbers are physically sensible given ``sample.md`` and the
installed domain skills. It runs only when an endpoint is configured.

The split matters because AuRE's own no-LLM fallback (``_simple_evaluation``)
is three chi-squared bands and nothing else --- it reads no parameters, no
bounds, no posterior. Wrapping it would have made the offline path look
supported while telling a user less than they could work out themselves. So the
offline path here is nr-workbench's own, and it is the stronger of the two on
everything except physics.

A posterior-aware boundary check is worth calling out: AuRE tests the point
estimate against the bound, and a parameter whose *p95 edge* touches its floor
is pinned in a way that test cannot see. Both are reported, separately.
"""

from __future__ import annotations

import contextlib
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: How close to a bound counts as sitting on it, as a fraction of the range.
#: AuRE uses 0.01 for point estimates; the same figure is used here so the two
#: agree on the same fit.
BOUND_TOLERANCE = 0.01

#: A posterior wider than this fraction of its prior is barely constrained --
#: the fit is reporting the range it was given back to you.
UNCONSTRAINED_FRACTION = 0.75


@dataclass
class Finding:
    """One thing worth knowing about a fit.

    Attributes:
        kind: A short slug, e.g. ``bound``, ``unconstrained``.
        severity: ``info``, ``warn``, or ``blocker``.
        parameter: The parameter involved, if any.
        message: What was found, in a sentence.
        value: The fitted value, for findings about one parameter.
        limit: The bound it is against, where that is the point.
        edge: ``lower`` or ``upper``.
    """

    kind: str
    severity: str
    message: str
    parameter: str | None = None
    value: float | None = None
    limit: float | None = None
    edge: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return the JSON form."""
        return {
            "kind": self.kind,
            "severity": self.severity,
            "parameter": self.parameter,
            "message": self.message,
            "value": self.value,
            "limit": self.limit,
            "edge": self.edge,
        }


@dataclass
class Assessment:
    """Everything the checks found.

    Attributes:
        fit_id: The fit assessed.
        chisq: Reduced chi-squared.
        n_free: Free parameters.
        n_points: Data points, summed across models.
        bic: Bayesian information criterion, when computable.
        findings: What the computable checks found.
        judgement: The language model's verdict, when one ran.
        problems: What could not be checked, and why.
    """

    fit_id: str
    chisq: float | None = None
    n_free: int | None = None
    n_points: int | None = None
    bic: float | None = None
    findings: list[Finding] = field(default_factory=list)
    judgement: dict[str, Any] | None = None
    problems: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """Return the JSON form."""
        return {
            "schema": "nrw-fit-assessment/1",
            "fit_id": self.fit_id,
            "chisq": self.chisq,
            "n_free": self.n_free,
            "n_points": self.n_points,
            "bic": self.bic,
            "findings": [f.as_dict() for f in self.findings],
            "judgement": self.judgement,
            "problems": self.problems,
        }

    @property
    def worst(self) -> str:
        """The highest severity present, or ``ok``."""
        for level in ("blocker", "warn"):
            if any(f.severity == level for f in self.findings):
                return level
        return "ok"


def bic(chisq: float, n_points: int, n_free: int) -> float:
    """Bayesian information criterion from a *reduced* chi-squared.

    Args:
        chisq: Reduced chi-squared.
        n_points: Number of data points.
        n_free: Number of free parameters.

    Returns:
        ``n ln(chi2) + k ln(n)``, AuRE's form, so the two are comparable.
    """
    return n_points * math.log(chisq) + n_free * math.log(n_points)


def read_bounds(fit_dir: Path) -> dict[str, tuple[float, float]]:
    """Read each free parameter's range from the serialised bumps problem.

    The problem JSON is the right source: it has already resolved everything
    the spec expresses indirectly --- ``pm: 0.3`` around a value, a constraint
    endpoint that becomes two parameters, a per-measurement override --- into
    the names and ranges the fitter actually used.

    Args:
        fit_dir: The result directory.

    Returns:
        Parameter name to ``(low, high)``, for free parameters only.
    """
    bounds: dict[str, tuple[float, float]] = {}
    for path in sorted((fit_dir / "fit").glob("*.json")):
        if path.name.endswith("-err.json") or "-expt" in path.name:
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        references = payload.get("references")
        if not isinstance(references, dict | list):
            continue
        entries = references.values() if isinstance(references, dict) else references
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("fixed"):
                continue
            name, limits = entry.get("name"), entry.get("bounds")
            if not isinstance(name, str) or not isinstance(limits, list):
                continue
            if len(limits) == 2 and all(isinstance(v, int | float) for v in limits):
                bounds[name] = (float(limits[0]), float(limits[1]))
        if bounds:
            break
    return bounds


def read_par(fit_dir: Path) -> dict[str, float]:
    """Read the best-fit parameters.

    Args:
        fit_dir: The result directory.

    Returns:
        Parameter name to value. Names contain spaces, so the split is on the
        last field.
    """
    values: dict[str, float] = {}
    for path in sorted((fit_dir / "fit").glob("*.par")):
        for line in path.read_text(encoding="utf-8").splitlines():
            name, _, number = line.strip().rpartition(" ")
            if not name:
                continue
            try:
                values[name.strip()] = float(number)
            except ValueError:
                continue
        break
    return values


def read_uncertainty(fit_dir: Path) -> dict[str, dict[str, Any]]:
    """Read the DREAM per-parameter statistics, if the fit produced them.

    Args:
        fit_dir: The result directory.

    Returns:
        Parameter name to its ``-err.json`` entry. Empty for an optimiser run,
        which is an absence of information rather than certainty.
    """
    for path in sorted((fit_dir / "fit").glob("*-err.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if isinstance(payload, dict):
            return {k: v for k, v in payload.items() if isinstance(v, dict)}
    return {}


def per_model_chisq(fit_dir: Path) -> dict[str, float]:
    """Per-model chi-squared, parsed from the bumps run log.

    Args:
        fit_dir: The result directory.

    Returns:
        Model label to chi-squared. Empty when there is no log.
    """
    found: dict[str, float] = {}
    for path in sorted((fit_dir / "fit").glob("*.out")):
        label = None
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            header = re.match(r"^--\s+Model\s+\d+\s+(\S+)", line)
            if header:
                label = header.group(1)
                continue
            match = re.search(r"\[chisq=([0-9.eE+-]+)", line)
            if match and label:
                with contextlib.suppress(ValueError):
                    found[label] = float(match.group(1))
                label = None
        break
    return found


def check(fit_dir: Path, manifest: dict[str, Any]) -> Assessment:
    """Run every check that needs no language model.

    Args:
        fit_dir: The result directory.
        manifest: The fit's manifest.

    Returns:
        The assessment, with ``judgement`` unset.
    """
    info = manifest.get("info") or {}
    provenance = manifest.get("provenance") or {}
    result = Assessment(
        fit_id=str(provenance.get("fit_id") or fit_dir.name),
        chisq=info.get("chisq"),
        n_free=info.get("n_free"),
    )

    # `info.n_points` is bumps' `dof`, not N. The per-model counts are the
    # honest total, and BIC is wrong by k log n if you use the wrong one.
    models = info.get("models") or []
    counted = sum(m.get("n_points", 0) for m in models if isinstance(m, dict))
    result.n_points = counted or info.get("n_points")

    if (
        isinstance(result.chisq, int | float)
        and result.chisq > 0
        and isinstance(result.n_points, int)
        and result.n_points > 0
        and isinstance(result.n_free, int)
    ):
        result.bic = bic(float(result.chisq), result.n_points, result.n_free)

    values = read_par(fit_dir)
    bounds = read_bounds(fit_dir)
    stats = read_uncertainty(fit_dir)

    if not values:
        result.problems.append("No .par file, so the fitted values could not be read.")
    if not bounds:
        result.problems.append(
            "No serialised problem, so parameter ranges are unknown and "
            "bound checks were skipped."
        )

    railed = _bound_findings(values, bounds)
    result.findings.extend(railed)
    # The posterior checks earn their place by catching what the point estimate
    # misses. Repeating them for a parameter already reported as railed turns
    # one problem into three lines and buries the other parameters.
    result.findings.extend(
        _posterior_findings(bounds, stats, already={f.parameter for f in railed})
    )

    if not stats:
        result.findings.append(
            Finding(
                kind="no-uncertainty",
                severity="info",
                message=(
                    "No uncertainty estimate: this was an optimiser run. That is "
                    "not the same as small uncertainties -- run dream before "
                    "quoting any interval."
                ),
            )
        )

    spread = per_model_chisq(fit_dir)
    if len(spread) > 1:
        worst = max(spread.items(), key=lambda kv: kv[1])
        best = min(spread.items(), key=lambda kv: kv[1])
        if worst[1] > 2 * best[1]:
            result.findings.append(
                Finding(
                    kind="uneven-fit",
                    severity="warn",
                    message=(
                        f"Chi-squared varies {best[1]:.3g} to {worst[1]:.3g} across "
                        f"{len(spread)} models; the overall figure hides that "
                        f"{worst[0]} fits far worse than {best[0]}."
                    ),
                )
            )
    return result


def _bound_findings(
    values: dict[str, float],
    bounds: dict[str, tuple[float, float]],
) -> list[Finding]:
    """Parameters whose best-fit value sits on a bound."""
    findings = []
    for name, value in values.items():
        limits = bounds.get(name)
        if not limits:
            continue
        low, high = limits
        span = high - low
        if span <= 0:
            continue
        if abs(value - low) <= BOUND_TOLERANCE * span:
            edge, at = "lower", low
        elif abs(value - high) <= BOUND_TOLERANCE * span:
            edge, at = "upper", high
        else:
            continue
        findings.append(
            Finding(
                kind="bound",
                severity="warn",
                parameter=name,
                value=value,
                limit=at,
                edge=edge,
                message=(
                    f"{name} = {value:.6g} sits on its {edge} bound ({at:g}). "
                    "The data want to go past it, so this value is the bound "
                    "you chose, not a measurement."
                ),
            )
        )
    return findings


def _posterior_findings(
    bounds: dict[str, tuple[float, float]],
    stats: dict[str, dict[str, Any]],
    already: set[str | None] | None = None,
) -> list[Finding]:
    """What the DREAM posterior says that the point estimate does not.

    Args:
        bounds: Parameter ranges.
        stats: The ``-err.json`` entries.
        already: Parameters already reported as sitting on a bound, whose
            posterior findings would only restate that.

    Returns:
        Findings for parameters not already accounted for.
    """
    reported = already or set()
    findings = []
    for name, entry in stats.items():
        if name in reported:
            continue
        limits = bounds.get(name)
        p95 = entry.get("p95")
        p68 = entry.get("p68")
        best = entry.get("best")

        if limits and isinstance(p95, list) and len(p95) == 2:
            low, high = limits
            span = high - low
            if span > 0:
                width = float(p95[1]) - float(p95[0])
                if width >= UNCONSTRAINED_FRACTION * span:
                    findings.append(
                        Finding(
                            kind="unconstrained",
                            severity="warn",
                            parameter=name,
                            message=(
                                f"{name}'s 95% interval spans {width / span:.0%} of "
                                "its allowed range: the data barely constrain it, "
                                "and the fit is handing your prior back."
                            ),
                        )
                    )
                elif (
                    float(p95[0]) - low <= BOUND_TOLERANCE * span
                    or high - float(p95[1]) <= BOUND_TOLERANCE * span
                ):
                    findings.append(
                        Finding(
                            kind="posterior-bound",
                            severity="warn",
                            parameter=name,
                            message=(
                                f"{name}'s 95% interval reaches its bound even "
                                "though the best-fit value does not. The "
                                "posterior is pressed against the range."
                            ),
                        )
                    )

        if (
            isinstance(best, int | float)
            and isinstance(p68, list)
            and len(p68) == 2
            and not (float(p68[0]) <= float(best) <= float(p68[1]))
        ):
            findings.append(
                Finding(
                    kind="skewed",
                    severity="info",
                    parameter=name,
                    message=(
                        f"{name}'s best-fit value {float(best):.6g} lies outside its "
                        f"own 68% interval [{float(p68[0]):.6g}, {float(p68[1]):.6g}] "
                        "-- a skewed or multimodal posterior. Quote the median "
                        "and the interval, not the point."
                    ),
                )
            )
    return findings


def as_markdown(assessment: Assessment) -> str:
    """Render an assessment as the prose that goes into a note.

    Args:
        assessment: What the checks found.

    Returns:
        A markdown section.
    """
    from nr_workbench.notes import GENERATED_CLOSE, GENERATED_OPEN

    lines = [GENERATED_OPEN, "## Assessment", ""]
    facts = []
    if assessment.chisq is not None:
        facts.append(f"chi-squared {assessment.chisq:.4g}")
    if assessment.n_free is not None:
        facts.append(f"{assessment.n_free} free")
    if assessment.n_points is not None:
        facts.append(f"{assessment.n_points} points")
    if assessment.bic is not None:
        facts.append(f"BIC {assessment.bic:.1f}")
    if facts:
        lines += [", ".join(facts), ""]

    if assessment.findings:
        for finding in assessment.findings:
            mark = {"blocker": "**", "warn": "**", "info": ""}[finding.severity]
            lines.append(f"- {mark}{finding.kind}{mark}: {finding.message}")
        lines.append("")
    else:
        lines += [
            "No parameter sits on a bound, and no posterior is unconstrained.",
            "",
        ]

    judgement = assessment.judgement
    if judgement:
        lines += ["### What a language model made of it", ""]
        quality = judgement.get("quality_assessment")
        if quality:
            lines += [f"Quality: **{quality}**", ""]
        for key, heading in (
            ("issues", "Issues"),
            ("physical_concerns", "Physical concerns"),
            ("suggestions", "Suggestions"),
        ):
            items = judgement.get(key) or []
            if items:
                lines.append(f"{heading}:")
                lines += [f"- {item}" for item in items]
                lines.append("")
        lines += [
            "<!-- Written by a language model from the numbers, sample.md and the",
            "     installed skills. It has not seen the data. Treat it as a",
            "     reviewer's first pass, not a result. -->",
            "",
        ]

    if assessment.problems:
        lines.append("Not checked:")
        lines += [f"- {p}" for p in assessment.problems]
        lines.append("")
    lines.append(GENERATED_CLOSE)
    lines.append("")
    return "\n".join(lines)
