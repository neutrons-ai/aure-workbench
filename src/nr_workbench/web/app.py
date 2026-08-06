"""The Flask application: routing and nothing else.

Every view here resolves data through :class:`~nr_workbench.web.project.
ProjectData` and hands it to a template. There is no analysis in this module
and there should never be any -- if a view needs a number computed, the
computation belongs in ``ProjectData`` where it can be tested without a
request context.

The server is read-only and intended for ``localhost``. It has no
authentication, so :func:`serve` binds to the loopback interface unless told
otherwise, and says so when it does not.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from flask import Flask, abort, render_template, send_from_directory

from nr_workbench.web.api import api
from nr_workbench.web.project import ProjectData


def create_app(root: Path) -> Flask:
    """Build the application for one project.

    Args:
        root: Project root, the directory holding ``nrw.toml``.

    Returns:
        A configured Flask application.

    Raises:
        FileNotFoundError: If ``root`` is not a workbench project.
    """
    root = Path(root).resolve()
    if not (root / "nrw.toml").is_file():
        raise FileNotFoundError(
            f"{root} is not a workbench project (no nrw.toml). "
            "Run `nrw init` there first, or pass --root."
        )

    app = Flask(__name__)
    app.config["NRW_DATA"] = ProjectData(root)
    app.config["NRW_ROOT"] = root
    app.register_blueprint(api)

    app.jinja_env.filters["nrwjson"] = _compact_json
    app.jinja_env.filters["markdown"] = _render_markdown

    _register_views(app)
    return app


def _compact_json(value: Any) -> str:
    """Serialise for embedding inside a ``<script>`` block.

    ``</`` is escaped because the browser's HTML tokeniser finds ``</script>``
    inside a string literal and ends the block there -- a run label or a note
    containing that text would otherwise truncate the page's JavaScript and
    spill the rest as visible markup. ``json.dumps`` does not do this on its
    own, and Jinja's autoescaping does not apply inside a script tag.
    """
    return json.dumps(value, separators=(",", ":"), default=str).replace("</", "<\\/")


def _render_markdown(text: str | None) -> Any:
    """Render markdown for the rail, marked safe for direct inclusion."""
    from markupsafe import Markup

    from nr_workbench.web.prose import render

    return Markup(render(text))  # noqa: S704 - render() disables raw HTML


def _register_views(app: Flask) -> None:
    """Attach the HTML routes."""

    def data() -> ProjectData:
        return app.config["NRW_DATA"]  # type: ignore[no-any-return]

    @app.get("/")
    def index() -> str:
        """Project overview: one card per sample."""
        return render_template("index.html", overview=data().overview())

    @app.get("/s/<sample_id>")
    def sample(sample_id: str) -> str:
        """The sample landscape -- every measurement on one page."""
        project = data()
        try:
            detail = project.sample(sample_id)
        except FileNotFoundError:
            abort(404, f"No sample {sample_id!r} in this project.")

        curves = project.steady_curves(sample_id)
        problems = list(detail["problems"]) + list(curves["problems"])

        # The first plottable series and the first assessment are loaded
        # eagerly so the page is useful without a click. Anything further is
        # fetched from the API on demand.
        series_payload = None
        plottable = [s for s in detail["series"] if s.get("plottable")]
        if plottable:
            name = plottable[0]["name"]
            try:
                series_payload = project.series_data(sample_id, name)
                problems += series_payload.get("problems", [])
            except (FileNotFoundError, ValueError) as exc:
                problems.append({"scope": f"series:{name}", "message": str(exc)})

        assessment = None
        labels = detail["assessments"]
        if labels:
            try:
                assessment = project.assessment(sample_id, labels[0])
                problems += assessment.get("problems", [])
            except (FileNotFoundError, ValueError) as exc:
                problems.append(
                    {"scope": f"assessment:{labels[0]}", "message": str(exc)}
                )

        return render_template(
            "sample.html",
            sample=detail,
            curves=curves["curves"],
            series=series_payload,
            assessment=assessment,
            problems=problems,
        )

    @app.get("/f/<fit_id>")
    def fit(fit_id: str) -> str:
        """One fit in detail, with its provenance."""
        try:
            detail = data().fit(fit_id)
        except FileNotFoundError as exc:
            abort(404, str(exc))
        except ValueError as exc:
            abort(400, str(exc))
        trajectory = data().trajectory(fit_id)
        return render_template("fit.html", fit=detail, trajectory=trajectory)

    @app.get("/fits")
    def fits() -> str:
        """Every fit in the project, newest first."""
        return render_template(
            "fits.html", fits=data().fits(), overview=data().overview()
        )

    @app.get("/figures/<sample_id>/<label>/<path:filename>")
    def figure(sample_id: str, label: str, filename: str) -> Any:
        """Serve a figure from an assessment directory.

        ``send_from_directory`` rejects paths that escape the directory, which
        is what keeps a crafted ``filename`` from reading the rest of the disk.
        The sample and label are resolved through the layout rather than joined
        as strings for the same reason.
        """
        project = data()
        directory = (project.layout.sample(sample_id) / "assessments" / label).resolve()
        if not directory.is_dir():
            abort(404)
        try:
            directory.relative_to(project.root)
        except ValueError:
            abort(404)
        return send_from_directory(directory, filename)

    @app.get("/results/<sample_id>/<fit_id>/<path:filename>")
    def result_file(sample_id: str, fit_id: str, filename: str) -> Any:
        """Serve a file from a fit's immutable result directory."""
        project = data()
        directory = (project.layout.sample(sample_id) / "results" / fit_id).resolve()
        if not directory.is_dir():
            abort(404)
        try:
            directory.relative_to(project.root)
        except ValueError:
            abort(404)
        return send_from_directory(directory, filename)

    @app.errorhandler(404)
    def not_found(error: Any) -> tuple[str, int]:
        """Render a 404 in the site's own layout."""
        return render_template("error.html", code=404, message=error.description), 404

    @app.errorhandler(400)
    def bad_request(error: Any) -> tuple[str, int]:
        """Render a 400 in the site's own layout."""
        return render_template("error.html", code=400, message=error.description), 400


def serve(
    root: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    debug: bool = False,
) -> None:
    """Run the development server.

    Args:
        root: Project root.
        host: Interface to bind. Defaults to loopback.
        port: Port to listen on.
        debug: Enable Flask's reloader and debugger.

    Raises:
        FileNotFoundError: If ``root`` is not a workbench project.
    """
    app = create_app(root)
    app.run(host=host, port=port, debug=debug)
