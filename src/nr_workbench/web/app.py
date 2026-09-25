"""The Flask application: routing and nothing else.

Every view here resolves data through :class:`~nr_workbench.web.project.
ProjectData` and hands it to a template. There is no analysis in this module
and there should never be any -- if a view needs a number computed, the
computation belongs in ``ProjectData`` where it can be tested without a
request context.

The server is for ``localhost``. Every page and ``/api`` route only reads.
The one exception is the Experiment page's blueprint
(:mod:`nr_workbench.web.experiment_api`), whose writes are gated by
:mod:`nr_workbench.web.security`: loopback only, from a browser that opened
the one-time link ``nrw serve`` prints, and disabled entirely when bound
elsewhere or running under ``NRW_AGENT``.
"""

from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import Any

from flask import (
    Flask,
    abort,
    make_response,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)

from nr_workbench.web import security
from nr_workbench.web.api import api
from nr_workbench.web.experiment import ExperimentData
from nr_workbench.web.experiment_api import experiment_api
from nr_workbench.web.project import ProjectData

#: Methods that change nothing.
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def create_app(
    root: Path,
    *,
    writable: bool = True,
    read_only_reason: str = "",
    bound_host: str = "127.0.0.1",
    token: str | None = None,
    autostart: bool = True,
) -> Flask:
    """Build the application for one project.

    Args:
        root: Project root, the directory holding ``nrw.toml``.
        writable: Whether the Experiment page may write at all. Also forced off
            when ``bound_host`` is not loopback.
        read_only_reason: What the page says when it may not.
        bound_host: The interface the server listens on.
        token: The secret behind the one-time link; generated if omitted.
        autostart: Start polling the data source on first use.

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
    security.install(
        app,
        bound_host=bound_host,
        writable=writable,
        token=token or secrets.token_urlsafe(24),
    )
    app.config["NRW_READ_ONLY_REASON"] = read_only_reason or (
        "" if app.config["NRW_WRITABLE"] else _default_reason(bound_host)
    )
    app.config["NRW_EXPERIMENT"] = ExperimentData(
        root,
        writable=app.config["NRW_WRITABLE"],
        why_read_only=app.config["NRW_READ_ONLY_REASON"],
        autostart=autostart,
    )
    app.register_blueprint(api)
    app.register_blueprint(experiment_api)

    @app.before_request
    def _only_the_experiment_blueprint_writes() -> None:
        # The second mechanism, beside the blueprint's own gate: an unsafe
        # method anywhere else is refused, so a write route added outside the
        # blueprint cannot slip past the gate by accident.
        if (
            request.method not in _SAFE_METHODS
            and request.blueprint != "experiment_api"
        ):
            abort(405)

    app.jinja_env.filters["nrwjson"] = _compact_json
    app.jinja_env.filters["markdown"] = _render_markdown

    # A global rather than a per-route variable: the fit-story row is included
    # by two pages and would otherwise silently lose its meaning on whichever
    # route forgot to pass it -- an undefined name in Jinja is falsy, so every
    # change line would read as informative again with nothing to show for it.
    from nr_workbench.provenance.summary import FIRST_RUN

    app.jinja_env.globals["first_run"] = FIRST_RUN

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
    text = json.dumps(value, separators=(",", ":"), default=str)
    # Every character that can change how the HTML parser reads a script
    # block, as a JSON escape. `</` alone is not enough: `<!--<script>` puts
    # the tokeniser into a state where the block's own `</script>` no longer
    # closes it, and the rest of the page becomes script.
    for char, escape in (
        ("<", "\\u003c"),
        (">", "\\u003e"),
        ("&", "\\u0026"),
        ("\u2028", "\\u2028"),
        ("\u2029", "\\u2029"),
    ):
        text = text.replace(char, escape)
    return text


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

    @app.get("/auth/<token>")
    def authorize(token: str) -> Any:
        """The one-time link `nrw serve` prints: grants this browser write access.

        The link goes in a cookie and the browser is redirected to a clean URL,
        so the secret does not linger in the address bar or leak in a Referer.
        """
        expected = app.config.get("NRW_TOKEN", "")
        if not (
            app.config.get("NRW_WRITABLE")
            and security.is_loopback(request.remote_addr)
            and secrets.compare_digest(token, expected)
        ):
            abort(403, "This link is not valid for this server.")
        response = make_response(redirect(url_for("experiment"), code=303))
        response.set_cookie(
            security.COOKIE, expected, httponly=True, samesite="Strict", path="/"
        )
        return response

    @app.get("/experiment")
    def experiment() -> Any:
        """Every run of the experiment, organized into samples."""
        nonce = secrets.token_urlsafe(16)
        writer = security.can_write()
        page = render_template(
            "experiment.html",
            payload=app.config["NRW_EXPERIMENT"].overview(),
            page_token=app.config["NRW_PAGE_TOKEN"] if writer else "",
            writer=writer,
            csp_nonce=nonce,
        )
        response = make_response(page)
        # 'unsafe-eval' because Plotly's WebGL traces (scattergl, which the
        # reflectivity panel uses) compile their shaders through regl, which
        # builds functions at run time. It admits no injected <script> tag and
        # no inline handler -- the nonce still stops both.
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            f"script-src 'self' 'nonce-{nonce}' 'unsafe-eval' https://cdn.plot.ly; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "img-src 'self' data:; connect-src 'self'; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        return response

    @app.errorhandler(403)
    def forbidden(error: Any) -> tuple[str, int]:
        """Render a 403 in the site's own layout."""
        return render_template("error.html", code=403, message=error.description), 403

    @app.errorhandler(404)
    def not_found(error: Any) -> tuple[str, int]:
        """Render a 404 in the site's own layout."""
        return render_template("error.html", code=404, message=error.description), 404

    @app.errorhandler(400)
    def bad_request(error: Any) -> tuple[str, int]:
        """Render a 400 in the site's own layout."""
        return render_template("error.html", code=400, message=error.description), 400


def _default_reason(bound_host: str) -> str:
    if not security.is_loopback(bound_host) and bound_host != "localhost":
        return (
            f"The server is bound to {bound_host}, not loopback, so it only "
            "shows the experiment. Run `nrw serve` without --host to edit it."
        )
    return "This server was started read-only."


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
    app = create_app(root, bound_host=host)
    app.run(host=host, port=port, debug=debug)
