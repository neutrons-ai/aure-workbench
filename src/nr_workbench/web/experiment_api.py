"""The Experiment page's JSON API, mirroring :class:`~nr_workbench.web.experiment.ExperimentData`.

Every route is a one-line call plus error mapping, for the same reason as
:mod:`nr_workbench.web.api`: an agent debugging the page can ``curl`` exactly
what the browser received. This blueprint is the only place in the server that
accepts a write, and every write passes :func:`~nr_workbench.web.security.
refuse_unless_writer` first.

Status codes say whose problem it is: 400 the request was wrong, 403 it is not
allowed from here, 409 something changed or must be resolved first, 503 the
catalog cannot be read, 504 the data source did not answer.
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, current_app, jsonify, request
from werkzeug.exceptions import HTTPException

from nr_workbench.web import security
from nr_workbench.web.experiment import ExperimentData, SourceTimeoutError

experiment_api = Blueprint("experiment_api", __name__, url_prefix="/api/experiment")


def data() -> ExperimentData:
    """The request's :class:`ExperimentData`, held on the app config."""
    return current_app.config["NRW_EXPERIMENT"]  # type: ignore[no-any-return]


@experiment_api.before_request
def _gate() -> None:
    security.refuse_unless_writer()


def _body() -> dict[str, Any]:
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise ValueError("the request body must be a JSON object")
    return payload


def _error(exc: Exception, status: int) -> tuple[Any, int]:
    return jsonify({"error": str(exc), "kind": type(exc).__name__}), status


@experiment_api.errorhandler(Exception)
def _map(exc: Exception) -> tuple[Any, int]:
    """Turn each failure into an honest status code, as JSON."""
    from nr_workbench.experiment.adopt import AdoptRefused
    from nr_workbench.experiment.apply import ApplyError
    from nr_workbench.experiment.model import RecordConflict
    from nr_workbench.experiment.render import SampleRenderError
    from nr_workbench.experiment.store import CatalogError
    from nr_workbench.project.scaffold import LockProblemError

    if isinstance(exc, HTTPException):
        return jsonify({"error": exc.description}), exc.code or 500
    for kinds, status in (
        ((FileNotFoundError,), 404),
        ((PermissionError,), 403),
        ((RecordConflict, ApplyError, AdoptRefused, LockProblemError, SampleRenderError), 409),
        ((CatalogError,), 503),
        ((SourceTimeoutError,), 504),
        ((ValueError,), 400),
    ):
        if isinstance(exc, kinds):
            return _error(exc, status)
    current_app.logger.exception("Unhandled error in %s", request.path)
    return _error(exc, 500)


def _http_json(exc: HTTPException) -> tuple[Any, int]:
    return jsonify({"error": exc.description}), exc.code or 500


# Code-specific, on the blueprint: Flask tries every level's handler *for the
# status code* before any level's class-based handler, so the app's HTML 403
# and 404 pages would otherwise answer this API's refusals -- and a script
# reading `response.json()["error"]` would get a page of markup instead.
for _code in (400, 403, 404, 405, 409, 413, 415):
    experiment_api.register_error_handler(_code, _http_json)


def _list_arg(name: str) -> list[str] | None:
    raw = request.args.get(name, "")
    return [part for part in raw.split(",") if part] or None


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


@experiment_api.get("")
def overview() -> Any:
    """Everything the page shows on load."""
    return jsonify(data().overview())


@experiment_api.get("/changes")
def changes() -> Any:
    """What changed since ``?since=<cursor>``."""
    return jsonify(data().changes(request.args.get("since")))


@experiment_api.get("/runs/<int:run>/curves")
def curves(run: int) -> Any:
    """One run's reflectivity, from the data source."""
    return jsonify(data().curves(run))


@experiment_api.get("/samples/<sample_id>/preview")
def preview(sample_id: str) -> Any:
    """The sample.md the catalog would write, and where the file stands."""
    return jsonify(data().sample_preview(sample_id))


@experiment_api.get("/apply")
def apply_plan() -> Any:
    """What applying would do: ``?samples=S1,S2&confirm=218386``."""
    return jsonify(data().apply_plan(_list_arg("samples"), _list_arg("confirm")))


@experiment_api.get("/samples/<sample_id>/adopt")
def adopt_plan(sample_id: str) -> Any:
    """What adopting, or pulling, a sample's sample.md would do."""
    return jsonify(data().adopt_plan(sample_id))


# ---------------------------------------------------------------------------
# Writing -- every one behind the gate above
# ---------------------------------------------------------------------------


@experiment_api.put("/runs")
def update_runs() -> Any:
    """Record run edits: ``{"changes": [{"run", "base_rev", "fields"}]}``."""
    return jsonify(data().update_runs(_body().get("changes")))


@experiment_api.put("/samples/<sample_id>")
def update_sample(sample_id: str) -> Any:
    """Record a sample's context: ``{"base_rev", "fields"}`` or ``{"delete": true}``."""
    return jsonify(data().update_sample(sample_id, _body()))


@experiment_api.post("/apply")
def apply() -> Any:
    """Carry out a reviewed plan: ``{"plan_id", "samples", "confirmed"}``."""
    body = _body()
    return jsonify(
        data().apply(body.get("plan_id"), body.get("samples"), body.get("confirmed"))
    )


@experiment_api.post("/samples/<sample_id>/adopt")
def adopt(sample_id: str) -> Any:
    """Adopt or pull a sample's sample.md: ``{"rewrite": bool}``."""
    return jsonify(data().adopt(sample_id, _body().get("rewrite", False)))
