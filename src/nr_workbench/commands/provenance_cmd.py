"""``nrw whence``, ``ls``, ``promote`` and ``check`` -- the query surface.

These four commands are the product. Everything else is plumbing that exists so
these can answer honestly.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import click

from nr_workbench.project.layout import ProjectLayout, ProjectNotFoundError
from nr_workbench.provenance.index import EVENT_PROMOTE, FitIndex
from nr_workbench.provenance.lookup import (
    FitNotFoundError,
    resolve_fit,
)
from nr_workbench.provenance.lookup import fit_dir as find_fit_dir
from nr_workbench.provenance.record import FitDirectory, format_timestamp, utc_now
from nr_workbench.provenance.summary import annotate
from nr_workbench.provenance.whence import (
    Freshness,
    Resolution,
    check_inputs,
    whence,
)

_FRESHNESS_MARK = {
    Freshness.FRESH: "fresh",
    Freshness.STALE: "STALE",
    Freshness.BROKEN: "BROKEN",
    Freshness.UNKNOWN: "-",
}


def _layout() -> ProjectLayout:
    """Discover the project, or fail with guidance."""
    try:
        return ProjectLayout.discover()
    except ProjectNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc


def run_whence(*, path: str, as_json: bool = False) -> None:
    """Trace a path back to the fit that produced or consumed it.

    Args:
        path: A figure, artifact, data file, script, or fit id.
        as_json: Emit machine-readable JSON.

    Raises:
        click.ClickException: If there is no project here.
        SystemExit: With code 1 when nothing could be traced.
    """
    layout = _layout()
    index = FitIndex(layout.index_file)

    target = Path(path)
    if not target.exists():
        # Accept a bare fit id, which is what a user reads off `nrw ls`.
        matches = index.resolve(path)
        if len(matches) == 1:
            resolved = find_fit_dir(layout, matches[0])
            if resolved is not None:
                target = resolved
        elif len(matches) > 1:
            raise click.ClickException(
                f"'{path}' matches {len(matches)} fits: "
                + ", ".join(str(m["fit_id"]) for m in matches[:5])
            )

    result = whence(target, layout.root, index)

    if as_json:
        click.echo(json.dumps(_whence_payload(result), indent=2, default=str))
        raise SystemExit(0 if result.found else 1)

    if not result.found:
        click.echo(f"Nothing recorded for {path}")
        click.echo(
            "  Not inside a fit directory, not stamped, and not a recorded input."
        )
        raise SystemExit(1)

    _print_whence(result)


def _whence_payload(result: Any) -> dict[str, Any]:
    """Build the JSON form of a whence result."""
    return {
        "query": str(result.query),
        "resolution": str(result.resolution),
        "found": result.found,
        "fit_id": result.fit_id,
        "freshness": str(result.freshness),
        "note": result.note,
        "promotion": result.promotion,
        "record": result.record,
        "provenance": (result.manifest or {}).get("provenance"),
        "info": (result.manifest or {}).get("info"),
        "inputs": [
            {
                "role": i.role,
                "path": i.path,
                "recorded_sha256": i.recorded_sha256,
                "current_sha256": i.current_sha256,
                "state": str(i.state),
            }
            for i in result.inputs
        ],
        "consumed_by": result.consumed_by,
    }


def _print_whence(result: Any) -> None:
    """Print a human-readable provenance chain."""
    if result.resolution is Resolution.RECORDED_INPUT:
        click.echo(f"{result.query}")
        click.echo(f"  used as an input by {len(result.consumed_by)} fit(s):")
        for entry in result.consumed_by:
            click.echo(
                f"    {entry['fit_id']}  {entry.get('model', '')}  "
                f"chisq {_format_number(entry.get('chisq'))}"
            )
        click.echo()
        click.echo(
            "  If this file has changed, those fits are stale. `nrw check` will say so."
        )
        return

    manifest = result.manifest or {}
    provenance = manifest.get("provenance", {})
    info = manifest.get("info", {})
    params = manifest.get("params", {})
    env = provenance.get("env", {})
    git = env.get("git", {})

    promoted = ""
    if result.promotion:
        promoted = f"   [{result.promotion.get('label', 'promoted').upper()}]"

    click.echo(f"{result.query}")
    click.echo(f"  -> fit {result.fit_id}{promoted}")
    if result.note:
        click.echo(f"     ({result.note})")
    click.echo()
    click.echo(f"  sample     {provenance.get('sample') or '-'}")
    click.echo(
        f"  model      {provenance.get('model')}  ({provenance.get('script_origin')})"
    )
    click.echo(
        f"  script     model.py  sha256 {_short(provenance.get('identity', {}).get('script_sha256'))}"
    )
    click.echo(
        f"  fit        {params.get('method')}"
        + (f", {params.get('steps')} steps" if params.get("steps") else "")
        + (f", seed {params.get('seed')}" if params.get("seed") is not None else "")
    )
    if info.get("chisq") is not None:
        click.echo(f"  chisq      {info['chisq']:.4g}   free {info.get('n_free')}")
    click.echo(
        f"  ran        {provenance.get('started_at')} -> {provenance.get('finished_at')}"
    )

    versions = ", ".join(f"{k} {v}" for k, v in sorted(env.get("packages", {}).items()))
    click.echo(f"  versions   python {env.get('python')}, {versions}")
    if env.get("aure_commit"):
        click.echo(f"  aure       {_short(env['aure_commit'])}")
    if git.get("available"):
        state = "dirty (patch in env/project.patch)" if git.get("dirty") else "clean"
        click.echo(
            f"  git        {_short(git.get('commit'))} on {git.get('branch') or '?'} -- {state}"
        )

    click.echo()
    click.echo(
        f"  inputs     {len(result.inputs)} file(s), {_FRESHNESS_MARK[result.freshness]}"
    )
    for status in result.inputs:
        mark = " " if not status.changed else "!"
        click.echo(f"   {mark} {status.role:<22} {status.path}")
        if status.changed:
            detail = (
                "missing from disk"
                if status.current_sha256 is None
                else "content changed"
            )
            click.echo(f"       {detail} since the fit ran")

    if result.promotion:
        click.echo()
        click.echo(
            f"  promoted   {result.promotion.get('label')} by {result.promotion.get('who')}"
        )
        click.echo(f"             {result.promotion.get('reason')}")

    click.echo()
    click.echo("  reproduce  " + (provenance.get("command") or "-"))

    if result.freshness is not Freshness.FRESH and result.inputs:
        click.echo()
        click.secho(
            "  This result no longer matches its inputs on disk. Re-run before citing it.",
            fg="red",
        )


def run_ls(
    *, sample: str | None = None, as_json: bool = False, limit: int = 50
) -> None:
    """List recorded fits, newest first.

    Args:
        sample: Restrict to one sample.
        as_json: Emit machine-readable JSON.
        limit: Maximum rows to show.

    Raises:
        click.ClickException: If there is no project here.
    """
    layout = _layout()
    index = FitIndex(layout.index_file)
    # Annotate the whole history, then trim: the change line for the oldest
    # row shown is relative to a fit that may be below the limit.
    rows = annotate(index.fits(sample=sample))[:limit]

    if as_json:
        click.echo(json.dumps(rows, indent=2, default=str))
        return

    if not rows:
        click.echo("No fits recorded yet. Run one with `nrw fit run <script.py>`.")
        return

    documented = _documented(layout, rows)
    promoted = {
        entry.get("fit_id")
        for label in {str(e.get("label")) for e in index.promotions()}
        if (entry := index.current_label(label)) is not None
    }

    width = max(len(str(r.get("fit_id", ""))) for r in rows)
    click.echo(
        f"  {'FIT_ID':<{width}}  {'MODEL':<24} {'STATUS':<8} {'CHISQ':>9}  {'FRESH':<7}"
    )
    for row in rows:
        fit_id = str(row.get("fit_id", ""))
        chisq = row.get("chisq")
        chisq_text = f"{chisq:.4g}" if isinstance(chisq, int | float) else "-"
        star = " *" if fit_id in promoted else ""
        freshness = _freshness_of(layout, index, fit_id)
        click.echo(
            f"  {fit_id:<{width}}  {str(row.get('model', ''))[:24]:<24} "
            f"{str(row.get('status', '')):<8} {chisq_text:>9}  "
            f"{_FRESHNESS_MARK[freshness]:<7}{star}"
        )
        # Dimmed and indented, so the table still scans as a table while every
        # row carries the two things an id cannot say: what this run was, and
        # what you changed to get it.
        click.secho(f"      {row['change']}", fg="cyan", dim=True)
        if row.get("note"):
            click.secho(f"      “{row['description']}”", dim=True)
        written = documented.get(fit_id)
        if written:
            click.secho(f"      ✎ {written}", fg="green", dim=True)

    if promoted:
        click.echo()
        click.echo("  * promoted")
    undocumented = [r for r in rows if not documented.get(str(r.get("fit_id")))]
    if undocumented:
        click.echo()
        click.secho(
            f"  {len(undocumented)} of {len(rows)} fits have nothing written down.",
            dim=True,
        )
        # The hash, not the first 8 characters: every fit from the same day
        # shares its date, so a leading slice is the one part that cannot
        # identify anything.
        first = str(undocumented[0].get("fit_id", ""))
        click.secho(
            f"    nrw note {first.rpartition('-')[2] or first} "
            '-m "what this run showed"',
            dim=True,
        )


def _documented(layout: ProjectLayout, rows: list[dict[str, Any]]) -> dict[str, str]:
    """Say, per fit, what has been written down about it.

    Reading the notebook is what makes writing in it worthwhile. A listing
    that cannot tell a reasoned-about fit from an unexamined one gives no
    reason to reason in public.

    Args:
        layout: The project layout.
        rows: The fit entries being listed.

    Returns:
        Fit id to a short phrase, for fits that have prose.
    """
    from nr_workbench.notes import fit_note, notes_about, sample_notes

    by_sample: dict[str, list[Any]] = {}
    summary: dict[str, str] = {}
    for row in rows:
        fit_id = str(row.get("fit_id", ""))
        sample = row.get("sample")
        directory = find_fit_dir(layout, row)

        parts = []
        if directory is not None:
            own = fit_note(
                layout.root, directory, fit_id, str(sample) if sample else None
            )
            if own is not None and not own.blank:
                parts.append(own.summary or "a note on this fit")

        if sample:
            name = str(sample)
            if name not in by_sample:
                by_sample[name] = sample_notes(layout.root, name)
            related = notes_about(by_sample[name], fit_id)
            if related:
                parts.append(f"in {len(related)} report(s)")

        if parts:
            summary[fit_id] = " · ".join(parts)[:96]
    return summary


def run_promote(*, fit_id: str, label: str, reason: str, force: bool = False) -> None:
    """Mark a fit as the answer, recording who decided and why.

    "Final" is never implicit. The most recent fit is not the answer, the
    lowest chi-squared is not automatically the answer -- a person decides, and
    that decision is itself provenance worth keeping.

    Args:
        fit_id: The fit to promote. A unique prefix is accepted.
        label: The label to apply, e.g. ``final``.
        reason: Why this fit is the answer. Mandatory.
        force: Promote even when the fit's inputs have changed.

    Raises:
        click.ClickException: On an unknown fit, an empty reason, or stale
            inputs without ``--force``.
    """
    layout = _layout()
    index = FitIndex(layout.index_file)

    if not reason.strip():
        raise click.ClickException(
            "A reason is required: it is the part worth keeping."
        )

    matches = index.resolve(fit_id)
    if not matches:
        raise click.ClickException(f"No fit matching '{fit_id}'. See `nrw ls`.")
    if len(matches) > 1:
        raise click.ClickException(
            f"'{fit_id}' matches {len(matches)} fits: "
            + ", ".join(str(m["fit_id"]) for m in matches[:5])
        )

    entry = matches[0]
    resolved_id = str(entry["fit_id"])

    if entry.get("status") != "ok":
        raise click.ClickException(
            f"{resolved_id} has status '{entry.get('status')}'. Only a successful fit can be promoted."
        )

    fit_dir = find_fit_dir(layout, entry)
    if fit_dir is None:
        raise click.ClickException(f"Fit directory for {resolved_id} is missing.")

    _, freshness = check_inputs(fit_dir, layout.root)
    if (
        freshness is not Freshness.FRESH
        and freshness is not Freshness.UNKNOWN
        and not force
    ):
        raise click.ClickException(
            f"{resolved_id} is {_FRESHNESS_MARK[freshness]}: its inputs have changed since it ran.\n"
            "Re-run it, or pass --force to promote it anyway (recorded in the entry)."
        )

    previous = index.current_label(label, sample=entry.get("sample"))
    if previous and previous.get("fit_id") != resolved_id:
        # Superseding is recorded, never erased: what was once considered
        # final is part of the story.
        index.append(
            {
                "fit_id": previous.get("fit_id"),
                "sample": previous.get("sample"),
                "label": label,
                "superseded_by": resolved_id,
                "at": format_timestamp(utc_now()),
            },
            event="supersede",
        )

    index.append(
        {
            "fit_id": resolved_id,
            "sample": entry.get("sample"),
            "model": entry.get("model"),
            "label": label,
            "reason": reason.strip(),
            "who": _current_user(),
            "at": format_timestamp(utc_now()),
            "forced": bool(force) and freshness is not Freshness.FRESH,
            "supersedes": previous.get("fit_id") if previous else None,
        },
        event=EVENT_PROMOTE,
    )

    click.echo(f"Promoted {resolved_id} as '{label}'.")
    if previous and previous.get("fit_id") != resolved_id:
        click.echo(f"  supersedes {previous['fit_id']} (kept in the index)")


def run_check(*, as_json: bool = False) -> None:
    """Verify project integrity: stale results, missing inputs, broken pointers.

    Args:
        as_json: Emit machine-readable JSON.

    Raises:
        click.ClickException: If there is no project here.
        SystemExit: With code 1 when any problem is found.
    """
    layout = _layout()
    index = FitIndex(layout.index_file)
    problems: list[dict[str, str]] = []
    checked = 0

    for entry in index.fits():
        fit_id = str(entry.get("fit_id", ""))
        fit_dir = find_fit_dir(layout, entry)
        if fit_dir is None:
            problems.append(
                {
                    "fit_id": fit_id,
                    "kind": "missing-directory",
                    "detail": "recorded in the index but not on disk",
                }
            )
            continue

        checked += 1
        try:
            FitDirectory(fit_dir).read_manifest()
        except (FileNotFoundError, ValueError):
            problems.append(
                {
                    "fit_id": fit_id,
                    "kind": "unreadable-manifest",
                    "detail": str(fit_dir),
                }
            )
            continue

        statuses, freshness = check_inputs(fit_dir, layout.root)
        if freshness is Freshness.BROKEN:
            for status in statuses:
                if status.current_sha256 is None:
                    problems.append(
                        {
                            "fit_id": fit_id,
                            "kind": "missing-input",
                            "detail": status.path,
                        }
                    )
        elif freshness is Freshness.STALE:
            for status in statuses:
                if status.changed:
                    problems.append(
                        {"fit_id": fit_id, "kind": "stale-input", "detail": status.path}
                    )

        if not [s for s in statuses if s.role != "script"]:
            problems.append(
                {
                    "fit_id": fit_id,
                    "kind": "no-data-inputs",
                    "detail": "no data files were recorded for this fit",
                }
            )

    for promotion in index.promotions():
        label = str(promotion.get("label"))
        current = index.current_label(label, sample=promotion.get("sample"))
        if current is None or current.get("fit_id") != promotion.get("fit_id"):
            continue
        if index.find(str(promotion.get("fit_id"))) is None:
            problems.append(
                {
                    "fit_id": str(promotion.get("fit_id")),
                    "kind": "dangling-promotion",
                    "detail": f"label '{label}' points at an unknown fit",
                }
            )

    problems.extend(check_orphan_results(layout, index))
    problems.extend(check_contradictions(layout))
    problems.extend(check_generated_scripts(layout))
    if as_json:
        click.echo(json.dumps({"checked": checked, "problems": problems}, indent=2))
    else:
        click.echo(f"Checked {checked} fit(s).")
        if not problems:
            click.echo("  no problems found")
        else:
            for problem in problems:
                click.echo(
                    f"  {problem['kind']:<20} {problem['fit_id']}  {problem['detail']}"
                )

    if problems:
        raise SystemExit(1)


def _freshness_of(layout: ProjectLayout, index: FitIndex, fit_id: str) -> Freshness:
    """Return a fit's freshness for the listing."""
    entry = index.find(fit_id)
    if entry is None:
        return Freshness.UNKNOWN
    fit_dir = find_fit_dir(layout, entry)
    if fit_dir is None:
        return Freshness.BROKEN
    _, freshness = check_inputs(fit_dir, layout.root)
    return freshness


def _short(value: str | None, length: int = 12) -> str:
    """Abbreviate a hash for display."""
    return value[:length] if value else "-"


def _current_user() -> str:
    """Best-effort identity of whoever is promoting a result."""
    import getpass

    try:
        return getpass.getuser()
    except Exception:
        return "unknown"


def _format_number(value: object) -> str:
    """Render a value for display, trimming float noise.

    Chi-squared arrives as a full-precision float. Printing
    ``1.8295690333746149`` beside a verdict that says ``1.83`` invites the
    reader to think the digits mean something; four significant figures is
    already more than the fit warrants. Non-numbers pass through unchanged, so
    this is safe on the mixed values a diff can carry.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        return str(value)
    if isinstance(value, int):
        return str(value)
    return f"{value:.4g}"


def run_diff(
    *, fit_a: str, fit_b: str, as_json: bool = False, script: bool = False
) -> None:
    """Compare two fits and say what actually changed.

    The most useful line is the verdict, because it separates "the fit got
    better" from "the data changed underneath me" -- two situations that look
    identical in a chi-squared column and mean completely different things.

    Args:
        fit_a: Earlier fit id, or a unique prefix.
        fit_b: Later fit id, or a unique prefix.
        as_json: Emit machine-readable JSON.
        script: Also print a unified diff of the two scripts.

    Raises:
        click.ClickException: If either fit cannot be resolved.
    """
    layout = _layout()
    index = FitIndex(layout.index_file)

    entry_a, dir_a = _resolve_fit(layout, index, fit_a)
    entry_b, dir_b = _resolve_fit(layout, index, fit_b)

    manifest_a = FitDirectory(dir_a).read_manifest()
    manifest_b = FitDirectory(dir_b).read_manifest()
    prov_a = manifest_a.get("provenance", {})
    prov_b = manifest_b.get("provenance", {})
    id_a = prov_a.get("identity", {})
    id_b = prov_b.get("identity", {})

    inputs = _diff_inputs(dir_a, dir_b)
    changed = {
        "inputs": id_a.get("inputs_digest") != id_b.get("inputs_digest"),
        # `inputs_digest` counts the script among the inputs, so on its own it
        # cannot tell "I edited the model" from "the reduction was redone" --
        # the one distinction this command exists to make. Ask the input lists,
        # which carry a role per file.
        "data": bool(inputs["changed"] or inputs["only_a"] or inputs["only_b"]),
        "script": id_a.get("script_sha256") != id_b.get("script_sha256"),
        "settings": id_a.get("settings_digest") != id_b.get("settings_digest"),
        "environment": id_a.get("env_digest") != id_b.get("env_digest"),
    }

    payload = {
        "a": entry_a.get("fit_id"),
        "b": entry_b.get("fit_id"),
        "changed": changed,
        "settings": _diff_mapping(
            manifest_a.get("params", {}), manifest_b.get("params", {})
        ),
        "results": _diff_mapping(
            manifest_a.get("info", {}), manifest_b.get("info", {})
        ),
        "inputs": inputs,
        "verdict": _diff_verdict(
            changed, manifest_a.get("info", {}), manifest_b.get("info", {})
        ),
    }

    if as_json:
        click.echo(json.dumps(payload, indent=2, default=str))
        return

    click.echo(f"  a  {payload['a']}")
    click.echo(f"  b  {payload['b']}")
    click.echo()
    # `inputs` stays in the JSON for anything already reading it, but the human
    # table shows `data` -- the same question asked without the script in it.
    for field_name in ("data", "script", "settings", "environment"):
        differs = changed[field_name]
        click.echo(f"  {field_name:<12} {'CHANGED' if differs else 'same'}")

    if payload["settings"]:
        click.echo("\n  settings")
        for key, (was, now) in payload["settings"].items():
            click.echo(f"    {key:<16} {was} -> {now}")

    if (
        payload["inputs"]["changed"]
        or payload["inputs"]["only_a"]
        or payload["inputs"]["only_b"]
    ):
        click.echo("\n  inputs")
        for path in payload["inputs"]["changed"]:
            click.echo(f"    changed  {path}")
        for path in payload["inputs"]["only_a"]:
            click.echo(f"    only a   {path}")
        for path in payload["inputs"]["only_b"]:
            click.echo(f"    only b   {path}")

    if payload["results"]:
        click.echo("\n  results")
        for key, (was, now) in payload["results"].items():
            click.echo(f"    {key:<16} {_format_number(was)} -> {_format_number(now)}")

    if script:
        import difflib

        a_source = (
            (dir_a / "model.py").read_text(encoding="utf-8").splitlines(keepends=True)
        )
        b_source = (
            (dir_b / "model.py").read_text(encoding="utf-8").splitlines(keepends=True)
        )
        click.echo("\n  script")
        for line in difflib.unified_diff(
            a_source, b_source, fromfile="a/model.py", tofile="b/model.py"
        ):
            click.echo("    " + line.rstrip("\n"))

    click.echo(f"\n  verdict  {payload['verdict']}")


def _resolve_fit(layout: ProjectLayout, index: FitIndex, reference: str):
    """Resolve a fit id or prefix, reporting failure as a CLI error."""
    try:
        return resolve_fit(layout, index, reference)
    except FitNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc


def _diff_mapping(a: dict[str, Any], b: dict[str, Any]) -> dict[str, tuple[Any, Any]]:
    """Keys whose values differ between two mappings."""
    return {
        key: (a.get(key), b.get(key))
        for key in sorted(set(a) | set(b))
        if a.get(key) != b.get(key)
    }


def _diff_inputs(dir_a: Path, dir_b: Path) -> dict[str, list[str]]:
    """Compare the measurements two fits consumed, by content rather than name.

    The script is an input too, but it has its own line, its own hash and
    ``--script`` to diff it. Leaving it in here would make every model edit
    also read as a data change.
    """
    a = _data_inputs(dir_a)
    b = _data_inputs(dir_b)
    return {
        "changed": sorted(p for p in set(a) & set(b) if a[p] != b[p]),
        "only_a": sorted(set(a) - set(b)),
        "only_b": sorted(set(b) - set(a)),
    }


def _data_inputs(directory: Path) -> dict[str, str]:
    """The recorded measurements of one fit, path to hash."""
    return {
        e["path"]: e["sha256"]
        for e in FitDirectory(directory).read_inputs()
        if e.get("role") != "script"
    }


def _diff_verdict(changed: dict[str, bool], info_a: dict, info_b: dict) -> str:
    """Say what the difference means, not just that there is one."""
    chisq_a, chisq_b = info_a.get("chisq"), info_b.get("chisq")
    direction = ""
    if isinstance(chisq_a, int | float) and isinstance(chisq_b, int | float):
        if chisq_b < chisq_a:
            direction = f"chi-squared improved {chisq_a:.4g} -> {chisq_b:.4g}"
        elif chisq_b > chisq_a:
            direction = f"chi-squared worsened {chisq_a:.4g} -> {chisq_b:.4g}"
        else:
            direction = "chi-squared unchanged"

    if changed.get("data"):
        return (
            f"the DATA changed{'; ' + direction if direction else ''}. Any comparison "
            "between these two is about different measurements, not different models."
        )
    if changed["script"] and changed["settings"]:
        return f"model and fit settings both changed{'; ' + direction if direction else ''}"
    if changed["script"]:
        return (
            f"model change on identical data{'; ' + direction if direction else ''} -- "
            "the difference is attributable to the model."
        )
    if changed["settings"]:
        return (
            f"fit settings only{'; ' + direction if direction else ''} -- same model, "
            "same data, different optimizer run."
        )
    if changed["environment"]:
        return f"only the environment differs{'; ' + direction if direction else ''}"
    return "nothing recorded differs; these are replicates"


def check_contradictions(layout: ProjectLayout) -> list[dict[str, str]]:
    """Compare every spec against the assessment its sample already has.

    The most-repeated Red Flag in the skill set --- a constraint form or a
    freed parameter contradicting what `nrw tnr assess` read off the data ---
    with both sides machine-readable and nothing, until now, comparing them.

    Args:
        layout: The project layout.

    Returns:
        One problem per contradiction.
    """
    from nr_workbench.contradictions import check
    from nr_workbench.spec.models import SpecError, load_spec

    problems: list[dict[str, str]] = []
    for sample in layout.list_samples():
        directory = layout.sample(sample)
        assessment = _latest_assessment(directory)
        for spec_path in sorted((directory / "models").glob("*.yaml")):
            try:
                spec = load_spec(spec_path)
            except (SpecError, OSError):
                continue  # `nrw model validate` is where a broken spec is reported
            report = check(spec, name=spec_path.stem, tnr=assessment, fitted=None)
            for found in report.contradictions:
                problems.append(
                    {
                        "fit_id": spec_path.stem,
                        "kind": found.kind,
                        "detail": found.message
                        + (f"  [{found.evidence}]" if found.evidence else ""),
                    }
                )
    return problems


def _latest_assessment(sample_dir: Path) -> dict[str, Any] | None:
    """The most recent tNR assessment for a sample, or None."""
    found = sorted((sample_dir / "assessments").glob("*/*assessment.json"))
    for path in reversed(found):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            return payload
    return None


def check_orphan_results(
    layout: ProjectLayout, index: FitIndex
) -> list[dict[str, str]]:
    """Find result directories the index does not know about.

    A fit that was killed mid-run leaves its directory behind without ever
    appending to the index, so it is invisible to `ls`, `whence` and the rest
    of `check`. In the first real beamtime that happened five times in
    twenty-five, and one orphan held a 298 MB posterior chain.

    Args:
        layout: The project layout.
        index: The fit index.

    Returns:
        One problem per unrecorded directory.
    """
    known = {str(entry.get("fit_id")) for entry in index.fits()}
    problems: list[dict[str, str]] = []
    for sample in layout.list_samples():
        results = layout.sample(sample) / "results"
        if not results.is_dir():
            continue
        for directory in sorted(results.iterdir()):
            if not directory.is_dir() or directory.name in known:
                continue
            manifest = directory / "manifest.json"
            if not manifest.is_file():
                detail = (
                    "result directory with no manifest and no index entry -- a "
                    "fit was interrupted before it recorded anything"
                )
            else:
                status = _read_status(manifest)
                if status not in {"running", None}:
                    continue
                detail = (
                    "manifest says the fit was still running -- it was "
                    "interrupted before it finished"
                )
            problems.append(
                {"fit_id": directory.name, "kind": "interrupted-run", "detail": detail}
            )
    return problems


def _read_status(manifest: Path) -> str | None:
    """The status recorded in a manifest, or None if unreadable."""
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload.get("status") if isinstance(payload, dict) else None


def check_generated_scripts(layout: ProjectLayout) -> list[dict[str, str]]:
    """Verify every generated script still matches its spec and its own hash.

    Two failure modes, and they mean different things:

    * **hand-edited** -- the script's recorded self-hash no longer matches its
      contents. Someone edited a generated file, so the spec no longer
      describes what would run. `nrw model fork` is the supported way to take
      ownership; this is the unsupported way.
    * **stale** -- the spec has changed since the script was generated, so the
      script describes an older model than the one on disk.

    Forked scripts are skipped: they are hand-owned by design and carry a
    banner saying so.

    Args:
        layout: The project layout.

    Returns:
        Problems found, in the shape `nrw check` reports.
    """
    from nr_workbench.codegen.generator import verify_self_hash

    problems: list[dict[str, str]] = []
    for script in sorted(layout.samples_dir.glob("*/models/*.py")):
        try:
            source = script.read_text(encoding="utf-8")
        except OSError:
            continue

        relative = script.relative_to(layout.root).as_posix()
        if "HAND-OWNED SCRIPT" in source[:2000]:
            continue
        if "GENERATED BY nr-workbench" not in source[:2000]:
            # A plain hand-written script is not a problem. Running one exactly
            # as it is -- no spec, no migration -- is the adoption path this
            # package promises, and `nrw fit run` records it as fully as a
            # generated one. Only files that *claim* to be generated are
            # policed here; a missing spec for one of those is `missing-spec`.
            continue

        if not verify_self_hash(source):
            problems.append(
                {
                    "fit_id": "-",
                    "kind": "hand-edited-script",
                    "detail": (
                        f"{relative} was edited after generation; the spec no longer "
                        "describes it. Re-apply the change to the spec, or "
                        "`nrw model fork` to own it."
                    ),
                }
            )
            continue

        spec = script.with_suffix(".yaml")
        if not spec.is_file():
            problems.append(
                {
                    "fit_id": "-",
                    "kind": "missing-spec",
                    "detail": f"{relative} was generated from a spec that is gone",
                }
            )
            continue

        recorded = _recorded_spec_hash(source)
        actual = hashlib.sha256(spec.read_bytes()).hexdigest()
        if recorded and recorded != actual:
            problems.append(
                {
                    "fit_id": "-",
                    "kind": "stale-script",
                    "detail": (
                        f"{relative} is older than {spec.name}; re-run "
                        f"`nrw model generate {spec.relative_to(layout.root)}`"
                    ),
                }
            )
            continue

        _check_explanation(script, spec, layout, actual, problems)

    return problems


def _check_explanation(
    script: Path,
    spec: Path,
    layout: ProjectLayout,
    spec_hash: str,
    problems: list[dict[str, str]],
) -> None:
    """Verify the model's explanation still describes the current spec.

    The explanation is what a reader -- a collaborator, a reviewer, yourself in
    six months -- will actually read. One describing a model that has since
    changed is worse than none, because it reads as authoritative.
    """
    notes = script.with_suffix(".md")
    relative = notes.relative_to(layout.root).as_posix()
    if not notes.is_file():
        problems.append(
            {
                "fit_id": "-",
                "kind": "missing-explanation",
                "detail": (
                    f"{relative} is missing; re-run "
                    f"`nrw model generate {spec.relative_to(layout.root)}`"
                ),
            }
        )
        return

    try:
        recorded = _recorded_spec_hash(notes.read_text(encoding="utf-8"))
    except OSError:
        return
    if recorded and recorded != spec_hash:
        problems.append(
            {
                "fit_id": "-",
                "kind": "stale-explanation",
                "detail": (
                    f"{relative} describes an older version of {spec.name}; re-run "
                    f"`nrw model generate {spec.relative_to(layout.root)}`"
                ),
            }
        )


def _recorded_spec_hash(source: str) -> str | None:
    """Read the spec digest a generated script records in its header."""
    import re

    match = re.search(r"spec sha256: ([0-9a-f]{64})", source)
    return match.group(1) if match else None
