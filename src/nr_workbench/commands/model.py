"""``nrw model`` -- validate, preview, and generate fit scripts from a spec."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import click

from nr_workbench.project.layout import ProjectLayout, ProjectNotFoundError


def _layout(start: Path | None = None) -> ProjectLayout:
    """Discover the project, or fail with guidance."""
    try:
        return ProjectLayout.discover(start)
    except ProjectNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc


def _load(spec_path: str):
    """Load a spec and its project, reporting problems clearly."""
    from nr_workbench.spec.models import SpecError, load_spec

    path = Path(spec_path).resolve()
    layout = _layout(path.parent)
    try:
        return layout, path, load_spec(path)
    except SpecError as exc:
        raise click.ClickException(str(exc)) from exc


def _spec_sha256(path: Path) -> str:
    """Digest of a spec file, as recorded in a generated script's header."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_validate(
    *, spec: str, as_json: bool = False, result_out: str | None = None
) -> None:
    """Check a spec against the project on disk.

    Args:
        spec: Path to the spec YAML.
        as_json: Emit machine-readable JSON.
        result_out: Write an ``ndip-tool-result/1`` manifest here.

    Raises:
        SystemExit: With code 1 when the spec has errors.
    """
    from nr_workbench.spec.validate import validate_spec

    layout, path, model = _load(spec)
    report = validate_spec(model, layout.root)

    if as_json:
        click.echo(
            json.dumps(
                {"spec": str(path), "ok": report.ok, **report.as_dict()}, indent=2
            )
        )
    else:
        click.echo(f"{path.relative_to(layout.root)}  [{model.name}]")
        for line in report.info:
            click.echo(f"  info     {line}")
        for line in report.warnings:
            click.echo(f"  warning  {line}")
        for line in report.errors:
            click.echo(f"  ERROR    {line}")
        click.echo("  ok" if report.ok else f"\n  {len(report.errors)} error(s)")

    if result_out:
        from nr_workbench._vendor.result_manifest import write_manifest

        write_manifest(
            result_out,
            "nrw-model-validate",
            "ok" if report.ok else "failed",
            params={"spec": str(path)},
            info={"errors": len(report.errors), "warnings": len(report.warnings)},
            exit_code=0 if report.ok else 1,
        )

    if not report.ok:
        raise SystemExit(1)


def run_preview(*, spec: str, as_json: bool = False, build: bool = False) -> None:
    """Show the parameter table a spec resolves to, without writing anything.

    The real gate before a long fit: it says how many parameters will vary and
    how they are grouped, so a mistake costs a second rather than an hour of
    DREAM.

    Args:
        spec: Path to the spec YAML.
        as_json: Emit machine-readable JSON.
        build: Also construct the refl1d problem and report the initial chisq.

    Raises:
        click.ClickException: If the spec cannot be resolved.
    """
    from nr_workbench.spec.models import SpecError
    from nr_workbench.spec.resolve import build_table, discover_measurements

    layout, path, model = _load(spec)
    try:
        table = build_table(model, discover_measurements(model, layout.root))
    except SpecError as exc:
        raise click.ClickException(str(exc)) from exc

    payload: dict[str, Any] = {
        "spec": str(path),
        "model": model.name,
        "experiments": table.n_experiments,
        "free_parameters": table.n_free,
        "constrained_values": len(table.expressions),
        "groups": {
            group: [
                {"index": m.index, "file": m.file, "theta": m.theta, "time": m.time}
                for m in measurements
            ]
            for group, measurements in table.measurements.items()
        },
        "parameters": [
            {
                "key": p.key,
                "name": p.display,
                "value": p.value,
                "bounds": list(p.bounds) if p.bounds else None,
                "fixed": p.fixed,
            }
            for p in table.free
        ],
        "warnings": table.warnings,
    }

    if build:
        payload.update(_build_problem(table, layout.root))

    if as_json:
        click.echo(json.dumps(payload, indent=2))
        return

    click.echo(f"{model.name}  ({path.name})")
    click.echo(
        f"  {table.n_experiments} experiment(s) in {len(table.measurements)} group(s)"
    )
    for group, measurements in table.measurements.items():
        times = [m.time for m in measurements if m.time is not None]
        span = f", t = {min(times):g}..{max(times):g} s" if times else ""
        click.echo(f"    {group:<12} {len(measurements):>3} measurement(s){span}")

    click.echo(
        f"\n  {table.n_free} free parameter(s), {len(table.expressions)} constrained"
    )
    width = max((len(p.key) for p in table.free), default=4)
    for parameter in table.free:
        if parameter.fixed:
            click.echo(
                f"    {parameter.key:<{width}}  {parameter.value:>10.4g}   fixed"
            )
        else:
            low, high = parameter.bounds or (float("nan"), float("nan"))
            click.echo(
                f"    {parameter.key:<{width}}  {parameter.value:>10.4g}   [{low:g}, {high:g}]"
            )

    for warning in table.warnings:
        click.echo(f"\n  warning  {warning}")

    if build:
        click.echo()
        if "error" in payload:
            click.echo(f"  build FAILED: {payload['error']}")
        else:
            click.echo(
                f"  built ok: chisq {payload['chisq']:.6g}, "
                f"{payload['n_points']} data point(s)"
            )


def _build_problem(table, root: Path) -> dict[str, Any]:
    """Generate, execute, and report on the problem, without writing a script."""
    import tempfile

    from nr_workbench.codegen.generator import generate

    source = generate(table)
    with tempfile.TemporaryDirectory() as tmp:
        # Executed at the depth the generated script expects, so PROJECT_ROOT
        # resolves to the real project and its relative data paths work.
        staging = Path(tmp) / "samples" / "_preview" / "models"
        staging.mkdir(parents=True)
        script = staging / "preview.py"
        script.write_text(source, encoding="utf-8")
        for entry in root.iterdir():
            link = Path(tmp) / entry.name
            if not link.exists():
                link.symlink_to(entry)

        from nr_workbench.fitting.runner import FitError, load_problem

        try:
            loaded = load_problem(script, root=root)
        except FitError as exc:
            return {"error": str(exc)}

        problem = loaded.problem
        try:
            return {
                "chisq": float(problem.chisq()),
                "n_free_actual": len(problem.getp()),
                "n_points": int(sum(len(e.probe.Q) for e in problem.models)),
            }
        except Exception as exc:  # pragma: no cover - bumps API drift
            return {"error": f"{type(exc).__name__}: {exc}"}


def run_generate(*, spec: str, out: str | None = None, force: bool = False) -> None:
    """Write the refl1d script for a spec.

    Args:
        spec: Path to the spec YAML.
        out: Output path. Defaults to the spec's path with a ``.py`` suffix.
        force: Overwrite a script that has been hand-edited.

    Raises:
        click.ClickException: If the spec is invalid or the target was edited.
    """
    from nr_workbench.codegen.generator import generate, verify_self_hash
    from nr_workbench.provenance.env import package_version
    from nr_workbench.spec.models import SpecError
    from nr_workbench.spec.resolve import build_table, discover_measurements
    from nr_workbench.spec.validate import validate_spec

    layout, path, model = _load(spec)

    report = validate_spec(model, layout.root)
    if not report.ok:
        for line in report.errors:
            click.echo(f"  ERROR  {line}", err=True)
        raise click.ClickException("spec is not valid; nothing generated")

    try:
        table = build_table(model, discover_measurements(model, layout.root))
    except SpecError as exc:
        raise click.ClickException(str(exc)) from exc

    target = Path(out) if out else path.with_suffix(".py")
    if target.exists() and not force:
        existing = target.read_text(encoding="utf-8")
        if existing.startswith("# ---") and not verify_self_hash(existing):
            raise click.ClickException(
                f"{target.name} has been edited by hand since it was generated.\n"
                "Overwriting would discard those edits. Either re-apply them to the "
                "spec, or run `nrw model fork` to take ownership of the script with "
                "its provenance intact. Use --force to overwrite anyway."
            )

    versions = {
        name: version
        for name in ("refl1d", "bumps", "numpy", "nr-workbench")
        if (version := package_version(name))
    }
    source = generate(
        table,
        spec_path=path.relative_to(layout.root),
        spec_sha256=_spec_sha256(path),
        versions=versions,
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source, encoding="utf-8")

    click.echo(f"Wrote {target.relative_to(layout.root)}")
    click.echo(
        f"  {table.n_experiments} experiment(s), {table.n_free} free parameter(s), "
        f"{len(table.expressions)} constrained"
    )
    for warning in report.warnings:
        click.echo(f"  warning  {warning}")
    click.echo(f"\n  nrw fit run {target.relative_to(layout.root)}")


def run_schema(*, out: str | None = None) -> None:
    """Emit the JSON Schema for ``nrw-model/1``.

    Args:
        out: Where to write it. Defaults to the project's
            ``.nrw/schema/nrw-model-1.json``; ``-`` prints to stdout.
    """
    from nr_workbench.spec.schema import build_schema, write_schema

    if out == "-":
        click.echo(json.dumps(build_schema(), indent=2))
        return

    if out is None:
        layout = _layout()
        target = layout.schema_dir / "nrw-model-1.json"
    else:
        target = Path(out)

    write_schema(target)
    click.echo(f"Wrote {target}")


def run_forms() -> None:
    """List the available constraint forms."""
    from nr_workbench.spec.constraints import describe_forms

    rows = describe_forms()
    width = max(len(name) for name, _ in rows)
    for name, summary in rows:
        click.echo(f"  {name:<{width}}  {summary}")
