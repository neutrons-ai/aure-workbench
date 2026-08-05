"""Build a synthetic time-resolved run for testing.

Shared by the characterization tests and the golden-capture script, so both
see byte-identical input. Everything is seeded and timestamped from a fixed
epoch: the whole point is that regenerating the fixture must produce exactly
the same files.

The run is shaped like a real one: a leading block of short ``hold`` intervals
that serves as the reference, then alternating ``hold`` and longer ``eis``
intervals while the sample changes. The change is injected as a known
amplitude trajectory times a known Q template, which lets the metric tests
assert recovery rather than merely "it ran".
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

#: Fixed so timestamps in the reduction JSON never move.
EPOCH = datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)

RUN_NUMBER = 999001
N_Q = 80
Q_MIN, Q_MAX = 0.012, 0.055

#: Leading holds that form the reference block.
N_REFERENCE = 8
#: Interval pairs after the reference block; each is one hold plus one eis.
N_PAIRS = 12

HOLD_SECONDS = 30.0
EIS_SECONDS = 85.0

#: Relative uncertainty per interval type. The eis intervals count roughly 3x
#: longer, so their errors are smaller -- this asymmetry is exactly what makes
#: a chi-squared comparison between the two types misleading, and what the
#: amplitude metric exists to correct.
HOLD_REL_ERR = 0.141
EIS_REL_ERR = 0.085


def true_template(q: np.ndarray) -> np.ndarray:
    """The fractional-change shape injected into the data.

    Oscillatory in Q, which is the signature of a thickness change: an
    assessment should classify it as such rather than as a contrast change.
    """
    return 0.30 * np.cos(2.0 * np.pi * q / 0.018)


def true_amplitude(t: float, t_total: float) -> float:
    """A sigmoidal trajectory: induction period, rise, then plateau.

    Matches the behaviour ``docs/tnr-amplitude.md`` reports for run 218389,
    where the shape is the scientifically interesting part and a running
    chi-squared cannot see it.
    """
    midpoint = 0.45 * t_total
    width = 0.10 * t_total
    return float(1.0 / (1.0 + np.exp(-(t - midpoint) / width)))


def build(directory: Path, *, seed: int = 20260301) -> Path:
    """Write a complete synthetic run into ``directory``.

    Args:
        directory: Where to write the slices and the reduction JSON.
        seed: Seed for the noise realisation.

    Returns:
        The path to the reduction JSON.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)

    q = np.linspace(Q_MIN, Q_MAX, N_Q)
    # A plausible falling reflectivity, so R spans several decades as real data
    # does and the relative-error handling gets exercised.
    r0 = 2.0e-4 * (Q_MIN / q) ** 4
    template = true_template(q)

    plan: list[tuple[str, str, float]] = [
        (f"hold_initial_{i}", "hold", HOLD_SECONDS) for i in range(N_REFERENCE)
    ]
    for pair in range(N_PAIRS):
        plan.append((f"hold_step{pair}", "hold", HOLD_SECONDS))
        plan.append((f"eis_step{pair}", "eis", EIS_SECONDS))

    total_seconds = sum(duration for _, _, duration in plan)

    intervals = []
    elapsed = 0.0
    for label, itype, duration in plan:
        centre = elapsed + duration / 2.0
        amplitude = true_amplitude(centre, total_seconds)
        rel_err = HOLD_REL_ERR if itype == "hold" else EIS_REL_ERR

        clean = r0 * (1.0 + amplitude * template)
        dr = rel_err * clean
        observed = clean + dr * rng.standard_normal(N_Q)

        rows = "\n".join(
            f"{qi:.8e} {ri:.8e} {dri:.8e}"
            for qi, ri, dri in zip(q, observed, dr, strict=True)
        )
        (directory / f"r{RUN_NUMBER}_{label}.txt").write_text(
            rows + "\n", encoding="utf-8"
        )

        start = EPOCH + timedelta(seconds=elapsed)
        end = start + timedelta(seconds=duration)
        intervals.append(
            {
                "label": label,
                "interval_type": itype,
                "start": start.isoformat(),
                "end": end.isoformat(),
            }
        )
        elapsed += duration

    reduction = {
        "run_number": RUN_NUMBER,
        "duration": total_seconds,
        "n_intervals": len(intervals),
        "intervals": intervals,
    }
    json_path = directory / f"r{RUN_NUMBER}_eis_reduction.json"
    json_path.write_text(json.dumps(reduction, indent=2) + "\n", encoding="utf-8")
    return json_path


def expected_amplitudes() -> np.ndarray:
    """The amplitude trajectory that was injected, per interval.

    Returns:
        One value per interval, in file order.
    """
    plan = [HOLD_SECONDS] * N_REFERENCE
    for _ in range(N_PAIRS):
        plan.extend([HOLD_SECONDS, EIS_SECONDS])

    total = sum(plan)
    values = []
    elapsed = 0.0
    for duration in plan:
        values.append(true_amplitude(elapsed + duration / 2.0, total))
        elapsed += duration
    return np.array(values)


if __name__ == "__main__":  # pragma: no cover
    import sys

    target = Path(sys.argv[1] if len(sys.argv) > 1 else "tnr-fixture")
    print(build(target))
