"""``nrw agent status`` and ``nrw agent stop`` -- seeing and ending a session.

A session could be started and could not be stopped. Ending one meant finding it
with ``ps``, reading the prompt path out of its command line to work out which
sample it was on, and killing the pid by hand -- while a fit was writing into
the project.
"""

from __future__ import annotations

import json

import click

from nr_workbench.project.layout import ProjectLayout, ProjectNotFoundError


def _layout() -> ProjectLayout:
    """Discover the project, or fail with guidance."""
    try:
        return ProjectLayout.discover()
    except ProjectNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc


def run_agent_status(*, as_json: bool = False) -> None:
    """Report the sessions that recorded themselves as running.

    Args:
        as_json: Emit machine-readable JSON.

    Raises:
        click.ClickException: If there is no project here.
    """
    from nr_workbench.agent.running import running

    layout = _layout()
    sessions = running(layout.root)

    if as_json:
        click.echo(json.dumps([s.as_dict() for s in sessions], indent=2))
        return

    if not sessions:
        click.echo("No unattended session is running.")
        return

    for session in sessions:
        mark = "running" if session.alive else "STALE"
        click.echo(
            f"  {session.sample:<16} pid {session.pid:<8} {mark:<8} "
            f"since {session.started}"
        )
        click.secho(f"      {session.transcript}", dim=True)

    if any(not s.alive for s in sessions):
        click.echo()
        click.secho(
            "  STALE means the pidfile outlived its process: a session died "
            "without cleaning up.\n  `nrw agent stop <sample>` clears it.",
            fg="yellow",
        )


def run_agent_stop(*, sample: str | None = None) -> None:
    """Stop one running session, or all of them.

    Args:
        sample: Restrict to one sample. All running sessions if omitted.

    Raises:
        click.ClickException: If there is no project, or nothing to stop.
    """
    from nr_workbench.agent.running import running, stop

    layout = _layout()
    sessions = running(layout.root, sample)

    if not sessions:
        where = f" for {sample}" if sample else ""
        raise click.ClickException(
            f"No session recorded as running{where}.\n"
            "`nrw agent status` lists them. A session started before this "
            "existed has no pidfile -- find it with `ps` and kill its group."
        )

    for session in sessions:
        click.echo(f"  {stop(layout.root, session)}")

    click.echo()
    click.echo(
        "  Write down where it got to: `nrw note <fit-id> --why ...`, or "
        "`nrw report <sample>` for the sequence so far."
    )
