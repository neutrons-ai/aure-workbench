"""Compose an AuRE setup document from a sample's files and its prose.

AuRE's "setup YAML" describes one analysis run. Building it here rather than
asking a model to write one keeps the same split ``nrw model new`` already
uses: **facts come from the files, judgement comes from the notes.** The run,
its segment files and their order are read from disk and are exact; the stack,
the ambient medium and the geometry are the scientist's to state, and are read
from ``sample.md``.

Three upstream rules shape the output, all from AuRE's ``docs/launching.md``
§U2 ("one state, several files"):

* A REF_L measurement is **one state with several files** -- the spliced Q
  segments of a single curve share one refl1d ``Sample``, so every layer
  parameter is tied across them automatically.
* **Combined and partial files may not be mixed in one state**, and partials
  must share one set id. The setup-YAML route enforces this; the ad-hoc
  ``aure analyze -d`` route skips the check and will accept a mix, which is
  why this module exists rather than a shell command.
* ``theta_offset`` and ``sample_broadening`` are **partials-only** upstream,
  so they are never emitted for a combined-file state.

Nothing here imports ``aure``. The document is a plain mapping; validating it
against the real loader is :func:`nr_workbench.aure_adapter.validate_setup`,
which is where the import lives.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Ways a sample's notes say the beam arrives through the substrate. Shared
#: with ``commands/model.py``, which needs the same judgement when it corrects
#: a critical-edge estimate -- one copy, because two would drift into two
#: different ideas of what back reflection reads like.
#:
#: Patterns rather than literals, because a plain substring list misses the
#: phrasing scientists actually use: "through the substrate" does not match
#: *"measured through the silicon substrate"*, which is the more natural
#: sentence and names the material. The substrate materials are enumerated
#: rather than matched as any word, so "through the polymer film" stays a
#: description of the sample and not a claim about geometry.
_SUBSTRATE = r"(?:silicon|si|sapphire|al2o3|quartz|glass|germanium|ge|wafer)"

BACK_REFLECTION_PATTERNS = (
    r"back[\s-]*reflection",
    r"back\s+of\s+(?:the\s+)?sample",
    rf"through\s+the\s+(?:{_SUBSTRATE}\s+)?substrate",
    rf"through\s+the\s+{_SUBSTRATE}\b",
    r"from\s+the\s+substrate\s+side",
)

#: Sections of ``sample.md`` that describe the sample itself, in the order they
#: are concatenated into ``sample_description``. ``Description`` is the one
#: that must be filled in; ``Details`` adds composition and environment.
DESCRIPTION_SECTIONS = ("Description", "Details")

#: The section whose prose becomes AuRE's ``hypothesis`` -- what the scientist
#: already suspects, which AuRE folds in as top-ranked candidate structures.
HYPOTHESIS_SECTION = "Fits to perform"

#: Where a sample's AuRE runs live, under ``samples/<id>/``.
AURE_DIR = "aure"


class SetupError(Exception):
    """Raised when a setup cannot be composed from what is on disk."""


@dataclass
class Composed:
    """A setup document and the account of where each part came from.

    Keeping the provenance beside the document is what lets the written file
    say which lines were measured and which were somebody's sentence -- the
    distinction a reader needs before they trust a stack.

    Attributes:
        document: The setup mapping, ready to dump as YAML.
        from_files: One line per fact read off the disk.
        from_notes: One line per field taken from ``sample.md``.
        warnings: Non-fatal problems worth printing.
    """

    document: dict[str, Any]
    from_files: list[str] = field(default_factory=list)
    from_notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def describe_sample(notes: str) -> str:
    """Return the sample description AuRE should be given.

    Args:
        notes: The full text of ``sample.md``.

    Returns:
        ``## Description`` followed by ``## Details``, blank-line separated,
        with the template's own prompt comments stripped. ``""`` when nothing
        has been written under either.
    """
    from nr_workbench.notes import read_section

    parts = [read_section(notes, heading).strip() for heading in DESCRIPTION_SECTIONS]
    return "\n\n".join(part for part in parts if part)


def read_hypothesis(notes: str) -> str:
    """Return what the scientist already suspects, if they wrote it down.

    Args:
        notes: The full text of ``sample.md``.

    Returns:
        The prose under ``## Fits to perform``, or ``""``.
    """
    from nr_workbench.notes import read_section

    return read_section(notes, HYPOTHESIS_SECTION).strip()


def reads_as_back_reflection(notes: str) -> bool:
    """Whether the notes say the beam arrives through the substrate.

    This is not a nicety. refl1d takes the last layer as the incident medium,
    so the geometry decides the stack order; get it backwards and the fit
    converges, reports a chi-squared in the hundreds, and names no cause.

    Args:
        notes: Any prose from the sample.

    Returns:
        True if any of :data:`BACK_REFLECTION_PATTERNS` matches.
    """
    lowered = (notes or "").lower()
    return any(re.search(pattern, lowered) for pattern in BACK_REFLECTION_PATTERNS)


def state_files(measurement: Any) -> tuple[list[str], str]:
    """Choose the files for one state, and say which kind they are.

    Prefers the per-angle partials: they are what the measurement actually is,
    they carry their own incident angle, and only a partials state may declare
    ``theta_offset`` or ``sample_broadening`` upstream. The combined file is
    the fallback for a run reduced only that way.

    Args:
        measurement: A :class:`~nr_workbench.project.scan.SteadyMeasurement`.

    Returns:
        ``(paths, kind)`` where *kind* is ``"partials"`` or ``"combined"``.

    Raises:
        SetupError: If the run has neither partials nor a combined file.
    """
    partials = getattr(measurement, "partials", None) or {}
    if partials:
        return [partials[index] for index in sorted(partials)], "partials"

    combined = getattr(measurement, "combined", None)
    if combined:
        return [combined], "combined"

    run = getattr(measurement, "run", "?")
    raise SetupError(
        f"Run {run} has no reduced files on disk -- neither per-angle partials "
        "nor a combined dataset. Copy the reduced data into "
        "data/steady/ and run `nrw sample scan`."
    )


def choose_run(scan: Any, run: int | None) -> int:
    """Pick the run to fit, or explain why the choice cannot be made here.

    Args:
        scan: A :class:`~nr_workbench.project.scan.ScanResult`.
        run: The run the caller asked for, or ``None`` to infer it.

    Returns:
        The run number to use.

    Raises:
        SetupError: If there is no steady data, the named run is absent, or
            there is more than one and none was named.
    """
    available = sorted(getattr(scan, "steady", {}) or {})
    if not available:
        raise SetupError(
            f"No steady-state data found for {scan.sample!r}. Copy reduced "
            f"files into samples/{scan.sample}/data/steady/ and run "
            f"`nrw sample scan {scan.sample}`."
        )

    if run is not None:
        if run not in available:
            listed = ", ".join(str(number) for number in available)
            raise SetupError(
                f"Run {run} is not in {scan.sample!r}'s steady data. Found: {listed}."
            )
        return run

    if len(available) == 1:
        return available[0]

    listed = ", ".join(str(number) for number in available)
    raise SetupError(
        f"{scan.sample!r} has more than one steady run ({listed}); say which "
        "one with --run. A first fit is one measurement -- co-refining several "
        "conditions is a later step, and a different setup."
    )


def compose(
    *,
    sample: str,
    scan: Any,
    notes: str,
    root: Path,
    run: int | None = None,
    name: str | None = None,
) -> Composed:
    """Build the setup document for one run of one sample.

    Args:
        sample: The sample identifier.
        scan: What ``nrw sample scan`` found, as a ``ScanResult``.
        notes: The full text of ``sample.md``.
        root: The project root, which ``data_dir`` is written relative to.
        run: Which steady run to fit; inferred when the sample has only one.
        name: Name for the run; defaults to ``<sample>-<run>``.

    Returns:
        The document and the account of where each part came from.

    Raises:
        SetupError: If the data or the description is missing.
    """
    chosen = choose_run(scan, run)
    measurement = scan.steady[chosen]
    files, kind = state_files(measurement)

    description = describe_sample(notes)
    if not description:
        raise SetupError(
            f"samples/{sample}/sample.md has nothing under '## Description'.\n"
            "AuRE builds the whole model from that sentence, so it cannot be "
            "guessed from the data. Write three things there:\n"
            "  - the layers, substrate upwards, with rough thicknesses\n"
            "  - what the sample sits in (air, D2O, H2O, dTHF, electrolyte)\n"
            "  - whether the beam enters through the substrate\n"
            'e.g. "50 nm Cu on 5 nm Ti on Si, in dTHF, measured through the '
            'silicon."'
        )

    run_name = name or f"{sample}-{chosen}"
    back_reflection = reads_as_back_reflection(notes)

    # `scan` records every path relative to the project root, while AuRE
    # resolves `data_files` relative to `data_dir` -- itself resolved against
    # the directory holding the setup. Pointing `data_dir` back at the root is
    # what makes the two agree, and it keeps the written file free of the
    # absolute paths this project exists to remove.
    data_dir = os.path.relpath(root, setup_dir(root, sample, run_name))

    state: dict[str, Any] = {
        "name": "state0",
        "data_files": [{"file": f} for f in files],
    }
    if back_reflection:
        state["back_reflection"] = True

    document: dict[str, Any] = {
        "name": run_name,
        "sample_description": description,
        "data_dir": Path(data_dir).as_posix(),
        "states": [state],
    }

    hypothesis = read_hypothesis(notes)
    if hypothesis:
        document["hypothesis"] = hypothesis

    from_files = [
        f"run {chosen}, {len(files)} {kind} file(s), in segment order",
        f"data_dir: {Path(data_dir).as_posix()} (the project root)",
        f"state kind: {kind}"
        + (
            ""
            if kind == "partials"
            else " -- theta_offset and sample_broadening are partials-only upstream"
        ),
    ]
    from_notes = [
        f"sample_description: ## Description + ## Details ({len(description)} chars)"
    ]
    if hypothesis:
        from_notes.append("hypothesis: ## Fits to perform")
    from_notes.append(
        f"back_reflection: {back_reflection} "
        + (
            "-- the notes say the beam arrives through the substrate"
            if back_reflection
            else "-- nothing in the notes says otherwise; check this"
        )
    )

    warnings: list[str] = []
    if not back_reflection:
        warnings.append(
            "back_reflection was not set. A solid/liquid cell measured through "
            "the wafer needs it; without it refl1d takes the ambient as the "
            "incident medium and the fit absorbs the error silently."
        )

    return Composed(
        document=document,
        from_files=from_files,
        from_notes=from_notes,
        warnings=warnings,
    )


def setup_dir(root: Path, sample: str, name: str) -> Path:
    """Return where one AuRE run's files belong.

    Args:
        root: The project root.
        sample: The sample identifier.
        name: The run name.

    Returns:
        ``samples/<sample>/aure/<name>/``.
    """
    return root / "samples" / sample / AURE_DIR / name
