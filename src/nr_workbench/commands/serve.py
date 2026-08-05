"""``nrw serve`` -- start the read-only web view of a project."""

from __future__ import annotations

from pathlib import Path

import click

from nr_workbench.project.layout import ProjectLayout, ProjectNotFoundError


def run_serve(
    *,
    root: str | None = None,
    host: str = "127.0.0.1",
    port: int = 8765,
    debug: bool = False,
) -> None:
    """Serve the project UI.

    Args:
        root: Project root. Discovered from the working directory if omitted.
        host: Interface to bind to.
        port: Port to listen on.
        debug: Enable the Flask reloader and debugger.

    Raises:
        click.ClickException: If no project can be found, or the port is taken.
    """
    try:
        layout = (
            ProjectLayout(root=Path(root).resolve())
            if root
            else ProjectLayout.discover()
        )
    except ProjectNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc

    # Imported here rather than at module scope so that `nrw --help` does not
    # pay for Flask, matching how every other command treats its heavy deps.
    from nr_workbench.web.app import create_app

    try:
        app = create_app(layout.root)
    except FileNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc

    overview = app.config["NRW_DATA"].overview()
    click.echo(f"  {overview['name']}  {layout.root}")
    click.echo(f"  {len(overview['samples'])} sample(s), {overview['n_fits']} fit(s)")
    click.echo("")
    click.echo(f"  http://{host}:{port}/")
    click.echo(f"  http://{host}:{port}/api/overview   the same data as JSON")
    click.echo("")

    if host not in {"127.0.0.1", "localhost", "::1"}:
        click.echo(
            f"  ! Bound to {host}, not loopback. This server is read-only but "
            "has no\n    authentication, so anyone who can reach this port can "
            "read the project.",
            err=True,
        )

    try:
        app.run(host=host, port=port, debug=debug)
    except OSError as exc:
        raise click.ClickException(
            f"Cannot bind {host}:{port} -- {exc}. "
            "Another server may already be running; try --port."
        ) from exc
