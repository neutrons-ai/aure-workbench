"""``nrw model`` -- validate, preview, and generate fit scripts from a spec."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC
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
    """Generate, execute, and report on the problem, without keeping a script.

    Written inside the project's own cache rather than a temp directory. The
    generated script locates the project by walking up to ``nrw.toml``, so it
    has to sit somewhere that walk succeeds -- and staging a parallel tree of
    symlinks to fake that is fragile in exactly the way this check exists to
    catch.
    """
    from nr_workbench.codegen.generator import generate

    scratch = root / ".nrw" / "cache" / "preview"
    scratch.mkdir(parents=True, exist_ok=True)
    script = scratch / "preview.py"
    script.write_text(generate(table), encoding="utf-8")

    try:
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
    finally:
        script.unlink(missing_ok=True)


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


def run_new(
    *, sample: str, name: str, out: str | None = None, force: bool = False
) -> None:
    """Scaffold a spec from what `nrw sample scan` found on disk.

    Produces something that validates and generates immediately, with the
    stack left as a placeholder for the scientist to correct. Starting from a
    working file beats starting from a blank one -- the schema is easier to
    learn by editing than by reading.

    Args:
        sample: The sample to build a spec for.
        name: Model name; also the filename.
        out: Explicit output path.
        force: Overwrite an existing spec.

    Raises:
        click.ClickException: If the sample has no usable data, or the target
            exists and ``force`` was not given.
    """

    from nr_workbench.project.scan import scan_sample

    layout = _layout()
    try:
        found = scan_sample(layout.root, sample)
    except FileNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc

    if not found.steady and not found.series:
        raise click.ClickException(
            f"No data found for {sample!r}. Copy reduced files into "
            f"samples/{sample}/data/steady and data/tnr, then run `nrw sample scan`."
        )

    target = Path(out) if out else layout.sample(sample) / "models" / f"{name}.yaml"
    if target.exists() and not force:
        raise click.ClickException(
            f"{target} already exists. Use --force to overwrite."
        )

    document = _scaffold_document(sample, name, found, layout.root)
    assumed = document.pop("_nrw_assumed_angles", [])
    summed = document.pop("_nrw_summed_series", [])
    target.parent.mkdir(parents=True, exist_ok=True)
    header = (
        f"# yaml-language-server: $schema="
        f"{_schema_relative(layout, target)}\n"
        "#\n"
        "# Scaffolded by `nrw model new` from the data on disk. The stack below is a\n"
        "# PLACEHOLDER -- replace it with the real layers and starting values, then:\n"
        "#\n"
        "#   nrw model validate <this file>\n"
        "#   nrw model preview  <this file>\n"
        "#   nrw model generate <this file>\n"
    )
    target.write_text(header + _emit_spec(document), encoding="utf-8")

    click.echo(f"Wrote {target.relative_to(layout.root)}")
    for run in summed:
        click.echo(
            f"  note  run {run} is in data/steady as a summed dataset and also\n"
            "        as a time-resolved series. Only the series is in the spec --\n"
            "        fitting both would count the same neutrons twice. The summed\n"
            "        file is where the series' incident angle was read from."
        )
    if assumed:
        click.echo(
            f"  ! {len(assumed)} file(s) record no incident angle, so a default\n"
            "    was written. Check `thetas:` against the logbook before fitting --\n"
            "    theta sets the resolution and a wrong one is absorbed silently:\n"
            + "".join(f"      {n}\n" for n in assumed[:6])
            + ("      ...\n" if len(assumed) > 6 else "")
        )
    if assumed:
        click.echo(
            f"  ! {len(assumed)} file(s) record no incident angle, so a default\n"
            "    was written. Check `thetas:` against the logbook before fitting --\n"
            "    theta sets the resolution and a wrong one is absorbed silently:\n"
            + "".join(f"      {n}\n" for n in assumed[:6])
            + ("      ...\n" if len(assumed) > 6 else "")
        )

    click.echo(
        f"  {len(document.get('states', []))} state(s), "
        f"{len(document.get('series', []))} series"
    )
    click.echo("\n  The stack is a placeholder. Edit it, then:")
    click.echo(f"    nrw model validate {target.relative_to(layout.root)}")


#: Fallback angles, used only where a file records none. These are this
#: group's usual REF_L settings, not a measurement -- anything scaffolded from
#: them is flagged so it gets checked rather than trusted.
FALLBACK_THETAS = (0.45, 1.2, 3.5)

#: Fallback for a time-resolved series. Slices carry no header at all, and the
#: angle appears in neither the reduction JSON nor the tNR template, so there
#: is nothing on disk to read. This is the usual setting and must be checked.
TNR_FALLBACK_THETA = 0.6


def _thetas_from_headers(paths: list[Path]) -> tuple[list[float], list[str]]:
    """Read each segment's incident angle from its own file.

    The angle is recorded exactly, in radians, in the ``# Meta:`` JSON block
    REF_L writes at the top of a reduced file. It was previously assumed from a
    hardcoded ``[0.45, 1.2, 3.5]`` truncated to the segment count, which is
    right only for a three-segment measurement at this group's usual settings
    and silently wrong for anything else. theta sets the resolution through
    ``dT = dq/q * tan(theta)``, so a wrong one is absorbed into roughness
    rather than raising.

    Args:
        paths: Segment files, in order.

    Returns:
        ``(thetas, unreadable)`` -- angles in degrees, and the names of any
        files that recorded none and therefore got a fallback.
    """
    from nr_workbench.instrument.header import read_header

    thetas: list[float] = []
    unreadable: list[str] = []
    for index, path in enumerate(paths):
        angle: float | None = None
        try:
            angle = read_header(path).theta
        except Exception:
            angle = None
        if angle is None:
            unreadable.append(path.name)
            angle = FALLBACK_THETAS[min(index, len(FALLBACK_THETAS) - 1)]
        thetas.append(round(float(angle), 4))
    return thetas or [FALLBACK_THETAS[0]], unreadable


def _series_theta(root: Path, found_series, unknown: list[str]) -> float:
    """Resolve a time-resolved series' incident angle.

    The slices carry no header, but the same run is also reduced as a summed
    dataset into ``data/steady``, and that file does. So the angle is on disk,
    one directory across -- which beats the group's usual setting, because
    "usual" is 0.6 and the measured value for run 218389 is 0.5997.
    """
    from nr_workbench.instrument.header import theta_for_run

    if found_series.run is not None:
        steady = root / Path(found_series.directory).parent.parent / "steady"
        theta, source = theta_for_run(steady, found_series.run)
        if theta is not None:
            return round(float(theta), 4)
    unknown.append(f"{Path(found_series.directory).name} (series; no summed dataset)")
    return TNR_FALLBACK_THETA


def _scaffold_document(
    sample: str, name: str, found, root: Path | None = None
) -> dict[str, Any]:
    """Build the scaffolded spec mapping.

    Args:
        sample: Sample identifier.
        name: Model name.
        found: The scan result for this sample.
        root: Project root, needed to read the data-file headers.
    """
    root = Path(root) if root is not None else Path.cwd()
    states = []
    unknown_angles: list[str] = []

    # A time-resolved run is *also* reduced as a summed dataset into
    # data/steady, under the same run number. That file is the sum of the very
    # slices the series contributes, so including both would put the same
    # neutrons into the fit twice -- once whole, once in pieces -- and weight
    # that run roughly double. The series wins; the summed file stays on disk
    # and is still what `_series_theta` reads the angle from.
    series_runs = {s.run for s in found.series if s.run is not None}
    summed_series: list[int] = []

    for run in sorted(found.steady):
        if run in series_runs:
            summed_series.append(run)
            continue
        entry = found.steady[run]
        if entry.partials:
            paths = [root / entry.partials[k] for k in sorted(entry.partials)]
            thetas, missing = _thetas_from_headers(paths)
            unknown_angles.extend(missing)
            states.append(
                {
                    "name": f"run{run}",
                    "run": run,
                    "segments": "auto",
                    "thetas": thetas,
                    "data_dir": str(Path(next(iter(entry.partials.values()))).parent),
                }
            )
        elif entry.combined:
            thetas, missing = _thetas_from_headers([root / entry.combined])
            unknown_angles.extend(missing)
            states.append(
                {
                    "name": f"run{run}",
                    "run": run,
                    "kind": "combined",
                    "segments": "auto",
                    "thetas": thetas,
                    "data_dir": str(Path(entry.combined).parent),
                }
            )

    series = []
    for found_series in found.series:
        block: dict[str, Any] = {
            # A series with no resolvable run number is just "tnr"; the inner
            # fallback already covers that, so there is no outer default.
            "name": f"tnr{found_series.run or ''}",
            "run": found_series.run,
            "reduced_dir": found_series.directory,
            "theta": _series_theta(root, found_series, unknown_angles),
            "time_from": "filename"
            if found_series.kind == "time_binned"
            else "reduction_json",
        }
        if found_series.t_step is not None:
            block["select"] = {
                "t_start": found_series.t_start,
                "t_stop": found_series.t_stop,
                "t_step": found_series.t_step,
            }
        series.append(block)

    document: dict[str, Any] = {
        "schema": "nrw-model/1",
        "name": name,
        "sample": sample,
        "description": f"TODO: describe {sample}.\n",
        # A minimal physically-sensible stack, deliberately obvious as a stub.
        "materials": {
            "Ambient": {"rho": 0.0},
            "Film": {"rho": 4.0},
            "Si": {"rho": 2.07},
        },
        "stack": [
            {"name": "Ambient", "material": "Ambient", "thickness": 0, "roughness": 5},
            {"name": "Film", "material": "Film", "thickness": 100, "roughness": 5},
            {"name": "Si", "material": "Si"},
        ],
        "probe": {"resolution": "angular_only", "dq_is_fwhm": True},
    }
    if states:
        document["states"] = states
    if series:
        document["series"] = series

    # A path the series takes from a constraint must NOT also be declared free
    # there, or resolution rejects it as a double assignment. So structural
    # parameters are scoped to the states, and the constraint owns the series.
    # The scaffold has to validate and generate as written -- one that fails on
    # first contact teaches the pattern backwards.
    state_names = [s["name"] for s in states]
    constrained = bool(series and len(states) >= 2)

    thickness: dict[str, Any] = {
        "path": "Film.thickness",
        "range": [50, 200],
        "per": "state",
    }
    if constrained:
        thickness["in"] = state_names

    document["parameters"] = [
        thickness,
        {"path": "Film.rho", "range": [2, 6], "per": "model"},
        {"path": "probe.intensity", "value": 1.0, "pm": 0.1, "per": "state"},
    ]

    document["_nrw_assumed_angles"] = unknown_angles
    document["_nrw_summed_series"] = summed_series

    if constrained:
        document["constraints"] = [
            {
                "series": series[0]["name"],
                "form": "linear_in_time",
                "from": state_names[0],
                "to": state_names[-1],
                "paths": ["Film.thickness"],
            }
        ]
    elif series:
        # One state cannot anchor an interpolation, so give the series its own
        # value rather than emitting a constraint that would be rejected.
        document["parameters"].append(
            {
                "path": "Film.thickness",
                "range": [50, 200],
                "per": "state",
                "in": [s["name"] for s in series],
            }
        )
    document["fit"] = {"method": "amoeba", "steps": 1000}
    return document


def _schema_relative(layout, target: Path) -> str:
    """Relative path from a spec to the project's JSON Schema."""
    import os

    schema = layout.schema_dir / "nrw-model-1.json"
    return os.path.relpath(schema, target.parent)


def run_fork(*, spec: str, name: str | None = None, out: str | None = None) -> None:
    """Turn a generated script into a hand-owned one, keeping provenance.

    The escape hatch has to live *inside* the provenance system. If taking
    manual control of a script also meant losing the record of what produced a
    result, people would do it anyway and the record would quietly become
    fiction. A fork is legal, cheap, and permanently labelled.

    Args:
        spec: The spec whose generated script should be forked.
        name: Name for the forked script. Defaults to ``<spec>-fork``.
        out: Explicit output path.

    Raises:
        click.ClickException: If the script has not been generated yet, or the
            target already exists.
    """
    from datetime import datetime

    layout, path, model = _load(spec)
    generated = path.with_suffix(".py")
    if not generated.is_file():
        raise click.ClickException(
            f"{generated.name} does not exist yet. Run `nrw model generate {spec}` first."
        )

    fork_name = name or f"{model.name}-fork"
    target = Path(out) if out else generated.with_name(f"{fork_name}.py")
    if target.exists():
        raise click.ClickException(f"{target} already exists. Choose another --name.")

    source = generated.read_text(encoding="utf-8")
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    banner = "\n".join(
        [
            "# " + "-" * 74,
            "# HAND-OWNED SCRIPT -- edit this freely.",
            f"#   forked from: {path.relative_to(layout.root).as_posix()}",
            f"#   spec sha256: {_spec_sha256(path)}  (at the time of the fork)",
            f"#   forked:      {stamp}",
            "#",
            "# `nrw model generate` will not touch this file, and `nrw check` will not",
            "# compare it against the spec. It is yours.",
            "#",
            "# It is still fully tracked: `nrw fit run` records its hash, its inputs and",
            "# the environment exactly as for a generated script, so results stay",
            "# traceable. That is the point of forking rather than editing in place.",
            "# " + "-" * 74,
            "",
        ]
    )

    # Drop the generated header: its self-hash no longer applies, and leaving a
    # stale DO-NOT-EDIT banner on a file the user is meant to edit is worse
    # than having none.
    body = source
    if body.startswith("# ---"):
        marker = "# " + "-" * 74
        end = body.find(marker, body.find(marker) + 1)
        if end != -1:
            body = body[end + len(marker) :].lstrip("\n")

    target.write_text(banner + body, encoding="utf-8")

    click.echo(f"Forked to {target.relative_to(layout.root)}")
    click.echo("  edit it freely; `nrw model generate` will leave it alone")
    click.echo(f"\n  nrw fit run {target.relative_to(layout.root)}")


# --------------------------------------------------------------------------
# Emitting a spec someone will want to edit
# --------------------------------------------------------------------------

#: Section order and the comment introducing each. `yaml.safe_dump` writes
#: every mapping in block style, which turns a five-line stack into twenty and
#: loses the grouping that makes a spec readable. A spec is a file a scientist
#: edits by hand, so it is worth emitting deliberately.
_SECTIONS: tuple[tuple[str, str], ...] = (
    ("schema", ""),
    ("name", ""),
    ("sample", ""),
    ("description", ""),
    (
        "materials",
        "# SLD in 1e-6/A2. `nrw data features <file>` reports the\n"
        "# critical edge and the SLD it implies -- check the top layer.",
    ),
    ("stack", "# ambient -> substrate"),
    ("probe", ""),
    ("states", "# Angles were read from each file's `# Meta:` header."),
    ("series", ""),
    (
        "parameters",
        "# `per:` is the whole parameter-identity system:\n"
        "#   model       one value for the entire problem\n"
        "#   state       one per steady state\n"
        "#   measurement one per angle segment or slice\n"
        "# Angle segments within a state alias automatically.",
    ),
    (
        "constraints",
        "# The endpoints are the steady-state parameters, so a\n"
        "# constraint adds no new free parameters.",
    ),
    ("fit", ""),
)

#: Keys whose list items fit on one line and read better that way.
_FLOW_LISTS = frozenset({"stack", "parameters", "constraints", "states", "series"})


def _emit_spec(document: dict[str, Any]) -> str:
    """Render a spec as YAML a person would be happy to edit.

    Flow style for anything that fits on a line, block style for anything that
    does not, and a comment above each section. The result round-trips through
    ``yaml.safe_load`` unchanged -- there is a test.
    """
    import yaml

    lines: list[str] = []
    for key, comment in _SECTIONS:
        if key not in document:
            continue
        value = document[key]
        if value in ({}, [], None, ""):
            continue
        if lines:
            lines.append("")
        if comment:
            lines.extend(comment.splitlines())

        if isinstance(value, str) and "\n" in value:
            lines.append(f"{key}: >")
            lines.extend(f"  {line}" for line in value.strip().splitlines())
        elif isinstance(value, list):
            lines.append(f"{key}:")
            lines.extend(_emit_list_items(value, key, yaml))
        elif isinstance(value, dict):
            lines.append(f"{key}:")
            lines.extend(_emit_mapping(value, yaml))
        else:
            lines.append(yaml.safe_dump({key: value}, sort_keys=False).rstrip())

    remaining = [k for k in document if k not in dict(_SECTIONS)]
    for key in remaining:
        lines.append("")
        lines.append(yaml.safe_dump({key: document[key]}, sort_keys=False).rstrip())

    return "\n".join(lines) + "\n"


def _emit_list_items(value: list[Any], key: str, yaml: Any) -> list[str]:
    """Render a list, using flow style for short mappings."""
    lines: list[str] = []
    for item in value:
        rendered = _flow(item, yaml) if key in _FLOW_LISTS else None
        if rendered is not None:
            lines.append(f"  - {rendered}")
        else:
            block = yaml.safe_dump([item], sort_keys=False, default_flow_style=False)
            lines.extend(f"  {line}" for line in block.rstrip().splitlines())
    return lines


def _emit_mapping(value: dict[str, Any], yaml: Any) -> list[str]:
    """Render a mapping, using flow style for short nested mappings."""
    lines: list[str] = []
    for name, entry in value.items():
        rendered = _flow(entry, yaml) if isinstance(entry, dict) else None
        if rendered is not None:
            lines.append(f"  {name}: {rendered}")
        else:
            block = yaml.safe_dump({name: entry}, sort_keys=False)
            lines.extend(f"  {line}" for line in block.rstrip().splitlines())
    return lines


#: Longest a flow-style mapping may be before it goes back to block style.
_FLOW_WIDTH = 84


def _flow(item: Any, yaml: Any) -> str | None:
    """Render one mapping in flow style, or ``None`` if it is too long."""
    if not isinstance(item, dict):
        return None
    # A short list of scalars reads fine inline (`range: [50, 200]`); a nested
    # mapping or a list of mappings does not.
    for value in item.values():
        if isinstance(value, dict) and value:
            return None
        if isinstance(value, list) and any(
            isinstance(element, dict | list) for element in value
        ):
            return None
    text = yaml.safe_dump(
        item, sort_keys=False, default_flow_style=True, width=10_000
    ).strip()
    if text.startswith("{") and text.endswith("}") and len(text) <= _FLOW_WIDTH:
        return text
    return None
