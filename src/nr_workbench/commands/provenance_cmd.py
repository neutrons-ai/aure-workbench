"""``nrw whence``, ``ls``, ``promote`` and ``check`` -- the query surface.

These four commands are the product. Everything else is plumbing that exists so
these can answer honestly.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click

from nr_workbench.project.layout import ProjectLayout, ProjectNotFoundError
from nr_workbench.provenance.index import EVENT_PROMOTE, FitIndex
from nr_workbench.provenance.record import FitDirectory, format_timestamp, utc_now
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
            resolved = _fit_dir(layout, matches[0])
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
                f"chisq {entry.get('chisq')}"
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
    rows = index.fits(sample=sample)[:limit]

    if as_json:
        click.echo(json.dumps(rows, indent=2, default=str))
        return

    if not rows:
        click.echo("No fits recorded yet. Run one with `nrw fit run <script.py>`.")
        return

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

    if promoted:
        click.echo()
        click.echo("  * promoted")


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

    fit_dir = _fit_dir(layout, entry)
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
        fit_dir = _fit_dir(layout, entry)
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


def _fit_dir(layout: ProjectLayout, entry: dict[str, Any]) -> Path | None:
    """Locate the directory for an index entry."""
    fit_id = str(entry.get("fit_id", ""))
    sample = entry.get("sample")
    candidates = []
    if sample:
        candidates.append(layout.sample(str(sample)) / "results" / fit_id)
    candidates.append(layout.root / "results" / fit_id)
    for candidate in candidates:
        if (candidate / "manifest.json").is_file():
            return candidate
    return None


def _freshness_of(layout: ProjectLayout, index: FitIndex, fit_id: str) -> Freshness:
    """Return a fit's freshness for the listing."""
    entry = index.find(fit_id)
    if entry is None:
        return Freshness.UNKNOWN
    fit_dir = _fit_dir(layout, entry)
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
