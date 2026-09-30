"""Publishing a finalized fit to ISAAC: `nrw isaac push`, and the page's panel.

`nr-isaac-format` is stood in for by a script printing what the real one
prints, one line per record the portal made, so what is under test is nrw's
side: only the final fit is published; what is pushed is what the export
wrote; each push -- a half-failed one included -- is recorded with the fit; the
key is read as every setting is and never shown; and an agent publishes
nothing.
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from click.testing import CliRunner

from nr_workbench import env as env_module
from nr_workbench.cli import main
from nr_workbench.commands import isaac_cmd
from nr_workbench.project.layout import ProjectLayout
from nr_workbench.provenance import curation
from nr_workbench.web import jobs as jobs_module
from nr_workbench.web.app import create_app

from .test_curation import A, B, events, recorded

TOKEN = "t" * 32
ORIGIN = "http://localhost"

#: The key ~/.nrw holds: it must reach the tool, and never the page.
PLANTED = "not-a-real-one-q2w8"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    assert (
        CliRunner().invoke(main, ["init", str(root), "--sample", "S1"]).exit_code == 0
    )
    recorded(root, A)
    recorded(root, B)
    return root


@pytest.fixture
def settings(tmp_path: Path, monkeypatch) -> Iterator[Path]:
    """~/.nrw with the portal and its key, as a person would set them; and
    whatever loading it puts in this process's environment, taken out again."""
    home = tmp_path / "home"
    home.mkdir()
    (home / ".nrw").write_text(
        f"ISAAC_URL=https://isaac.example.org/portal/api\nISAAC_KEY={PLANTED}\n"
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(env_module, "USER_ENV_PATH", home / ".nrw")
    saved = dict(os.environ)
    try:
        yield home
    finally:
        os.environ.clear()
        os.environ.update(saved)


def exported(project: Path, fit_id: str, count: int = 2) -> Path:
    records = project / "samples" / "S1" / "results" / fit_id / "isaac" / "records"
    records.mkdir(parents=True)
    for i in range(count):
        (records / f"isaac_record_state{i}.json").write_text("{}")
    return records


def portal(monkeypatch, *, fail_after: int | None = None) -> None:
    """Stand in for `nr-isaac-format push`: each record made, as it says so,
    with the key's last four as its id -- so a test sees the key arrived."""
    code = (
        "import os, sys, pathlib\n"
        "files = sorted(pathlib.Path(sys.argv[2]).glob('*.json'))\n"
        "if '--validate-only' in sys.argv:\n"
        "    [print(f'  ✓ {f.name}: valid') for f in files]; sys.exit(0)\n"
        "key = os.environ.get('ISAAC_KEY', 'none')[-4:]\n"
        f"stop = {fail_after!r}\n"
        "for n, f in enumerate(files):\n"
        "    if stop is not None and n >= stop:\n"
        "        print(f'  ✗ {f.name}: network error', file=sys.stderr); sys.exit(1)\n"
        # Coloured as a tool that forces colour would: the name itself, where
        # a code would otherwise become part of the recorded file name.
        "    print(f'  ✓ \\x1b[1m{f.name}\\x1b[0m: created (record_id={key}-{n})')\n"
    )
    monkeypatch.setattr(
        isaac_cmd, "_find", lambda names, install: [sys.executable, "-c", code]
    )


def nrw(project: Path, monkeypatch, *args: str):
    monkeypatch.chdir(project)
    return CliRunner().invoke(main, list(args))


def finalize(project: Path, fit_id: str = A) -> None:
    curation.promote(ProjectLayout(root=project), fit_id, reason="the answer")


# --------------------------------------------------------------------------
# From the terminal
# --------------------------------------------------------------------------


def test_only_the_final_fit_is_published_but_any_can_be_validated(
    project: Path, settings: Path, monkeypatch
) -> None:
    exported(project, A)
    portal(monkeypatch)

    refused = nrw(project, monkeypatch, "isaac", "push", A, "--yes")
    validated = nrw(project, monkeypatch, "isaac", "push", A, "--validate-only")

    assert refused.exit_code != 0
    assert "only a finalized fit is published" in refused.output
    assert validated.exit_code == 0, validated.output
    assert "valid" in validated.output
    assert events(project, "publish") == []


def test_a_push_sends_what_was_exported_and_records_what_the_portal_made(
    project: Path, settings: Path, monkeypatch
) -> None:
    exported(project, A)
    finalize(project)
    portal(monkeypatch)

    result = nrw(project, monkeypatch, "isaac", "push", A, "--yes")

    assert result.exit_code == 0, result.output
    (push,) = events(project, "publish")
    # The key reached the tool from ~/.nrw -- nrw's own settings, not a
    # .env beside the tool -- and the records the portal made are kept.
    assert push["records"] == [
        {"file": "isaac_record_state0.json", "record_id": "q2w8-0"},
        {"file": "isaac_record_state1.json", "record_id": "q2w8-1"},
    ]
    assert (push["portal"], push["complete"]) == ("isaac.example.org", True)
    assert push["who"] and push["at"]
    assert PLANTED not in json.dumps(push)
    assert "\x1b[" not in result.output  # the tool's colours, not echoed raw


def test_a_push_that_fails_half_way_still_records_what_was_made(
    project: Path, settings: Path, monkeypatch
) -> None:
    exported(project, A, count=3)
    finalize(project)
    portal(monkeypatch, fail_after=1)

    result = nrw(project, monkeypatch, "isaac", "push", A, "--yes")

    assert result.exit_code != 0
    assert "1 record(s) were made before it failed" in result.output
    (push,) = events(project, "publish")
    assert (len(push["records"]), push["complete"]) == (1, False)


def test_pushing_again_says_it_adds_records_and_asks(
    project: Path, settings: Path, monkeypatch
) -> None:
    exported(project, A)
    finalize(project)
    portal(monkeypatch)
    nrw(project, monkeypatch, "isaac", "push", A, "--yes")

    monkeypatch.chdir(project)
    declined = CliRunner().invoke(main, ["isaac", "push", A], input="n\n")

    assert "published before" in declined.output
    assert "Not uploaded" in declined.output
    assert len(events(project, "publish")) == 1


def test_nothing_exported_is_nothing_to_push(
    project: Path, settings: Path, monkeypatch
) -> None:
    finalize(project)

    result = nrw(project, monkeypatch, "isaac", "push", A, "--yes")

    assert result.exit_code != 0
    assert f"nrw isaac export {A}" in result.output


def test_an_agent_publishes_nothing(project: Path, settings: Path, monkeypatch) -> None:
    from nr_workbench.agent.guard import judge

    exported(project, A)
    finalize(project)
    portal(monkeypatch)
    monkeypatch.setenv("NRW_AGENT", "1")

    refused = nrw(project, monkeypatch, "isaac", "push", A, "--yes")

    assert refused.exit_code != 0
    assert events(project, "publish") == []
    assert judge(f"nrw isaac push {A} --yes").rule == "upload"
    assert judge(f"nrw isaac push {A} --validate-only").allowed


def test_a_published_fits_files_are_never_deleted(
    project: Path, settings: Path, monkeypatch
) -> None:
    records = exported(project, A)
    finalize(project)
    portal(monkeypatch)
    nrw(project, monkeypatch, "isaac", "push", A, "--yes")
    # Its export removed by hand, another fit final, it set aside: the portal
    # still holds what it was given.
    for path in records.iterdir():
        path.unlink()
    records.rmdir()
    records.parent.rmdir()
    layout = ProjectLayout(root=project)
    finalize(project, B)
    curation.discard(layout, A, reason="superseded")

    with pytest.raises(curation.CurationRefused, match="pushed from it"):
        curation.delete_files(layout, A)


# --------------------------------------------------------------------------
# From the page
# --------------------------------------------------------------------------


@pytest.fixture
def app(project: Path, settings: Path):
    app = create_app(project, token=TOKEN, autostart=False)
    yield app
    app.config["NRW_MODELS"].jobs.stop()


@pytest.fixture
def writer(app):
    client = app.test_client()
    assert client.get(f"/auth/{TOKEN}").status_code == 303
    return client


def step(client, app, fit_id: str, name: str, body: dict | None = None):
    return client.post(
        f"/api/experiment/fits/{fit_id}/isaac/{name}",
        json=body or {},
        headers={"X-NRW-Token": app.config["NRW_PAGE_TOKEN"], "Origin": ORIGIN},
    )


def test_the_panel_says_where_the_portal_and_key_are_set_and_never_the_key(
    app, project: Path
) -> None:
    exported(project, A)
    finalize(project)

    shown = app.test_client().get(f"/api/experiment/fits/{A}/isaac").json

    assert shown["final"] is True
    assert shown["portal"] == {"host": "isaac.example.org", "from": "~/.nrw"}
    assert shown["key"] == {"set": True, "from": "~/.nrw"}
    assert shown["records"] == ["isaac_record_state0.json", "isaac_record_state1.json"]
    assert PLANTED[-4:] not in json.dumps(shown)


def test_each_step_is_a_job_and_only_for_the_final_fit(
    app, writer, project: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        jobs_module, "nrw_command", lambda *args: [sys.executable, "-c", "pass"]
    )
    not_final = step(writer, app, A, "export")
    finalize(project)
    early = step(writer, app, A, "validate")
    export = step(writer, app, A, "export")
    jobs = app.config["NRW_MODELS"].jobs
    deadline = time.monotonic() + 30
    while jobs.current().status == "running" and time.monotonic() < deadline:
        time.sleep(0.05)
    exported(project, A)
    unconfirmed = step(writer, app, A, "push")
    pushed = step(writer, app, A, "push", {"confirm": A})

    assert (
        not_final.status_code == 409 and "Finalize it first" in not_final.json["error"]
    )
    assert early.status_code == 409 and "export them first" in early.json["error"]
    assert export.status_code == 202, export.json
    assert export.json["job"]["steps"] == [["isaac", "export", A]]
    assert unconfirmed.status_code == 400
    assert pushed.status_code == 202, pushed.json
    assert pushed.json["job"]["steps"] == [["isaac", "push", A, "--yes"]]


def test_publishing_from_the_page_needs_the_link_and_a_writable_server(
    app, project: Path, settings: Path
) -> None:
    exported(project, A)
    finalize(project)
    read_only = create_app(project, token=TOKEN, autostart=False, writable=False)
    viewer = read_only.test_client()
    viewer.get(f"/auth/{TOKEN}")

    stranger = step(app.test_client(), app, A, "push", {"confirm": A})
    refused = step(viewer, read_only, A, "push", {"confirm": A})

    assert (stranger.status_code, refused.status_code) == (403, 403)
    assert app.config["NRW_MODELS"].jobs.current() is None


def test_nrw_doctor_says_the_key_is_set_and_never_what_it_is(
    project: Path, settings: Path, monkeypatch
) -> None:
    result = nrw(project, monkeypatch, "doctor", "--json")

    reported = next(c for c in json.loads(result.output) if c["name"] == "llm settings")
    assert "ISAAC_KEY=set" in reported["detail"]
    assert "ISAAC_URL=https://isaac.example.org/portal/api" in reported["detail"]
    assert PLANTED not in result.output
