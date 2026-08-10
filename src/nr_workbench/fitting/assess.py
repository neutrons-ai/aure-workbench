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

#: Above this, two parameters are trading against each other rather than each
#: being measured. `thin-layer-degeneracy` calls the (rho, t) ridge the thing
#: to look for, and the real analysis turned on exactly these numbers: an
#: r = -0.93 pair that made a thickness interchangeable with a solvent
#: interface, and an r = +0.97 pair broken only by fixing one side.
CORRELATION_THRESHOLD = 0.8

#: How many correlated pairs to name before summarising the rest. Eight free
#: parameters produced ten pairs on the real fit; listing all of them buries
#: the strongest.
MAX_PAIRS_REPORTED = 5

#: Draw a chain down to about this many rows before correlating. A posterior
#: correlation does not need half a million draws, and the largest chain in
#: the real corpus is 298 MB.
CORRELATION_SAMPLE_ROWS = 40_000

#: A layer counts as reaching its nominal SLD if the profile comes within this
#: much of it, in 1e-6 A^-2.
ATTAINMENT_SLACK = 0.05


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

    result.findings.extend(_correlation_findings(fit_dir))
    result.findings.extend(_attainment_findings(fit_dir))

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


def read_chain(fit_dir: Path) -> tuple[Any, list[str]]:
    """Read the posterior draws and the parameter names of their columns.

    Column 0 of a bumps ``-point.mc.gz`` is the log-likelihood; the parameter
    columns follow in the order ``-err.json`` records as ``index``. That
    ordering is the file's own and must not be re-derived --- reading it wrong
    silently correlates the wrong quantities.

    Args:
        fit_dir: The result directory.

    Returns:
        ``(draws, names)``, or ``(None, [])`` when there is no chain.
    """
    import numpy as np

    stats = read_uncertainty(fit_dir)
    if not stats:
        return None, []
    names = sorted(
        (n for n in stats if isinstance(stats[n].get("index"), int)),
        key=lambda n: stats[n]["index"],
    )
    if not names:
        return None, []

    chain = next(iter(sorted((fit_dir / "fit").glob("*-point.mc.gz"))), None)
    if chain is None:
        return None, []
    try:
        draws = np.loadtxt(chain)
    except (OSError, ValueError):
        return None, []
    if draws.ndim != 2 or draws.shape[1] < len(names) + 1:
        return None, []

    columns = draws[:, 1 : 1 + len(names)]
    if len(columns) > CORRELATION_SAMPLE_ROWS:
        # Stride rather than truncate: the head of a chain is not the
        # posterior, and a correlation from it would be the burn-in's.
        stride = len(columns) // CORRELATION_SAMPLE_ROWS + 1
        columns = columns[::stride]
    return columns, names


def _correlation_findings(fit_dir: Path) -> list[Finding]:
    """Parameter pairs the data cannot separate.

    A strong correlation is not a defect --- it is the honest shape of the
    posterior --- but it changes what may be quoted. Two parameters at r = 0.94
    have one measured combination between them, and reporting each with its own
    interval claims two measurements where there was one.
    """
    import numpy as np

    draws, names = read_chain(fit_dir)
    if draws is None or len(names) < 2 or len(draws) < 3:
        return []

    with np.errstate(invalid="ignore", divide="ignore"):
        matrix = np.corrcoef(draws, rowvar=False)
    if not np.ndim(matrix):
        return []

    pairs = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            r = float(matrix[i][j])
            if np.isfinite(r) and abs(r) >= CORRELATION_THRESHOLD:
                pairs.append((abs(r), r, names[i], names[j]))
    if not pairs:
        return []

    pairs.sort(reverse=True)
    shown = pairs[:MAX_PAIRS_REPORTED]
    listing = "; ".join(f"{a} <-> {b} (r={r:+.2f})" for _, r, a, b in shown)
    remainder = (
        f" and {len(pairs) - len(shown)} more pair(s) above {CORRELATION_THRESHOLD}"
        if len(pairs) > len(shown)
        else ""
    )
    return [
        Finding(
            kind="correlated",
            # Information, not a defect. Every DREAM fit in the real corpus --
            # all 19 with a chain -- has pairs above this threshold, because
            # correlation is the honest shape of a reflectometry posterior. A
            # warning that fires on everything discriminates nothing; what it
            # changes is what may be *quoted*, which is a judgement no check
            # can make.
            severity="info",
            parameter=shown[0][2],
            message=(
                f"{len(pairs)} parameter pair(s) are correlated above "
                f"{CORRELATION_THRESHOLD}: {listing}{remainder}. Each pair has "
                "one measured combination between them, so quoting both with "
                "their own intervals claims more measurements than were made."
            ),
        )
    ]


def _attainment_findings(fit_dir: Path) -> list[Finding]:
    """Layers the fit dissolved into their own interfaces.

    A slab whose two roughnesses sum to more than its thickness is not a slab:
    it is two overlapping error functions, its nominal SLD occurs nowhere in
    the structure, and the value reported for it describes a shape that is not
    there. Chi-squared cannot see it --- the analyst wrote the check as a
    manual TODO in his own spec header --- but the exported layer table can.

    Tested on the *fitted* table rather than the profile curve, because a
    profile passes through every intermediate value on its way between layers,
    so asking "is this SLD reached anywhere" is nearly always yes. The
    criterion the real analysis derived is arithmetic:
    ``sigma_top + sigma_bot`` against ``t``.

    Per state, because that is how it presented: OCV1's oxide was swallowed
    (21.3 A of layer between interfaces summing to 33.0) while OCV2's, at
    48.3 A, was fine.
    """
    from nr_workbench.web.trajectory import read_slabs

    layers = _stack_layers(fit_dir)
    by_index = _model_names(fit_dir)

    findings: list[Finding] = []
    seen: set[str] = set()
    for path in sorted((fit_dir / "fit").glob("*-slabs.dat")):
        model = by_index.get(_slab_index(path), "")
        state = model.split("#", 1)[0] if model else ""
        try:
            slabs = read_slabs(path)
        except (OSError, ValueError):
            continue
        if len(slabs) < 3:
            continue

        for row, slab in enumerate(slabs):
            # Row 0 is the ambient and the last is the substrate; neither has
            # a thickness to be swallowed.
            if row in {0, len(slabs) - 1}:
                continue
            name = layers[row - 1] if row - 1 < len(layers) else f"layer {row}"
            key = f"{state} {name}".strip()
            if key in seen:
                continue

            # `read_slabs` renames bumps' `interface` column to `roughness`;
            # reading the original name silently yields zero and the check
            # never fires.
            thickness = float(slab.get("thickness", 0.0))
            top = float(slabs[row - 1].get("roughness", 0.0))
            bottom = float(slab.get("roughness", 0.0))
            if thickness <= 0 or top + bottom <= thickness:
                continue

            seen.add(key)
            findings.append(
                Finding(
                    kind="layer-swallowed",
                    severity="warn",
                    parameter=key,
                    value=thickness,
                    message=(
                        f"{name} fitted to {thickness:.4g} A with interfaces of "
                        f"{top:.4g} and {bottom:.4g} A, which sum to "
                        f"{top + bottom:.4g} -- {(top + bottom) / thickness:.2f}x "
                        "its own thickness. It is two overlapping error "
                        "functions rather than a slab, so its SLD occurs "
                        "nowhere in the structure and the value reported for "
                        "it describes a shape that is not there."
                    ),
                )
            )
    return findings


def _stack_layers(fit_dir: Path) -> list[str]:
    """The named layers between the ambient and the substrate."""
    stack = _frozen_spec(fit_dir).get("stack") or []
    return [
        str(layer.get("name"))
        for index, layer in enumerate(stack)
        if isinstance(layer, dict) and index not in {0, len(stack) - 1}
    ]


def _frozen_spec(fit_dir: Path) -> dict[str, Any]:
    """The spec frozen into the fit directory, or an empty mapping."""
    path = fit_dir / "spec.yaml"
    if not path.is_file():
        return {}
    try:
        import yaml

        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - a spec we cannot read is not a finding
        return {}
    return payload if isinstance(payload, dict) else {}


def _model_names(fit_dir: Path) -> dict[int, str]:
    """Export index to model name, from the fit's manifest."""
    try:
        manifest = json.loads((fit_dir / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    models = (manifest.get("info") or {}).get("models") or []
    return {
        int(m["index"]): str(m.get("name", ""))
        for m in models
        if isinstance(m, dict) and isinstance(m.get("index"), int)
    }


def _slab_index(path: Path) -> int:
    """The export position in ``<stem>-<i>-slabs.dat``, or -1."""
    match = re.search(r"-(\d+)-slabs\.dat$", path.name)
    return int(match.group(1)) if match else -1


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
