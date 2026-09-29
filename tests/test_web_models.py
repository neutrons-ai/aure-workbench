"""The Experiment page's models: a sample's specs listed, and a new one written.

The spec is written by the real ``nrw model new``, in a child process, from
real partial files -- the page must write exactly what the terminal writes.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest
from click.testing import CliRunner

from nr_workbench.cli import main
from nr_workbench.project.samples import validate_model_name
from nr_workbench.web import jobs as jobs_module
from nr_workbench.web import models as models_module
from nr_workbench.web.app import create_app

from .test_lifecycle import write_partials

TOKEN = "t" * 32
ORIGIN = "http://localhost"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A project whose sample S1 holds two steady runs; S2 has no data."""
    root = tmp_path / "proj"
    result = CliRunner().invoke(main, ["init", str(root), "--sample", "S1"])
    assert result.exit_code == 0, result.output
    write_partials(root / "samples" / "S1" / "data" / "steady", 100001)
    write_partials(root / "samples" / "S1" / "data" / "steady", 100005)
    (root / "samples" / "S2" / "data" / "steady").mkdir(parents=True)
    return root


@pytest.fixture
def app(project: Path):
    return create_app(project, token=TOKEN, autostart=False)


@pytest.fixture
def writer(app):
    """A client that opened the one-time link."""
    client = app.test_client()
    assert client.get(f"/auth/{TOKEN}").status_code == 303
    return client


def create(client, app, sample: str, name, **headers):
    sent = {"X-NRW-Token": app.config["NRW_PAGE_TOKEN"], "Origin": ORIGIN}
    sent.update(headers)
    return client.post(
        f"/api/experiment/samples/{sample}/models", json={"name": name}, headers=sent
    )


def test_a_link_holder_writes_the_spec_nrw_model_new_writes(
    app, writer, project: Path, monkeypatch
) -> None:
    response = create(writer, app, "S1", "oxide")

    assert response.status_code == 201, response.json
    written = project / "samples" / "S1" / "models" / "oxide.yaml"
    assert "Wrote samples/S1/models/oxide.yaml" in response.json["output"]
    assert [m["name"] for m in response.json["models"]] == ["oxide"]
    # Byte for byte what the terminal writes.
    monkeypatch.chdir(project)
    result = CliRunner().invoke(main, ["model", "new", "S1", "--name", "typed"])
    assert result.exit_code == 0, result.output
    typed = written.with_name("typed.yaml").read_text(encoding="utf-8")
    assert written.read_text(encoding="utf-8") == typed.replace("typed", "oxide")


def test_the_models_are_listed_without_the_link(app, project: Path) -> None:
    (project / "samples" / "S1" / "models").mkdir(exist_ok=True)
    (project / "samples" / "S1" / "models" / "a.yaml").write_text("{}\n")
    (project / "samples" / "S1" / "models" / "a.py").write_text("")

    listed = app.test_client().get("/api/experiment/samples/S1/models").json

    assert listed["models"] == [
        {"name": "a", "spec": "samples/S1/models/a.yaml", "script": True}
    ]
    assert (listed["exists"], listed["has_data"]) == (True, True)


def test_a_spec_that_exists_is_never_overwritten(app, writer, project: Path) -> None:
    spec = project / "samples" / "S1" / "models" / "oxide.yaml"
    spec.parent.mkdir(exist_ok=True)
    spec.write_text("# mine\n", encoding="utf-8")

    response = create(writer, app, "S1", "oxide")

    assert response.status_code == 409
    assert "already exists" in response.json["error"]
    assert spec.read_text(encoding="utf-8") == "# mine\n"


@pytest.mark.parametrize("name", ["../x", "a/b", "-x", "a\n", "", ".hidden", "x" * 65])
def test_a_name_that_is_not_a_plain_name_is_refused(
    app, writer, project: Path, name: str
) -> None:
    before = sorted(p for p in project.rglob("*") if p.is_file())

    response = create(writer, app, "S1", name)

    assert response.status_code == 400
    assert "plain name" in response.json["error"]
    assert sorted(p for p in project.rglob("*") if p.is_file()) == before


def test_a_sample_without_data_says_so_and_writes_nothing(
    app, writer, project: Path
) -> None:
    assert (
        app.test_client().get("/api/experiment/samples/S2/models").json["has_data"]
        is False
    )

    no_data = create(writer, app, "S2", "oxide")
    no_directory = create(writer, app, "S3", "oxide")

    assert no_data.status_code == 409
    assert "No data found" in no_data.json["error"]
    assert no_directory.status_code == 409
    assert "Apply creates it" in no_directory.json["error"]
    assert not (project / "samples" / "S2" / "models" / "oxide.yaml").exists()


def test_writing_a_spec_needs_the_link_and_the_pages_token(
    app, writer, project: Path
) -> None:
    stranger = create(app.test_client(), app, "S1", "oxide")
    no_token = create(writer, app, "S1", "oxide", **{"X-NRW-Token": "stale"})

    assert (stranger.status_code, no_token.status_code) == (403, 403)
    assert not (project / "samples" / "S1" / "models" / "oxide.yaml").exists()


def test_a_read_only_server_writes_no_spec(project: Path) -> None:
    app = create_app(project, token=TOKEN, autostart=False, writable=False)
    data = app.config["NRW_MODELS"]

    with pytest.raises(PermissionError):
        data.create("S1", "oxide")
    assert not (project / "samples" / "S1" / "models" / "oxide.yaml").exists()
    spec = project / "samples" / "S1" / "models" / "oxide.yaml"
    spec.parent.mkdir(exist_ok=True)
    spec.write_text("{}\n")
    with pytest.raises(PermissionError):
        data.fit("S1", "oxide", {"method": "amoeba"})
    with pytest.raises(PermissionError):
        data.cancel("20260929T000000Z-abcdef")
    assert data.jobs.current() is None


def test_a_command_that_does_not_finish_is_stopped(app, writer, monkeypatch) -> None:
    monkeypatch.setattr(models_module, "MODEL_NEW_TIMEOUT", 0.5)
    monkeypatch.setattr(
        models_module,
        "nrw_command",
        lambda *args: [sys.executable, "-c", "import time; time.sleep(60)"],
    )

    response = create(writer, app, "S1", "oxide")

    assert response.status_code == 504
    assert "did not finish" in response.json["error"]


@pytest.mark.parametrize("name", ["oxide", "Cu-Pt_2", "v1.2", "x" * 64])
def test_validate_model_name_accepts_plain_names(name: str) -> None:
    assert validate_model_name(name) == name


# --------------------------------------------------------------------------
# Fitting a spec
# --------------------------------------------------------------------------


def fit(client, app, sample: str, name: str, body: dict, **headers):
    sent = {"X-NRW-Token": app.config["NRW_PAGE_TOKEN"], "Origin": ORIGIN}
    sent.update(headers)
    return client.post(
        f"/api/experiment/samples/{sample}/models/{name}/fit", json=body, headers=sent
    )


def job_ended(client, timeout: float = 120) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        payload = client.get("/api/experiment/jobs/current").json
        if payload["job"] and payload["job"]["status"] != "running":
            return payload
        time.sleep(0.1)
    raise AssertionError("the job did not end")


def test_a_fit_started_from_the_page_is_recorded_as_nrw_fit_run_records_it(
    app, writer, project: Path
) -> None:
    from nr_workbench.provenance.index import FitIndex

    assert create(writer, app, "S1", "oxide").status_code == 201

    started = fit(writer, app, "S1", "oxide", {"method": "amoeba", "steps": 3})
    payload = job_ended(writer)

    assert started.status_code == 202, started.json
    job = payload["job"]
    assert (job["status"], job["step"]) == ("ok", 2), payload["log"]
    assert payload["log"].startswith(
        "$ nrw model generate samples/S1/models/oxide.yaml"
    )
    assert "$ nrw fit run samples/S1/models/oxide.py --method=amoeba" in payload["log"]
    (recorded,) = FitIndex(project / ".nrw" / "index.jsonl").fits(sample="S1")
    assert recorded["fit_id"] == job["fit_id"]
    assert (recorded["model"], recorded["method"], recorded["status"]) == (
        "oxide",
        "amoeba",
        "ok",
    )
    assert recorded["settings"]["steps"] == 3


@pytest.mark.parametrize(
    "body",
    [
        {"method": "lm"},
        {"method": "amoeba", "steps": 0},
        {"method": "amoeba", "steps": "5"},
        {"method": "amoeba", "steps": True},
        {"method": "amoeba", "samples": 1000},
        {"method": "dream", "burn": -1},
        {"method": "dream", "samples": 10**9},
        {"method": "amoeba", "note": "two\nlines"},
        {"method": "amoeba", "note": "x" * 501},
    ],
)
def test_a_fit_setting_that_is_not_usable_is_refused_before_anything_runs(
    app, writer, project: Path, body: dict
) -> None:
    assert create(writer, app, "S1", "oxide").status_code == 201

    response = fit(writer, app, "S1", "oxide", body)

    assert response.status_code == 400, response.json
    assert app.config["NRW_MODELS"].jobs.current() is None


def test_fitting_a_spec_that_does_not_exist_is_refused(app, writer) -> None:
    response = fit(writer, app, "S1", "missing", {"method": "amoeba"})

    assert response.status_code == 409
    assert "does not exist" in response.json["error"]


def test_a_second_fit_waits_for_the_first_and_cancel_stops_it(
    app, writer, project: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        jobs_module,
        "nrw_command",
        lambda *args: [sys.executable, "-c", "import time; time.sleep(60)"],
    )
    spec = project / "samples" / "S1" / "models" / "oxide.yaml"
    spec.parent.mkdir(exist_ok=True)
    spec.write_text("{}\n")
    first = fit(writer, app, "S1", "oxide", {"method": "dream", "samples": 1000})

    second = fit(writer, app, "S1", "oxide", {"method": "amoeba"})
    stranger = app.test_client().post(
        f"/api/experiment/jobs/{first.json['job']['id']}/cancel",
        json={},
        headers={"X-NRW-Token": app.config["NRW_PAGE_TOKEN"], "Origin": ORIGIN},
    )
    cancelled = writer.post(
        f"/api/experiment/jobs/{first.json['job']['id']}/cancel",
        json={},
        headers={"X-NRW-Token": app.config["NRW_PAGE_TOKEN"], "Origin": ORIGIN},
    )

    assert first.status_code == 202
    assert second.status_code == 409
    assert "one job runs at a time" in second.json["error"]
    assert stranger.status_code == 403
    assert cancelled.status_code == 200
    assert job_ended(writer)["job"]["status"] == "cancelled"


def test_fitting_needs_the_link(app, project: Path) -> None:
    spec = project / "samples" / "S1" / "models" / "oxide.yaml"
    spec.parent.mkdir(exist_ok=True)
    spec.write_text("{}\n")

    response = fit(app.test_client(), app, "S1", "oxide", {"method": "amoeba"})

    assert response.status_code == 403
    assert app.config["NRW_MODELS"].jobs.current() is None
