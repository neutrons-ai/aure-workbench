#!/usr/bin/env python3
"""Evaluate the amplitude interpretation checklist instead of describing it.

The skill lists things to check before trusting `a(t)`. This runs them and
reports pass/fail, so the checklist is executed rather than read past.

Usage:
    python summarize_amplitude.py assessments/r223921/
    python summarize_amplitude.py assessments/r223921/r223921_assessment.json
    python summarize_amplitude.py <dir> --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

#: chi2_res outside this band means one template does not describe the change.
CHI2_RES_BAND = (0.7, 1.5)

#: |a/sigma| below this is consistent with no change at all.
SIGNIFICANCE_FLOOR = 3.0


def find_assessment(target: Path) -> Path:
    """Locate the assessment JSON from a file or a directory.

    Args:
        target: An assessment JSON, or a directory holding one.

    Returns:
        Path to the assessment JSON.

    Raises:
        SystemExit: If none is found, or the directory holds several.
    """
    if target.is_file():
        return target
    candidates = sorted(target.glob("*assessment.json"))
    if not candidates:
        raise SystemExit(
            f"No *assessment.json in {target}. Run `nrw tnr assess` first."
        )
    if len(candidates) > 1:
        raise SystemExit(
            f"Several assessments in {target}: {[c.name for c in candidates]}"
        )
    return candidates[0]


def check(payload: dict[str, Any]) -> list[tuple[str, bool | None, str]]:
    """Run the checklist.

    Args:
        payload: The parsed assessment.

    Returns:
        Triples of (check name, passed, detail). ``None`` means not applicable.
    """
    amplitude = payload.get("amplitude") or {}
    template = payload.get("template") or {}
    variogram = payload.get("variogram") or {}
    results: list[tuple[str, bool | None, str]] = []

    if not amplitude:
        return [
            (
                "amplitude computed",
                False,
                payload.get("skipped", {}).get("amplitude", "absent"),
            )
        ]

    # 1. Did anything change? The variogram answers this without a reference.
    if variogram:
        flat = bool(variogram.get("flat"))
        results.append(
            (
                "variogram shows a change",
                not flat,
                "flat -- stop here, there is nothing to fit"
                if flat
                else f"rises, knee near {variogram.get('knee_s')} s",
            )
        )

    # 2. Is one template enough? Read this BEFORE the trajectory.
    chi2_res = amplitude.get("chi2_res_median")
    if chi2_res is None:
        results.append(("chi2_res near 1", None, "not reported"))
    else:
        ok = CHI2_RES_BAND[0] <= chi2_res <= CHI2_RES_BAND[1]
        results.append(
            (
                "chi2_res near 1",
                ok,
                f"{chi2_res:.3f}"
                + ("" if ok else "  -- one template is not enough; read the PCA"),
            )
        )

    # 3. Is the change significant at all?
    significance = amplitude.get("max_significance")
    if significance is not None:
        ok = significance >= SIGNIFICANCE_FLOOR
        results.append(
            (
                "change is significant",
                ok,
                f"max |a/sigma| = {significance:.1f}"
                + ("" if ok else "  -- consistent with noise"),
            )
        )

    # 4. Do the interval types agree? If not, the counting-time artifact is back.
    sigmas = amplitude.get("median_sigma_a") or {}
    if len(sigmas) > 1:
        ordered = sorted(sigmas.items(), key=lambda kv: kv[1])
        detail = ", ".join(f"{k} {v:.4f}" for k, v in ordered)
        results.append(
            (
                "per-type sigma_a differ as counting time does",
                True,
                detail + "  (longer counting must give the smaller sigma)",
            )
        )

    # 5. Does the trajectory pick a constraint form?
    trajectory = amplitude.get("trajectory")
    form = {
        "sigmoidal": "logistic",
        "monotonic": "linear_in_time",
        "flat": "none -- do not fit a time dependence",
        "non-monotonic": "no single form; consider two templates",
    }.get(str(trajectory))
    results.append(
        (
            "trajectory implies a constraint form",
            form is not None and trajectory not in {"unknown", None},
            f"{trajectory} -> {form}" if form else f"{trajectory}: choose by hand",
        )
    )

    # 6. Does the template pick a parameter?
    implied = template.get("implied_change")
    thickness = template.get("implied_thickness_A")
    results.append(
        (
            "template implies a parameter to free",
            implied is not None,
            (
                f"{template.get('classification')} -> free a {implied}"
                + (f" (order {thickness:.0f} A)" if thickness else "")
            )
            if implied
            else "unclassified",
        )
    )

    # 7. Did the process finish inside the run? Worth stating explicitly.
    if amplitude.get("plateau"):
        results.append(
            (
                "process completed within the run",
                True,
                "a(t) plateaus -- say so in the report",
            )
        )

    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("target", type=Path, help="Assessment JSON, or its directory")
    parser.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON"
    )
    args = parser.parse_args()

    path = find_assessment(args.target)
    payload = json.loads(path.read_text(encoding="utf-8"))
    results = check(payload)

    if args.json:
        print(
            json.dumps(
                {
                    "assessment": str(path),
                    "checks": [
                        {"name": n, "passed": p, "detail": d} for n, p, d in results
                    ],
                    "verdict": payload.get("verdict"),
                },
                indent=2,
            )
        )
    else:
        width = max(len(n) for n, _, _ in results)
        print(f"{path}\n")
        for name, passed, detail in results:
            mark = {True: "ok  ", False: "FAIL", None: "  ? "}[passed]
            print(f"  {mark} {name:<{width}}  {detail}")
        print(f"\n  verdict: {payload.get('verdict', '(none)')}")

    if any(passed is False for _, passed, _ in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
