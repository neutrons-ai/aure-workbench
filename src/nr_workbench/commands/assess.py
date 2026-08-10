"""``nrw assess`` -- check a finished fit and write what was found.

The command runs the arithmetic checks always, adds a language model's reading
when one is configured, and appends the result to the fit's ``NOTES.md``.

That last step is the point. An assessment printed to a terminal is gone by the
next command; an assessment in the note is in the bundle, on the fit page, and
in front of whoever asks about this fit in a year. It also means the notebook
is never empty: every fit has at least the automatic section, so the human is
adding to a page rather than starting one.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click

from nr_workbench.fitting.assess import Assessment, as_markdown, check
from nr_workbench.notes import NOTES_FILENAME
from nr_workbench.project.layout import ProjectLayout, ProjectNotFoundError
from nr_workbench.provenance.index import FitIndex
from nr_workbench.provenance.lookup import FitNotFoundError, resolve_fit
from nr_workbench.provenance.record import FitDirectory

_SEVERITY_COLOUR = {"blocker": "red", "warn": "yellow", "info": None}


def run_assess(
    *,
    fit_id: str,
    write: bool = True,
    use_llm: bool = True,
    as_json: bool = False,
) -> None:
    """Assess one fit.

    Args:
        fit_id: The fit, or a unique prefix.
        write: Append the assessment to the fit's NOTES.md.
        use_llm: Ask a language model too, when one is configured.
        as_json: Emit machine-readable JSON instead of prose.

    Raises:
        click.ClickException: If there is no project or no such fit.
    """
    try:
        layout = ProjectLayout.discover()
    except ProjectNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc

    index = FitIndex(layout.index_file)
    try:
        entry, directory = resolve_fit(layout, index, fit_id)
    except FitNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc

    manifest = FitDirectory(directory).read_manifest()
    assessment = check(directory, manifest)

    if use_llm:
        _add_judgement(assessment, layout, entry, directory, manifest)

    if as_json:
        click.echo(json.dumps(assessment.as_dict(), indent=2, default=str))
        return

    _print(assessment)

    if write:
        path = directory / NOTES_FILENAME
        if not path.is_file():
            FitDirectory(directory).write_notes_stub(fit_id=assessment.fit_id)
        with path.open("a", encoding="utf-8") as handle:
            handle.write("\n" + as_markdown(assessment))
        click.echo()
        click.echo(f"  written to {path.relative_to(layout.root)}")


def _add_judgement(
    assessment: Assessment,
    layout: ProjectLayout,
    entry: dict[str, Any],
    directory: Path,
    manifest: dict[str, Any],
) -> None:
    """Attach a language model's reading, or record why there is none."""
    from nr_workbench.aure_adapter import AureUnavailableError, llm_available

    if not llm_available():
        assessment.problems.append(
            "No language-model endpoint configured, so nothing judged whether "
            "these values are physically sensible. Set LLM_PROVIDER and "
            "LLM_API_KEY in .env; `nrw doctor` reports what it sees."
        )
        return

    from nr_workbench.aure_adapter import judge_fit
    from nr_workbench.fitting.assess import read_par

    if not isinstance(assessment.chisq, int | float):
        assessment.problems.append(
            "No chi-squared recorded, so the fit could not be judged."
        )
        return

    sample = entry.get("sample")
    description = _sample_description(layout, sample, directory)

    try:
        verdict = judge_fit(
            chisq=float(assessment.chisq),
            method=str((manifest.get("params") or {}).get("method") or "unknown"),
            parameters=read_par(directory),
            sample_description=description,
            converged=(manifest.get("info") or {}).get("converged"),
            skill_context=_skill_context(layout),
            boundary_hits=_boundary_hits(assessment),
            bic=assessment.bic,
            n_params=assessment.n_free or 0,
            n_layers=_layer_count(directory),
        )
    except AureUnavailableError as exc:
        assessment.problems.append(str(exc))
        return

    assessment.judgement = verdict


def _boundary_hits(assessment: Assessment) -> list[dict[str, Any]]:
    """Re-shape our bound findings into what AuRE's prompt formatter wants.

    AuRE renders each hit with ``:.4f`` on ``value`` and ``bound_value``, so
    every key has to be present and numeric or the prompt raises.
    """
    hits = []
    for finding in assessment.findings:
        if finding.kind != "bound" or not finding.parameter:
            continue
        if finding.value is None or finding.limit is None:
            continue
        hits.append(
            {
                "name": finding.parameter,
                "value": float(finding.value),
                "bound_hit": finding.edge or "lower",
                "bound_value": float(finding.limit),
            }
        )
    return hits


def _sample_description(layout: ProjectLayout, sample: Any, directory: Path) -> str:
    """The prose a model needs to judge whether parameters make sense."""
    parts = []
    if sample:
        path = layout.sample(str(sample)) / "sample.md"
        if path.is_file():
            parts.append(path.read_text(encoding="utf-8"))
    spec = directory / "spec.yaml"
    if spec.is_file():
        parts.append("Model spec:\n" + spec.read_text(encoding="utf-8"))
    return "\n\n".join(parts) or "(not provided)"


def _skill_context(layout: ProjectLayout) -> str:
    """Concatenate the installed domain skills, the prompt's physics grounding.

    Without this the model has chi-squared and a list of numbers, which is
    exactly the situation where it invents plausible physics.
    """
    directory = layout.skills_dir
    if not directory.is_dir():
        return ""
    bodies = []
    for path in sorted(directory.glob("*/*/SKILL.md")):
        try:
            bodies.append(path.read_text(encoding="utf-8"))
        except OSError:
            continue
    return "\n\n---\n\n".join(bodies)


def _layer_count(directory: Path) -> int:
    """Layers in the frozen spec, or zero."""
    spec = directory / "spec.yaml"
    if not spec.is_file():
        return 0
    try:
        import yaml

        payload = yaml.safe_load(spec.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - a count is not worth failing over
        return 0
    stack = (payload or {}).get("stack") if isinstance(payload, dict) else None
    return len(stack) if isinstance(stack, list) else 0


def _print(assessment: Assessment) -> None:
    """Print the assessment."""
    click.echo(f"  fit       {assessment.fit_id}")
    if assessment.chisq is not None:
        click.echo(
            f"  chisq     {assessment.chisq:.4g}"
            + (f"   BIC {assessment.bic:.1f}" if assessment.bic is not None else "")
        )
    click.echo(f"  free      {assessment.n_free}   points {assessment.n_points}")
    click.echo()

    if not assessment.findings:
        click.secho("  Nothing flagged by the automatic checks.", fg="green")
    for finding in assessment.findings:
        click.secho(
            f"  {finding.severity:<7} {finding.message}",
            fg=_SEVERITY_COLOUR.get(finding.severity),
        )

    judgement = assessment.judgement
    if judgement:
        click.echo()
        click.echo(f"  model     {judgement.get('quality_assessment', 'unknown')}")
        for key in ("issues", "physical_concerns", "suggestions"):
            for item in judgement.get(key) or []:
                click.echo(f"    {key[:-1] if key.endswith('s') else key}: {item}")

    for problem in assessment.problems:
        click.secho(f"  ! {problem}", dim=True)
