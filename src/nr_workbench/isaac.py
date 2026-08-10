"""Stage a fit as a data-assembler ingest directory, for ISAAC export.

nr-workbench does not map anything to the ISAAC schema. That mapping lives in
``nr-isaac-format``, fed by ``data-assembler``, and reimplementing it here would
be a second copy to keep in step with a schema neither project owns.

What the assembler needs is a *file contract*, not a workflow: a directory
holding ``run_info.json``, the serialised bumps problem, its ``-err.json``
companion, and a ``final_state.json`` carrying chi-squared. Everything in that
contract is already in a fit record, so this module rewrites it into the shape
the assembler reads and lets the canonical pipeline do the rest.

Two things about that contract earn the module.

**Angle segments are one measurement, not three.** A REF_L steady state is
measured at three incident angles and reduced to three files with three run
numbers. They are one physical measurement of one sample, and the assembler
treats every file in a state's ``data_files`` as exactly that -- one ISAAC
record carrying N series. Listing them as separate states would claim three
measurements that never happened. (Getting this wrong is a bug AuRE has already
fixed once.)

**A co-refinement is several states of one sample.** Writing ``states[]``
rather than a flat file list gets each condition its own record, with the whole
co-refinement recorded as one fit spanning them, and the records cross-linked
by ``links[].same_sample_as`` because they share a sample id. That is what
makes three ISAAC records readable as three conditions of one experiment
rather than three unrelated measurements.

The grouping is never inferred from filenames. It comes from the frozen spec,
whose ``Measurement.key`` (``run218386#0``) is the same string the fit recorded
in its manifest -- so what is exported is what was fitted.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Filename the assembler expects for the serialised bumps problem.
PROBLEM_FILENAME = "problem.json"


@dataclass
class StagedState:
    """One physical condition, with the angle segments that measured it.

    Attributes:
        name: The state's name in the spec, e.g. ``run218386``.
        files: Absolute paths to its reduced data, one per angle segment.
        condition: Free text describing the condition, from the spec.
    """

    name: str
    files: list[str]
    condition: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return the ``states[]`` entry the assembler reads."""
        entry: dict[str, Any] = {"name": self.name, "data_files": self.files}
        if self.condition:
            entry["extra_description"] = self.condition
        return entry


@dataclass
class Staged:
    """What was written, and what could not be.

    Attributes:
        directory: The staging directory.
        states: The states written to ``run_info.json``.
        chisq: Chi-squared carried into ``final_state.json``.
        problems: Anything the export will be poorer for.
    """

    directory: Path
    states: list[StagedState]
    chisq: float | None = None
    problems: list[str] = field(default_factory=list)

    @property
    def n_files(self) -> int:
        """Total reduced files across every state."""
        return sum(len(s.files) for s in self.states)


def stage(
    fit_dir: Path,
    root: Path,
    destination: Path,
    *,
    sample_description: str | None = None,
) -> Staged:
    """Write a fit into a directory ``data-assembler ingest-workflow`` can read.

    Args:
        fit_dir: The immutable result directory.
        root: Project root, for resolving the recorded relative paths.
        destination: Directory to create and populate.
        sample_description: Prose for the sample record.

    Returns:
        What was staged.

    Raises:
        FileNotFoundError: If the fit has no serialised problem, without which
            there is no fitted model to export.
    """
    fit_dir, root = Path(fit_dir), Path(root)
    destination.mkdir(parents=True, exist_ok=True)
    problems: list[str] = []

    problem = _problem_json(fit_dir)
    if problem is None:
        raise FileNotFoundError(
            f"No serialised bumps problem in {fit_dir / 'fit'}. ISAAC records "
            "carry the fitted model, so there is nothing to export without it."
        )
    shutil.copy2(problem, destination / PROBLEM_FILENAME)

    # The uncertainty companion. The assembler finds it beside the problem and
    # uses it for per-parameter sigma; without it the record carries values
    # with no error bars, which is worth saying rather than shipping quietly.
    err = next(iter(sorted((fit_dir / "fit").glob("*-err.json"))), None)
    if err is not None:
        shutil.copy2(err, destination / f"{Path(PROBLEM_FILENAME).stem}-err.json")
    else:
        problems.append(
            "No -err.json: this was an optimiser run, so the exported record "
            "will carry fitted values with no uncertainties."
        )

    states = _states(fit_dir, root, problems)
    if not states:
        raise FileNotFoundError(
            f"No reduced data files could be resolved for {fit_dir.name}."
        )

    manifest = _read_json(fit_dir / "manifest.json")
    info = manifest.get("info") or {}
    chisq = info.get("chisq")

    run_info: dict[str, Any] = {
        "run_id": fit_dir.name,
        "states": [s.as_dict() for s in states],
        # The default, stated explicitly: co-refined states are conditions of
        # one physical sample, so they share a sample id and the records come
        # out cross-linked. A co-refinement of genuinely different samples
        # would set this true -- nr-workbench has no way to express that yet,
        # so it is never guessed.
        "distinct_sample": False,
    }
    if sample_description:
        run_info["sample_description"] = sample_description

    _write_json(destination / "run_info.json", run_info)
    _write_json(
        destination / "final_state.json",
        {
            "final_chi2": chisq,
            "state": {
                "states": [
                    {"name": s.name, "extra_description": s.condition or ""}
                    for s in states
                ]
            },
        },
    )
    return Staged(
        directory=destination, states=states, chisq=chisq, problems=problems
    )


def _states(fit_dir: Path, root: Path, problems: list[str]) -> list[StagedState]:
    """Group the fit's data into states, preferring the frozen spec."""
    spec_path = fit_dir / "spec.yaml"
    if spec_path.is_file():
        grouped = _states_from_spec(spec_path, root, problems)
        if grouped:
            return grouped
    return _states_from_inputs(fit_dir, root, problems)


def _states_from_spec(
    spec_path: Path, root: Path, problems: list[str]
) -> list[StagedState]:
    """Group by the spec's states, which is how the fit itself was built.

    ``discover_measurements`` returns the same grouping the generator used, so
    a state's files here are exactly the Experiments that state contributed.
    """
    from nr_workbench.spec.models import SpecError, load_spec
    from nr_workbench.spec.resolve import discover_measurements

    try:
        spec = load_spec(spec_path)
        found = discover_measurements(spec, root)
    except (SpecError, OSError, ValueError) as exc:
        problems.append(
            f"Could not read the frozen spec ({exc}); grouping every file as "
            "one state instead."
        )
        return []

    conditions = {s.name: getattr(s, "condition", None) for s in spec.states}
    for series in getattr(spec, "series", None) or []:
        conditions.setdefault(series.name, getattr(series, "condition", None))

    states = []
    for group, measurements in found.items():
        files = []
        for measurement in measurements:
            path = (root / measurement.file).resolve()
            if path.is_file():
                files.append(str(path))
            else:
                problems.append(f"Recorded data file is missing: {measurement.file}")
        if files:
            states.append(
                StagedState(name=group, files=files, condition=conditions.get(group))
            )
    return states


def _states_from_inputs(
    fit_dir: Path, root: Path, problems: list[str]
) -> list[StagedState]:
    """Fall back to one state holding every recorded data file.

    Used for a hand-written or forked script, which has no spec to group by.
    One state is the honest answer there: the segments still assemble into a
    single measurement, but nothing in the record says which condition each
    file belongs to, and inventing that from filenames would be a guess
    presented as provenance.
    """
    from nr_workbench.provenance.record import FitDirectory

    files = []
    for entry in FitDirectory(fit_dir).read_inputs():
        if entry.get("role") == "script":
            continue
        path = (root / str(entry.get("path", ""))).resolve()
        if path.is_file():
            files.append(str(path))
    if not files:
        return []
    problems.append(
        "No frozen spec, so every data file is exported as one state. A "
        "co-refinement of several conditions will come out as a single "
        "record rather than one per condition."
    )
    return [StagedState(name=fit_dir.name, files=files)]


def _problem_json(fit_dir: Path) -> Path | None:
    """Locate the serialised bumps problem among the fit outputs."""
    for path in sorted((fit_dir / "fit").glob("*.json")):
        if path.name.endswith("-err.json") or "-expt" in path.name:
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and "references" in payload:
            return path
    return None


def _read_json(path: Path) -> dict[str, Any]:
    """Read a JSON object, or return an empty mapping."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write a JSON object."""
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
