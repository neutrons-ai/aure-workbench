"""The Experiment page and its API: the server's first write surface.

Until now `nrw serve` only read, bound to loopback, and needed no
authentication. The tests here are mostly about who may write -- a browser
that opened the one-time link, from this machine, with a JSON body, from this
page's own origin -- and that everything else still only reads.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

from nr_workbench.web.app import _compact_json, create_app

from .experiment_fixtures import write_autoreduced

TOKEN = "one-time-secret-for-tests"
ORIGIN = "http://localhost"


@pytest.fixture
def expt(project: Path, tmp_path: Path) -> Path:
    """The scaffolded project, watching a folder with runs in three states."""
    folder = tmp_path / "facility" / "new_reduction"
    for run in (234277, 234280):
        write_autoreduced(folder, run, [1, 2, 3], planned=3, mtime=time.time() - 3600)
    write_autoreduced(folder, 234283, [1], planned=3, mtime=time.time() - 3600)
    with (project / "nrw.toml").open("a", encoding="utf-8") as handle:
        handle.write(f'\n[experiment.source]\nlocation = "{folder}"\n')
    return project


def make_app(root: Path, **kwargs):
    app = create_app(root, token=TOKEN, autostart=False, **kwargs)
    app.config["NRW_EXPERIMENT"].live.scan_once()
    return app


@pytest.fixture
def app(expt: Path):
    app = make_app(expt)
    yield app
    app.config["NRW_EXPERIMENT"].stop()


@pytest.fixture
def writer(app):
    """A client that opened the one-time link."""
    client = app.test_client()
    response = client.get(f"/auth/{TOKEN}")
    assert response.status_code == 303
    return client


def write_headers(app, **extra) -> dict[str, str]:
    headers = {"X-NRW-Token": app.config["NRW_PAGE_TOKEN"], "Origin": ORIGIN}
    headers.update(extra)
    return headers


def assign(client, app, run: int = 234277, base_rev: int = 0, **headers):
    return client.put(
        "/api/experiment/runs",
        json={
            "changes": [
                {"run": run, "base_rev": base_rev, "fields": {"sample_id": "Sample6"}}
            ]
        },
        headers=write_headers(app, **headers),
    )


# --------------------------------------------------------------------------
# The data layer needs no web framework
# --------------------------------------------------------------------------


def test_experiment_data_imports_without_flask() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, nr_workbench.web.experiment; "
            "assert 'flask' not in sys.modules; print('clean')",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_the_api_mirrors_experiment_data(app) -> None:
    data = app.config["NRW_EXPERIMENT"]

    via_api = app.test_client().get("/api/experiment").get_json()
    direct = data.overview()

    for key in ("runs", "samples", "ipts", "cursor"):
        assert via_api[key] == direct[key], key


def test_overview_lists_runs_with_their_states(app) -> None:
    payload = app.test_client().get("/api/experiment").get_json()

    states = {row["run"]: row["state"] for row in payload["runs"]}
    assert states == {234277: "complete", 234280: "complete", 234283: "unconfirmed"}


def test_curves_come_from_the_source_segment_by_segment(app) -> None:
    payload = app.test_client().get("/api/experiment/runs/234277/curves").get_json()

    assert [c["label"] for c in payload["curves"]] == [
        "234277#1",
        "234277#2",
        "234277#3",
    ]
    assert all(c["run"] == 234277 for c in payload["curves"])


def test_curves_for_an_unlisted_run_are_404(app) -> None:
    assert (
        app.test_client().get("/api/experiment/runs/999999/curves").status_code == 404
    )


# --------------------------------------------------------------------------
# The page
# --------------------------------------------------------------------------


def _embedded(html: str, name: str) -> object:
    match = re.search(rf"window\.{name} = (.*?);\n", html)
    assert match, name
    return json.loads(match.group(1))


def test_the_page_renders_and_its_payload_parses(app) -> None:
    response = app.test_client().get("/experiment")

    assert response.status_code == 200
    payload = _embedded(response.get_data(as_text=True), "NRW_EXPERIMENT")
    assert {row["run"] for row in payload["runs"]} == {234277, 234280, 234283}


def test_the_page_forbids_framing_and_scripts_it_did_not_sign(app) -> None:
    response = app.test_client().get("/experiment")

    policy = response.headers["Content-Security-Policy"]
    nonce = re.search(r"'nonce-([^']+)'", policy).group(1)
    assert "frame-ancestors 'none'" in policy
    assert f'nonce="{nonce}"' in response.get_data(as_text=True)
    assert response.headers["X-Frame-Options"] == "DENY"


def test_every_html_page_forbids_framing(app) -> None:
    response = app.test_client().get("/")

    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_the_write_token_is_only_in_a_page_served_to_a_link_holder(app, writer) -> None:
    anonymous = app.test_client().get("/experiment").get_data(as_text=True)
    holder = writer.get("/experiment").get_data(as_text=True)

    assert _embedded(anonymous, "NRW_WRITE_TOKEN") == ""
    assert _embedded(holder, "NRW_WRITE_TOKEN") == app.config["NRW_PAGE_TOKEN"]


def test_a_hostile_run_title_cannot_break_out_of_the_script_block(
    project: Path, tmp_path: Path
) -> None:
    folder = tmp_path / "facility" / "new_reduction"
    write_autoreduced(
        folder,
        234277,
        [1],
        planned=1,
        mtime=time.time() - 3600,
        stem="x</script><script>alert(1)</script><!--<script>",
    )
    with (project / "nrw.toml").open("a", encoding="utf-8") as handle:
        handle.write(f'\n[experiment.source]\nlocation = "{folder}"\n')
    app = make_app(project)

    html = app.test_client().get("/experiment").get_data(as_text=True)

    script = html.split("window.NRW_EXPERIMENT = ", 1)[1].split(";\n", 1)[0]
    assert "<" not in script and ">" not in script
    assert "alert(1)" in _embedded(html, "NRW_EXPERIMENT")["runs"][0]["title"]


def test_compact_json_escapes_everything_that_can_end_a_script() -> None:
    text = _compact_json({"t": "</script><!--<script>& "})

    assert not set("<>& ") & set(text)
    assert json.loads(text) == {"t": "</script><!--<script>& "}


# --------------------------------------------------------------------------
# Who may write
# --------------------------------------------------------------------------


def test_a_link_holder_can_assign_runs(app, writer, expt: Path) -> None:
    response = assign(writer, app)

    assert response.status_code == 200, response.get_json()
    from nr_workbench.experiment.store import ParquetCatalogStore

    catalog = ParquetCatalogStore.for_project(expt).load()
    assert catalog.sample_ids() == ["Sample6"]


def test_a_write_without_the_link_is_refused(app) -> None:
    response = assign(app.test_client(), app)

    assert response.status_code == 403
    assert "link" in response.get_json()["error"]


def test_a_write_without_the_page_token_is_refused(app, writer) -> None:
    response = writer.put(
        "/api/experiment/runs", json={"changes": []}, headers={"Origin": ORIGIN}
    )

    assert response.status_code == 403


def test_a_text_plain_write_is_refused(app, writer) -> None:
    """A text/plain POST is a CORS 'simple request': no preflight protects it."""
    response = writer.put(
        "/api/experiment/runs",
        data='{"changes": []}',
        headers=write_headers(app, **{"Content-Type": "text/plain"}),
    )

    assert response.status_code == 415


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({"Origin": "http://localhost:3000"}, id="another_localhost_port"),
        pytest.param({"Origin": "null"}, id="null_origin"),
        pytest.param({"Origin": "https://evil.example"}, id="another_site"),
        pytest.param({"Sec-Fetch-Site": "cross-site"}, id="cross_site_fetch"),
    ],
)
def test_a_write_from_another_origin_is_refused(app, writer, headers) -> None:
    response = assign(writer, app, **headers)

    assert response.status_code == 403


def test_a_write_from_another_machine_is_refused_even_claiming_localhost(app) -> None:
    client = app.test_client()
    client.set_cookie("nrw_session", TOKEN, domain="localhost")

    response = client.put(
        "/api/experiment/runs",
        json={"changes": []},
        headers=write_headers(app),
        environ_base={"REMOTE_ADDR": "10.0.0.5"},
    )

    assert response.status_code == 403


def test_an_ipv4_mapped_loopback_address_is_this_machine() -> None:
    from nr_workbench.web.security import is_loopback

    assert is_loopback("::ffff:127.0.0.1")
    assert is_loopback("::1")
    assert not is_loopback("::ffff:10.0.0.5")


def test_an_oversized_write_is_refused(app, writer) -> None:
    response = writer.put(
        "/api/experiment/runs",
        data=json.dumps({"changes": [], "pad": "x" * (2 * 1024 * 1024)}),
        headers=write_headers(app, **{"Content-Type": "application/json"}),
    )

    assert response.status_code == 413


def test_a_stale_revision_is_a_409_and_changes_nothing(app, writer) -> None:
    assert assign(writer, app).status_code == 200

    response = writer.put(
        "/api/experiment/runs",
        json={
            "changes": [{"run": 234277, "base_rev": 0, "fields": {"condition": "CA"}}]
        },
        headers=write_headers(app),
    )

    assert response.status_code == 409


def test_a_rule_broken_is_a_400_with_the_reason(app, writer) -> None:
    response = writer.put(
        "/api/experiment/runs",
        json={
            "changes": [{"run": 234277, "base_rev": 0, "fields": {"condition": "a|b"}}]
        },
        headers=write_headers(app),
    )

    assert response.status_code == 400
    assert "'|'" in response.get_json()["error"]


def test_the_page_cannot_set_the_title_it_came_from_the_source(app, writer) -> None:
    response = writer.put(
        "/api/experiment/runs",
        json={"changes": [{"run": 234277, "base_rev": 0, "fields": {"title": "mine"}}]},
        headers=write_headers(app),
    )

    assert response.status_code == 400


def test_a_corrupt_catalog_is_shown_read_only_and_refuses_writes(
    app, writer, expt: Path
) -> None:
    assert assign(writer, app).status_code == 200
    (expt / "experiment" / "runs.parquet").write_bytes(b"")

    overview = writer.get("/api/experiment").get_json()
    assert overview["writable"] is False
    assert overview["catalog"]["readable"] is False
    assert any(p["scope"] == "catalog" for p in overview["problems"])

    assert assign(writer, app, base_rev=1).status_code == 503


def test_a_read_only_server_refuses_writes_and_the_link(expt: Path) -> None:
    app = make_app(expt, writable=False, read_only_reason="testing read-only")
    client = app.test_client()

    assert client.get(f"/auth/{TOKEN}").status_code == 403
    client.set_cookie("nrw_session", TOKEN, domain="localhost")
    response = assign(client, app)
    assert response.status_code == 403
    assert "testing read-only" in response.get_json()["error"]


def test_a_server_bound_beyond_loopback_never_writes(expt: Path) -> None:
    app = make_app(expt, bound_host="0.0.0.0")

    assert app.config["NRW_WRITABLE"] is False
    assert app.test_client().get(f"/auth/{TOKEN}").status_code == 403


# --------------------------------------------------------------------------
# Hosts, and read-only everywhere else
# --------------------------------------------------------------------------


@pytest.mark.parametrize("host", ["evil.example", "evil.example:8765", "0.0.0.0:8765"])
def test_a_foreign_host_name_is_refused_even_for_reads(app, host: str) -> None:
    """DNS rebinding makes a hostile page same-origin; its Host still names it."""
    response = app.test_client().get("/api/experiment", headers={"Host": host})

    assert response.status_code == 400


@pytest.mark.parametrize("host", ["localhost:8765", "127.0.0.1:8765", "[::1]:8765"])
def test_loopback_host_names_are_served(app, host: str) -> None:
    assert (
        app.test_client().get("/api/experiment", headers={"Host": host}).status_code
        == 200
    )


def test_every_non_get_route_is_behind_the_write_gate(app) -> None:
    """The one blueprint that writes is the only one allowed to."""
    unsafe = {
        rule.endpoint
        for rule in app.url_map.iter_rules()
        if rule.methods - {"GET", "HEAD", "OPTIONS"}
    }
    assert unsafe
    assert all(endpoint.startswith("experiment_api.") for endpoint in unsafe), unsafe


def test_reads_change_nothing_in_the_project(app, writer, expt: Path) -> None:
    """A GET can be triggered by an <img> tag; it must never write."""
    assert assign(writer, app).status_code == 200
    before = {
        p: p.stat().st_mtime_ns
        for p in expt.rglob("*")
        if p.is_file() and ".nrw/cache" not in p.as_posix()
    }
    for path in (
        "/api/experiment",
        "/api/experiment/changes?since=x",
        "/api/experiment/runs/234277/curves",
        "/api/experiment/samples/Sample6/preview",
        "/api/experiment/apply",
        "/experiment",
    ):
        assert writer.get(path).status_code == 200, path

    after = {p: p.stat().st_mtime_ns for p in before}
    assert after == before
    assert not (expt / "samples" / "Sample6").exists()


# --------------------------------------------------------------------------
# Apply and preview through the API
# --------------------------------------------------------------------------


def test_apply_through_the_api_copies_the_reviewed_plan(
    app, writer, expt: Path
) -> None:
    assert assign(writer, app).status_code == 200
    plan = writer.get("/api/experiment/apply").get_json()
    assert plan["writes"]

    response = writer.post(
        "/api/experiment/apply",
        json={"plan_id": plan["plan_id"]},
        headers=write_headers(app),
    )

    assert response.status_code == 200, response.get_json()
    steady = expt / "samples" / "Sample6" / "data" / "steady"
    assert len(list(steady.glob("REFL_234277_*"))) == 3


def test_apply_with_a_stale_plan_is_a_409(app, writer) -> None:
    assert assign(writer, app).status_code == 200

    response = writer.post(
        "/api/experiment/apply", json={"plan_id": "stale"}, headers=write_headers(app)
    )

    assert response.status_code == 409


def test_the_preview_shows_what_sample_md_would_become(app, writer) -> None:
    assert assign(writer, app).status_code == 200

    preview = writer.get("/api/experiment/samples/Sample6/preview").get_json()

    assert preview["state"] == "create"
    assert "| 234277 |" in preview["markdown"]
    assert "<table>" in preview["html"]


def test_a_sample_id_with_a_path_in_it_is_refused(app) -> None:
    response = app.test_client().get("/api/experiment/samples/..%2Fetc/preview")

    assert response.status_code in (400, 404)


# --------------------------------------------------------------------------
# `nrw serve`
# --------------------------------------------------------------------------


def _serve(expt: Path, *args: str, env: dict[str, str] | None = None, monkeypatch=None):
    from click.testing import CliRunner
    from flask import Flask

    from nr_workbench.cli import main

    monkeypatch.setattr(Flask, "run", lambda self, **kwargs: None)
    monkeypatch.delenv("NRW_SERVE_TOKEN", raising=False)
    return CliRunner().invoke(
        main, ["serve", "--root", str(expt), *args], env=env or {}
    )


def test_serve_prints_the_one_time_link(expt: Path, monkeypatch) -> None:
    result = _serve(expt, monkeypatch=monkeypatch)

    assert result.exit_code == 0, result.output
    assert "/auth/" in result.output


def test_serve_beyond_loopback_says_view_only_and_prints_no_link(
    expt: Path, monkeypatch
) -> None:
    result = _serve(expt, "--host", "0.0.0.0", monkeypatch=monkeypatch)

    assert result.exit_code == 0, result.output
    assert "/auth/" not in result.output
    assert "not edited" in result.output


def test_serve_under_an_agent_accepts_no_edits(expt: Path, monkeypatch) -> None:
    result = _serve(expt, env={"NRW_AGENT": "1"}, monkeypatch=monkeypatch)

    assert "/auth/" not in result.output
    assert "does not accept edits" in result.output


def test_serve_debug_beyond_loopback_is_refused(expt: Path, monkeypatch) -> None:
    """The Werkzeug debugger runs whatever it is sent."""
    result = _serve(expt, "--debug", "--host", "0.0.0.0", monkeypatch=monkeypatch)

    assert result.exit_code != 0
    assert "run code on this machine" in result.output
