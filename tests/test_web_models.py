"""The Experiment page's models: specs listed and written, and fits run.

The spec is written by the real ``nrw model new``, in a child process, from
real partial files -- the page must write exactly what the terminal writes --
and fits are real ``nrw model generate`` and ``nrw fit run`` runs.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest
from click.testing import CliRunner

from nr_workbench.cli import main
from nr_workbench.project.samples import validate_model_name
from nr_workbench.provenance.index import FitIndex
from nr_workbench.web import jobs as jobs_module
from nr_workbench.web import models as models_module
from nr_workbench.web.app import create_app

from .test_lifecycle import write_partials

TOKEN = "t" * 32
ORIGIN = "http://localhost"

#: The command line a real step runs, before any test replaces it.
REAL_COMMAND = jobs_module.nrw_command


@pytest.fixture(autouse=True)
def empty_home(tmp_path: Path, monkeypatch) -> None:
    """Give the nrw processes these tests start an empty home, as conftest
    gives this one: a child reads ``~/.nrw`` and ``~/.aure`` afresh, and a
    developer's real endpoint key there would make a test bill for a call."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))


def serial(*args: str) -> list[str]:
    """The real command, with a fit on one CPU.

    ``nrw fit run`` takes every core by default -- a pool of twenty processes
    here -- and a test's fit doing that starves the browser tests running on
    the other workers until their pages time out loading.
    """
    if args[:2] == ("fit", "run"):
        args = (*args, "--parallel=1")
    return REAL_COMMAND(*args)


@pytest.fixture(autouse=True)
def serial_fits(monkeypatch) -> None:
    monkeypatch.setattr(jobs_module, "nrw_command", serial)


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
    app = create_app(project, token=TOKEN, autostart=False)
    yield app
    # A test that failed mid-job leaves nothing running to slow the next one.
    app.config["NRW_MODELS"].jobs.stop()


@pytest.fixture
def writer(app):
    """A client that opened the one-time link."""
    client = app.test_client()
    assert client.get(f"/auth/{TOKEN}").status_code == 303
    return client


def headers(app, **extra) -> dict[str, str]:
    return {"X-NRW-Token": app.config["NRW_PAGE_TOKEN"], "Origin": ORIGIN, **extra}


def create(client, app, sample: str, name, **extra):
    return client.post(
        f"/api/experiment/samples/{sample}/models",
        json={"name": name},
        headers=headers(app, **extra),
    )


def spec_of(project: Path, name: str = "oxide") -> Path:
    """A spec on disk, for tests whose steps never read it."""
    spec = project / "samples" / "S1" / "models" / f"{name}.yaml"
    spec.parent.mkdir(exist_ok=True)
    spec.write_text("{}\n")
    return spec


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


def test_a_module_in_the_project_is_not_imported_in_place_of_nrws(
    app, writer, project: Path
) -> None:
    # A scientist's own click.py beside the analysis, or one planted there.
    (project / "click.py").write_text("raise SystemExit('the project copy ran')\n")

    response = create(writer, app, "S1", "oxide")

    assert response.status_code == 201, response.json


def test_the_models_are_listed_without_the_link(app, project: Path) -> None:
    spec_of(project, "a").with_suffix(".py").write_text("")

    listed = app.test_client().get("/api/experiment/samples/S1/models").json

    assert listed["models"] == [
        {"name": "a", "spec": "samples/S1/models/a.yaml", "script": True}
    ]
    assert (listed["exists"], listed["has_data"]) == (True, True)


def test_a_spec_that_exists_is_never_overwritten(app, writer, project: Path) -> None:
    spec = spec_of(project)
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
    listed = app.test_client().get("/api/experiment/samples/S2/models").json

    no_data = create(writer, app, "S2", "oxide")
    no_directory = create(writer, app, "S3", "oxide")

    assert listed["has_data"] is False
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


def test_a_read_only_server_writes_no_spec_and_starts_no_job(project: Path) -> None:
    spec_of(project, "fitted")
    app = create_app(project, token=TOKEN, autostart=False, writable=False)
    data = app.config["NRW_MODELS"]

    with pytest.raises(PermissionError):
        data.create("S1", "oxide")
    with pytest.raises(PermissionError):
        data.fit("S1", "fitted", {"method": "amoeba"})
    with pytest.raises(PermissionError):
        data.quick_fit("S1", "auto", 100001)
    with pytest.raises(PermissionError):
        data.cancel("20260929T120000000000Z-abcdef")

    assert not (project / "samples" / "S1" / "models" / "oxide.yaml").exists()
    assert data.jobs.current() is None


def test_a_command_that_does_not_finish_is_stopped(app, writer, monkeypatch) -> None:
    monkeypatch.setattr(models_module, "MODEL_NEW_TIMEOUT", 0.5)
    monkeypatch.setattr(
        jobs_module,
        "nrw_command",
        lambda *args: [sys.executable, "-c", "import time; time.sleep(60)"],
    )

    response = create(writer, app, "S1", "oxide")

    assert response.status_code == 504
    assert "did not finish" in response.json["error"]


def test_a_command_that_declines_is_409_and_one_that_crashes_is_500(
    app, writer, monkeypatch
) -> None:
    def fails(code: str):
        monkeypatch.setattr(
            jobs_module, "nrw_command", lambda *args: [sys.executable, "-c", code]
        )

    fails("import sys; print('Error: nope, and why'); sys.exit(1)")
    declined = create(writer, app, "S1", "oxide")
    fails("raise PermissionError(13, 'Permission denied', '/proj/x.yaml')")
    crashed = create(writer, app, "S1", "oxide")

    assert (declined.status_code, declined.json["error"]) == (409, "nope, and why")
    # A crash is not the caller's mistake: said as such, its detail in the log.
    assert (crashed.status_code, crashed.json["kind"]) == (500, "InternalError")
    assert "/proj" not in crashed.json["error"]


@pytest.mark.parametrize("name", ["oxide", "Cu-Pt_2", "v1.2", "x" * 64])
def test_validate_model_name_accepts_plain_names(name: str) -> None:
    assert validate_model_name(name) == name


# --------------------------------------------------------------------------
# Fitting a spec
# --------------------------------------------------------------------------


def fit(client, app, sample: str, name: str, body: dict, **extra):
    return client.post(
        f"/api/experiment/samples/{sample}/models/{name}/fit",
        json=body,
        headers=headers(app, **extra),
    )


def job_ended(client, timeout: float = 120) -> dict:
    deadline = time.monotonic() + timeout
    payload: dict = {}
    while time.monotonic() < deadline:
        payload = client.get("/api/experiment/jobs/current?offset=0").json
        if payload["job"] and payload["job"]["status"] != "running":
            return payload
        time.sleep(0.1)
    raise AssertionError(f"the job did not end: {payload}")


def started(response) -> None:
    """Say at once why a job was not started, not two minutes on."""
    assert response.status_code == 202, response.json


@pytest.mark.integration
def test_a_fit_started_from_the_page_is_recorded_as_nrw_fit_run_records_it(
    app, writer, project: Path
) -> None:
    assert create(writer, app, "S1", "oxide").status_code == 201

    started(fit(writer, app, "S1", "oxide", {"method": "amoeba", "steps": 3}))
    payload = job_ended(writer)

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


@pytest.mark.integration
def test_a_fit_that_records_nothing_links_nothing_and_force_links_the_new_one(
    app, writer, project: Path
) -> None:
    assert create(writer, app, "S1", "oxide").status_code == 201
    body = {"method": "amoeba", "steps": 3}
    started(fit(writer, app, "S1", "oxide", body))
    first = job_ended(writer)["job"]["fit_id"]

    started(fit(writer, app, "S1", "oxide", body))  # nothing changed since
    refused = job_ended(writer)
    started(fit(writer, app, "S1", "oxide", {**body, "force": True}))
    forced = job_ended(writer)["job"]

    assert (refused["job"]["status"], refused["job"]["fit_id"]) == ("failed", None)
    assert "An identical run already exists" in refused["log"]
    assert forced["status"] == "ok"
    newest = FitIndex(project / ".nrw" / "index.jsonl").fits(sample="S1")[0]
    assert forced["fit_id"] == newest["fit_id"] != first


def test_a_fit_of_the_same_model_made_meanwhile_is_not_taken_for_the_jobs(
    app, writer, project: Path, monkeypatch
) -> None:
    # The job's fit prints where it is recorded; meanwhile someone at the
    # terminal fits the same model, later and newer in the index.
    index = project / ".nrw" / "index.jsonl"
    lines = [
        {
            "fit_id": "20260929-120000Z-aaaaaaaa",
            "sample": "S1",
            "model": "oxide",
            "started_at": "2999-01-01T00:00:00Z",
        },
        {
            "fit_id": "20260929-120001Z-bbbbbbbb",
            "sample": "S1",
            "model": "oxide",
            "started_at": "2999-01-01T00:00:01Z",
        },
    ]
    step = (
        "import json, pathlib\n"
        "print('Running amoeba fit -> samples/S1/results/20260929-120000Z-aaaaaaaa')\n"
        f"with pathlib.Path({str(index)!r}).open('a') as f:\n"
        f"    for line in {lines!r}: f.write(json.dumps({{'event': 'fit', **line}}) + '\\n')\n"
    )
    monkeypatch.setattr(
        jobs_module, "nrw_command", lambda *args: [sys.executable, "-c", step]
    )
    spec_of(project)

    started(fit(writer, app, "S1", "oxide", {"method": "amoeba"}))

    assert job_ended(writer)["job"]["fit_id"] == "20260929-120000Z-aaaaaaaa"


@pytest.mark.parametrize(
    "body",
    [
        {"method": "lm"},
        {"method": 5},
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
    spec_of(project)

    response = fit(writer, app, "S1", "oxide", body)

    assert response.status_code == 400, response.json
    assert app.config["NRW_MODELS"].jobs.current() is None


def test_fitting_a_spec_that_does_not_exist_is_404(app, writer) -> None:
    response = fit(writer, app, "S1", "missing", {"method": "amoeba"})

    assert response.status_code == 404
    assert "does not exist" in response.json["error"]


def test_a_second_fit_is_refused_while_one_runs_and_cancel_stops_it(
    app, writer, project: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        jobs_module,
        "nrw_command",
        lambda *args: [sys.executable, "-c", "import time; time.sleep(60)"],
    )
    spec_of(project)
    first = fit(writer, app, "S1", "oxide", {"method": "dream", "samples": 1000})
    cancel = f"/api/experiment/jobs/{first.json['job']['id']}/cancel"

    second = fit(writer, app, "S1", "oxide", {"method": "amoeba"})
    stranger = app.test_client().post(cancel, json={}, headers=headers(app))
    cancelled = writer.post(cancel, json={}, headers=headers(app))

    assert first.status_code == 202
    assert second.status_code == 409
    assert "one job runs at a time" in second.json["error"]
    assert stranger.status_code == 403
    assert cancelled.status_code == 200
    assert job_ended(writer)["job"]["status"] == "cancelled"


def test_fitting_needs_the_link(app, project: Path) -> None:
    spec_of(project)

    response = fit(app.test_client(), app, "S1", "oxide", {"method": "amoeba"})

    assert response.status_code == 403
    assert app.config["NRW_MODELS"].jobs.current() is None


@pytest.mark.parametrize("offset", ["abc", "-3", "1" + "0" * 30, "²"])
def test_an_offset_that_is_not_a_byte_count_is_refused(app, offset: str) -> None:
    response = app.test_client().get(f"/api/experiment/jobs/current?offset={offset}")

    assert response.status_code == 400


def test_a_job_a_restart_found_running_is_shown_detached(project: Path) -> None:
    jobs = project / ".nrw" / "jobs"
    jobs.mkdir(parents=True)
    (jobs / "20260929T120000000000Z-abcdef.json").write_text(
        json.dumps(
            {
                "id": "20260929T120000000000Z-abcdef",
                "label": "dream fit of oxide",
                "sample": "S1",
                "model": "oxide",
                "steps": [["fit", "run", "samples/S1/models/oxide.py"]],
                "status": "running",
                "step": 1,
            }
        )
    )

    app = create_app(project, token=TOKEN, autostart=False)
    payload = app.test_client().get("/api/experiment/jobs/current").json

    assert payload["job"]["status"] == "detached"


# --------------------------------------------------------------------------
# A quick fit with AuRE
# --------------------------------------------------------------------------


def quick(client, app, sample: str, body: dict):
    return client.post(
        f"/api/experiment/samples/{sample}/models/quick-fit",
        json=body,
        headers=headers(app),
    )


def described(project: Path) -> None:
    """A sample.md AuRE can take a description from."""
    (project / "samples" / "S1" / "sample.md").write_text(
        "# S1\n\n## Description\n\nTi and Cu on silicon, in d8-THF.\n",
        encoding="utf-8",
    )


@pytest.mark.integration
def test_a_quick_fit_with_aure_is_recorded_as_a_fit_of_the_spec_it_proposed(
    app, writer, project: Path, monkeypatch
) -> None:
    from .test_aure_cmd import FITTED

    described(project)
    # Every step is the real command but AuRE's run, which needs a language
    # model: that one writes what a finished run leaves.
    finished = json.dumps(
        {
            "success": True,
            "error": None,
            "final_chi2": 1.8,
            "state": {"current_model": FITTED, "best_chi2": 1.8},
        }
    )

    def command(*args: str) -> list[str]:
        if args[:2] == ("aure", "run"):
            output = Path(args[2]).parent / "output"
            code = (
                f"import pathlib; p = pathlib.Path({str(output)!r}); "
                f"p.mkdir(parents=True); (p / 'final_state.json').write_text({finished!r})"
            )
            return [sys.executable, "-c", code]
        return serial(*args)

    monkeypatch.setattr(jobs_module, "nrw_command", command)

    started(quick(writer, app, "S1", {"name": "auto", "run": 100001}))
    payload = job_ended(writer)

    job, log = payload["job"], payload["log"]
    assert (job["status"], job["step"]) == ("ok", 5), log
    assert "$ nrw aure new --name=auto --run=100001 -- S1\n" in log
    assert "$ nrw aure run samples/S1/aure/auto/setup.yaml --budget=quick\n" in log
    # No --run: import reads the run from the setup AuRE was given.
    assert (
        "$ nrw aure import samples/S1/aure/auto/output --sample=S1 --name=auto\n" in log
    )
    assert (project / "samples" / "S1" / "models" / "auto.yaml").is_file()
    (recorded,) = FitIndex(project / ".nrw" / "index.jsonl").fits(sample="S1")
    assert recorded["fit_id"] == job["fit_id"]
    assert (recorded["model"], recorded["method"]) == ("auto", "amoeba")


@pytest.mark.integration
def test_a_quick_fit_without_an_endpoint_stops_at_aure_run_and_says_how_to_set_one(
    app, writer, project: Path, monkeypatch
) -> None:
    from nr_workbench import aure_adapter

    # Every step real. Conftest clears the variables AuRE reads, and the home
    # the steps see is empty -- but a machine where an endpoint is configured
    # anyway must never be billed for this test.
    if aure_adapter.llm_available():
        pytest.skip("a language-model endpoint is configured here")
    monkeypatch.delenv("NRW_AGENT", raising=False)
    described(project)

    started(quick(writer, app, "S1", {"name": "auto", "run": 100001}))
    payload = job_ended(writer)

    job, log = payload["job"], payload["log"]
    assert (job["status"], job["step"], job["fit_id"]) == ("failed", 2, None), log
    assert "no endpoint is configured" in log
    assert "nrw check-llm" in log
    assert not (project / "samples" / "S1" / "models" / "auto.yaml").exists()
    assert FitIndex(project / ".nrw" / "index.jsonl").fits(sample="S1") == []


def test_a_quick_fit_needs_aure_installed_and_says_so(
    app, writer, project: Path, monkeypatch
) -> None:
    from nr_workbench import aure_adapter

    monkeypatch.setattr(aure_adapter, "is_available", lambda: False)

    listed = app.test_client().get("/api/experiment/samples/S1/models").json
    response = quick(writer, app, "S1", {"name": "auto", "run": 100001})

    assert listed["aure"] is False
    assert response.status_code == 409
    assert "AuRE is not installed" in response.json["error"]
    assert app.config["NRW_MODELS"].jobs.current() is None


@pytest.mark.parametrize("taken", ["models/auto.yaml", "aure/auto"])
def test_a_quick_fit_never_writes_over_a_name_taken(
    app, writer, project: Path, taken: str
) -> None:
    path = project / "samples" / "S1" / taken
    path.parent.mkdir(parents=True, exist_ok=True)
    if taken.endswith(".yaml"):
        path.write_text("# mine\n")
    else:
        path.mkdir()

    response = quick(writer, app, "S1", {"name": "auto"})

    assert response.status_code == 409
    assert "already exists" in response.json["error"]
    assert app.config["NRW_MODELS"].jobs.current() is None


@pytest.mark.parametrize("run", ["100001", True, 0, -5, 1.5])
def test_a_quick_fit_run_must_be_a_run_number(app, writer, run) -> None:
    response = quick(writer, app, "S1", {"name": "auto", "run": run})

    assert response.status_code == 400
    assert app.config["NRW_MODELS"].jobs.current() is None


def test_the_runs_aure_can_fit_are_the_ones_the_commands_see(
    app, project: Path
) -> None:
    listed = app.test_client().get("/api/experiment/samples/S1/models").json
    # A file that is not data does not make a sample look measured.
    (project / "samples" / "S2" / "data" / "steady" / ".DS_Store").write_text("")
    empty = app.test_client().get("/api/experiment/samples/S2/models").json

    assert listed["runs"] == [100001, 100005]
    assert (empty["runs"], empty["has_data"]) == ([], False)
