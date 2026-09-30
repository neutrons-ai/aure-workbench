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
        {
            "name": "a",
            "spec": "samples/S1/models/a.yaml",
            "script": True,
            "proposed": False,
        }
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
        {"method": "amoeba", "note": "## Next steps"},
        {"method": "amoeba", "note": "see <!-- here"},
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


def test_a_quick_fit_never_replaces_a_spec_someone_wrote(
    app, writer, project: Path
) -> None:
    spec = spec_of(project, "auto")
    spec.write_text("# mine\n")

    response = quick(writer, app, "S1", {"name": "auto"})

    assert response.status_code == 409
    assert "edited since AuRE proposed it" in response.json["error"]
    assert spec.read_text() == "# mine\n"
    assert app.config["NRW_MODELS"].jobs.current() is None


def test_an_earlier_run_of_aure_is_kept_and_a_new_one_gets_its_own_folder(
    app, writer, project: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        jobs_module, "nrw_command", lambda *args: [sys.executable, "-c", "pass"]
    )
    for kept in ("auto", "auto-2"):
        (project / "samples" / "S1" / "aure" / kept).mkdir(parents=True)

    started(quick(writer, app, "S1", {"name": "auto", "run": 100001}))

    log = job_ended(writer)["log"]
    assert "$ nrw aure new --name=auto-3 --run=100001 -- S1\n" in log
    assert "aure run samples/S1/aure/auto-3/setup.yaml" in log
    # The spec is new, so there is nothing to replace.
    assert "--replace-unedited" not in log


def test_a_model_aure_proposed_is_quick_fitted_again_replacing_only_the_unedited(
    app, writer, project: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        jobs_module, "nrw_command", lambda *args: [sys.executable, "-c", "pass"]
    )
    proposed = spec_of(project, "auto")
    proposed.write_text(unedited_proposal(), encoding="utf-8")
    (project / "samples" / "S1" / "aure" / "auto").mkdir(parents=True)

    listed = app.test_client().get("/api/experiment/samples/S1/models").json
    started(quick(writer, app, "S1", {"name": "auto", "run": 100001}))

    assert [m["proposed"] for m in listed["models"] if m["name"] == "auto"] == [True]
    payload = job_ended(writer)
    assert payload["job"]["label"] == "quick fit of auto with AuRE again"
    assert (
        "$ nrw aure import samples/S1/aure/auto-2/output --sample=S1 --name=auto "
        "--replace-unedited\n"
    ) in payload["log"]


def unedited_proposal() -> str:
    """A spec as `nrw aure import` writes it: its header, and its self-hash."""
    from nr_workbench.aure_import import PROPOSED_MARKER
    from nr_workbench.codegen.generator import stamp_self_hash

    return stamp_self_hash(
        f"# The stack below {PROPOSED_MARKER} 0.1 @ abc,\n"
        f"#   self sha256: {'0' * 64}  (nrw:self)\n"
        "schema: nrw-model/1\n"
    )


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


# --------------------------------------------------------------------------
# The project's own fit settings
# --------------------------------------------------------------------------


def fit_settings(project: Path, text: str) -> None:
    with (project / "nrw.toml").open("a", encoding="utf-8") as handle:
        handle.write("\n" + text)


def test_the_fit_form_starts_from_the_projects_nrw_toml(app, project: Path) -> None:
    fit_settings(project, '[fit]\nmethod = "de"\n[fit.dream]\nsamples = 20000\n')

    listed = app.test_client().get("/api/experiment/samples/S1/models").json

    assert listed["fit"]["method"] == "de"
    assert listed["fit"]["settings"]["dream"] == {"samples": 20000}
    assert listed["fit"]["problem"] is None


def test_the_fit_form_starts_at_dream_when_nrw_toml_says_nothing(app) -> None:
    from nr_workbench.fitting.settings import BUMPS_DEFAULTS

    listed = app.test_client().get("/api/experiment/samples/S1/models").json

    assert listed["fit"]["method"] == "dream"
    # What each box starts at when nrw.toml says nothing, and which it shows.
    assert listed["fit"]["bumps"] == BUMPS_DEFAULTS
    assert listed["fit"]["takes"] == {
        "amoeba": ["steps"],
        "de": ["steps"],
        "dream": ["samples", "burn", "steps"],
    }


def test_a_fit_asked_for_without_a_fitter_uses_the_projects(
    app, writer, project: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        jobs_module, "nrw_command", lambda *args: [sys.executable, "-c", "pass"]
    )
    fit_settings(project, '[fit]\nmethod = "de"\n')
    spec_of(project)

    started(fit(writer, app, "S1", "oxide", {}))

    assert "--method=de" in job_ended(writer)["log"]


def test_no_fit_starts_while_nrw_toml_fit_cannot_be_read(
    app, writer, project: Path
) -> None:
    fit_settings(project, '[fit]\nmethod = "lm"\n')
    spec_of(project)

    listed = app.test_client().get("/api/experiment/samples/S1/models").json
    response = fit(writer, app, "S1", "oxide", {"method": "amoeba"})

    assert "not available" in listed["fit"]["problem"]
    assert response.status_code == 409
    assert "nrw.toml" in response.json["error"]
    assert app.config["NRW_MODELS"].jobs.current() is None
