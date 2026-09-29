"""The Experiment page in a real browser: what someone typed survives a save made elsewhere.

The catalog can be saved while the editor is open -- from another tab, the
command line, a `git pull`. The page reloads then, and on the conflict the
next save meets; each reload used to fill the editor afresh, wiping what had
been typed, or leaving stale values for that save to write back.

Headless Chrome over DevTools pipes, as in ``test_settings_page_browser``;
skipped where there is no Chrome.
"""

# The browser fixtures are imported and requested by name, which ruff reads
# as redefinitions.
# ruff: noqa: F811

from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from click.testing import CliRunner

from nr_workbench.cli import main
from nr_workbench.experiment.model import RunChange, RunKey, SampleChange
from nr_workbench.experiment.store import ParquetCatalogStore

from .experiment_fixtures import write_autoreduced
from .test_lifecycle import write_partials
from .test_settings_page_browser import (  # noqa: F401 - fixtures
    Page,
    Site,
    browser,
    page,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A project watching a folder, with one sample in its catalog."""
    from nr_workbench.project.settings import save

    root = tmp_path / "analysis"
    assert CliRunner().invoke(main, ["init", str(root)]).exit_code == 0
    facility = tmp_path / "facility"
    write_autoreduced(facility, 234277, [1, 2, 3], mtime=time.time() - 3600)
    save(root, {"ipts": "IPTS-00001", "source.location": str(facility)})
    ParquetCatalogStore.for_project(root).update(
        runs=[RunChange(RunKey(234277), 0, {"sample_id": "S1"})],
        samples=[SampleChange("S1", 0, {"title": "Mine", "description": ""})],
    )
    return root


@pytest.fixture
def site(project: Path) -> Iterator[Site]:
    from werkzeug.serving import make_server

    from nr_workbench.web.app import create_app

    token = secrets.token_urlsafe(24)
    app = create_app(project, token=token, autostart=False)
    app.config["NRW_EXPERIMENT"].live.scan_once()
    server = make_server("127.0.0.1", 0, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}"
    try:
        yield Site(url=url, link=f"{url}/auth/{token}")
    finally:
        server.shutdown()
        app.config["NRW_EXPERIMENT"].reload()


def field(page: Page, element_id: str) -> str:
    return page.js(f"document.getElementById('{element_id}').value")


def test_what_you_typed_survives_a_save_of_the_same_sample_made_elsewhere(
    page: Page, site: Site, project: Path
) -> None:
    page.goto(site.link)
    page.goto(f"{site.url}/experiment")
    page.wait_for(
        "Array.from(document.querySelectorAll('#expt-samples button'))"
        ".some(b => b.textContent.includes('S1'))",
        what="the sample list",
    )
    page.js(
        "Array.from(document.querySelectorAll('#expt-samples button'))"
        ".find(b => b.textContent.includes('S1')).click()"
    )
    page.wait_for("!document.getElementById('f-description').disabled", what="editor")
    page.type("f-description", "typed by me")

    # The same sample is saved elsewhere -- another field -- in the meantime.
    store = ParquetCatalogStore.for_project(project)
    rev = store.load().samples["S1"].rev
    store.update(samples=[SampleChange("S1", rev, {"title": "Their title"})])

    page.click("expt-save")  # meets the conflict, and the page reloads

    page.wait_for(
        "document.getElementById('f-title').value === 'Their title'",
        what="the reload with their title",
    )
    assert field(page, "f-description") == "typed by me"
    assert "saved somewhere else" in page.text("expt-form-status")

    page.click("expt-save")

    page.wait_for(
        "document.getElementById('expt-form-status').textContent.startsWith('Saved')",
        what="the save",
    )
    saved = ParquetCatalogStore.for_project(project).load().samples["S1"]
    assert (saved.title, saved.description) == ("Their title", "typed by me")


def empty_home(project: Path, monkeypatch) -> None:
    """An empty home for the nrw processes a test starts: a child reads
    ``~/.nrw`` and ``~/.aure`` afresh, and a real endpoint key there bills."""
    home = project.parent / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))


def open_s1(page: Page, site: Site) -> None:
    page.goto(site.link)
    page.goto(f"{site.url}/experiment")
    page.wait_for(
        "Array.from(document.querySelectorAll('#expt-samples button'))"
        ".some(b => b.textContent.includes('S1'))",
        what="the sample list",
    )
    page.js(
        "Array.from(document.querySelectorAll('#expt-samples button'))"
        ".find(b => b.textContent.includes('S1')).click()"
    )
    page.wait_for(
        "document.querySelector('#f-measurements .expt-measurement') !== null"
        " && !document.querySelector('#f-measurements select').disabled",
        what="the sample's measurements",
    )


def in_row(selector: str) -> str:
    return f"document.querySelector('#f-measurements .expt-measurement {selector}')"


def test_each_runs_type_condition_notes_and_good_switch_are_saved(
    page: Page, site: Site, project: Path
) -> None:
    open_s1(page, site)
    assert page.js(in_row("select") + ".value") == "steady"  # never set: steady
    assert page.js(in_row("[data-field=include]") + ".checked") is True

    page.js(
        f"{{ const s = {in_row('select')}; s.value = '__new__';"
        " s.dispatchEvent(new Event('change')); }"
    )
    page.js(f"{in_row('.expt-type-field input')}.value = 'grazing'")
    page.js(f"{in_row('[data-field=condition]')}.value = 'OCV'")
    page.js(f"{in_row('[data-field=note]')}.value = 'Beam dropped halfway.'")
    page.js(
        f"{{ const g = {in_row('[data-field=include]')}; g.checked = false;"
        " g.dispatchEvent(new Event('change')); }"
    )
    assert "bad" in page.js(in_row(".form-check-label") + ".textContent")
    page.click("expt-save")

    page.wait_for(
        "document.getElementById('expt-form-status').textContent.startsWith('Saved')",
        what="the save",
    )
    entry = ParquetCatalogStore.for_project(project).load().runs[RunKey(234277)]
    assert (entry.measurement, entry.condition, entry.note, entry.include) == (
        "grazing",
        "OCV",
        "Beam dropped halfway.",
        False,
    )
    # The type added is a choice from now on, in the bulk bar too.
    assert page.js(
        "Array.from(document.querySelectorAll('#expt-type-slot option'))"
        ".some(o => o.value === 'grazing')"
    )


def test_a_run_changed_elsewhere_keeps_the_notes_being_typed(
    page: Page, site: Site, project: Path
) -> None:
    open_s1(page, site)
    page.js(f"{in_row('[data-field=note]')}.value = 'realigned after mounting'")
    store = ParquetCatalogStore.for_project(project)
    entry = store.load().runs[RunKey(234277)]
    store.update(runs=[RunChange(entry.key, entry.rev, {"condition": "CA"})])

    page.click("expt-save")  # meets the conflict, and the page reloads

    page.wait_for(
        in_row("[data-field=condition]") + ".value === 'CA'",
        what="the reload with the condition saved elsewhere",
    )
    assert page.js(in_row("[data-field=note]") + ".value") == "realigned after mounting"
    assert "saved somewhere else" in page.js(
        "document.getElementById('expt-form-status').textContent"
    )
    page.click("expt-save")
    page.wait_for(
        "document.getElementById('expt-form-status').textContent.startsWith('Saved')",
        what="the second save",
    )
    saved = ParquetCatalogStore.for_project(project).load().runs[RunKey(234277)]
    assert (saved.condition, saved.note) == ("CA", "realigned after mounting")


def test_the_bulk_bar_leaves_each_runs_type_unless_one_is_chosen(
    page: Page, site: Site, project: Path
) -> None:
    store = ParquetCatalogStore.for_project(project)
    entry = store.load().runs[RunKey(234277)]
    store.update(runs=[RunChange(entry.key, entry.rev, {"measurement": "tNR"})])
    page.goto(site.link)
    page.goto(f"{site.url}/experiment")
    page.wait_for(
        "document.querySelector('#expt-runs tbody input[type=checkbox]') !== null",
        what="the runs",
    )

    def assign_selected() -> None:
        page.js(
            "{ const b = document.querySelector('#expt-runs tbody input[type=checkbox]');"
            " if (!b.checked) { b.checked = true; b.dispatchEvent(new Event('change')); } }"
        )
        page.js(
            "{ const s = document.getElementById('expt-sample'); s.value = 'S1';"
            " s.dispatchEvent(new Event('change')); }"
        )
        page.click("expt-assign")
        page.wait_for(
            "document.getElementById('expt-message').textContent.startsWith('Assigned')",
            what="the assignment",
        )
        page.js("document.getElementById('expt-message').textContent = ''")

    assign_selected()  # the type chooser left at "unchanged"
    assert store.load().runs[RunKey(234277)].measurement == "tNR"

    page.js(
        "{ const b = document.querySelector('#expt-runs tbody input[type=checkbox]');"
        " b.checked = true; b.dispatchEvent(new Event('change'));"
        " const t = document.querySelector('#expt-type-slot select'); t.value = 'steady'; }"
    )
    assign_selected()
    assert store.load().runs[RunKey(234277)].measurement == "steady"


def test_a_model_is_written_from_the_samples_data_once_it_has_some(
    page: Page, site: Site, project: Path, monkeypatch
) -> None:
    empty_home(project, monkeypatch)
    open_s1(page, site)
    page.wait_for(
        "!document.getElementById('expt-models').classList.contains('d-none')",
        what="the models panel",
    )
    assert "Apply first" in page.text("expt-models-list")
    assert page.js("document.getElementById('expt-model-create').disabled") is True

    # What Apply copies in: the sample's data, on disk.
    write_partials(project / "samples" / "S1" / "data" / "steady", 234277)
    page.js(
        "Array.from(document.querySelectorAll('#expt-samples button'))"
        ".find(b => b.textContent.includes('S1')).click()"
    )
    page.wait_for(
        "!document.getElementById('expt-model-create').disabled", what="the data seen"
    )
    page.type("expt-model-name", "oxide")
    page.click("expt-model-create")

    page.wait_for(
        "document.getElementById('expt-model-status').textContent.startsWith('Wrote')",
        what="the spec written",
    )
    assert (project / "samples" / "S1" / "models" / "oxide.yaml").is_file()
    assert "samples/S1/models/oxide.yaml" in page.text("expt-models-list")
    assert "Wrote samples/S1/models/oxide.yaml" in page.text("expt-model-output")


def test_a_fit_started_on_the_page_is_followed_to_its_record(
    page: Page, site: Site, project: Path, monkeypatch
) -> None:
    from nr_workbench.web import jobs as jobs_module

    from .test_web_models import serial

    empty_home(project, monkeypatch)
    monkeypatch.setattr(jobs_module, "nrw_command", serial)
    write_partials(project / "samples" / "S1" / "data" / "steady", 234277)
    open_s1(page, site)
    page.wait_for(
        "!document.getElementById('expt-model-create').disabled", what="the data seen"
    )
    page.type("expt-model-name", "oxide")
    page.click("expt-model-create")
    page.wait_for(
        "document.querySelector('#expt-models-list .expt-model-fit') !== null",
        what="the spec listed",
    )

    page.js("document.querySelector('#expt-models-list .expt-model-fit').click()")
    page.type("expt-fit-steps", "3")
    page.click("expt-fit-run")

    page.wait_for(
        "document.getElementById('expt-job-result').textContent.includes('Recorded as')",
        what="the fit recorded",
        timeout=120,
    )
    href = page.js("document.querySelector('#expt-job-result a').getAttribute('href')")
    from nr_workbench.provenance.index import FitIndex

    (recorded,) = FitIndex(project / ".nrw" / "index.jsonl").fits(sample="S1")
    assert href == f"/f/{recorded['fit_id']}"
    assert "$ nrw fit run samples/S1/models/oxide.py" in page.text("expt-job-log")
    assert page.text("expt-job-state") == "ok"


def test_a_fit_is_cancelled_from_the_page(
    page: Page, site: Site, project: Path, monkeypatch
) -> None:
    import sys

    from nr_workbench.web import jobs as jobs_module

    # A step that runs until it is stopped, standing in for a long DREAM run.
    monkeypatch.setattr(
        jobs_module,
        "nrw_command",
        lambda *args: [sys.executable, "-c", "import time; time.sleep(60)"],
    )
    spec = project / "samples" / "S1" / "models" / "oxide.yaml"
    spec.parent.mkdir(parents=True)
    spec.write_text("{}\n")
    (project / "samples" / "S1" / "data" / "steady").mkdir(parents=True)
    open_s1(page, site)
    page.wait_for(
        "document.querySelector('#expt-models-list .expt-model-fit') !== null",
        what="the spec listed",
    )
    page.js("document.querySelector('#expt-models-list .expt-model-fit').click()")
    page.click("expt-fit-run")
    page.wait_for(
        "document.getElementById('expt-job-state').textContent === 'step 1 of 2'",
        what="the job running",
    )

    page.click("expt-job-cancel")

    page.wait_for(
        "document.getElementById('expt-job-state').textContent === 'cancelled'",
        what="the job cancelled",
    )
    assert "interrupted run" in page.text("expt-job-result")
    assert page.js(
        "document.getElementById('expt-job-cancel').classList.contains('d-none')"
    )


def test_a_quick_fit_with_aure_is_started_for_the_run_chosen(
    page: Page, site: Site, project: Path, monkeypatch
) -> None:
    import sys

    from nr_workbench.web import jobs as jobs_module

    # Each step says what it was asked, and succeeds: what is tested is the
    # page -- the run chosen, the job followed -- not AuRE.
    monkeypatch.setattr(
        jobs_module,
        "nrw_command",
        lambda *args: [sys.executable, "-c", f"print({' '.join(args)!r})"],
    )
    steady = project / "samples" / "S1" / "data" / "steady"
    write_partials(steady, 234277)
    write_partials(steady, 234280)
    open_s1(page, site)
    page.wait_for(
        "!document.getElementById('expt-model-run').classList.contains('d-none')",
        what="the choice of run",
    )
    page.js("document.getElementById('expt-model-run').value = '234280'")
    page.type("expt-model-name", "auto")
    page.click("expt-model-quick")

    page.wait_for(
        "document.getElementById('expt-job-state').textContent === 'ok'",
        what="the quick fit ended",
    )
    assert page.text("expt-job-title") == "Quick fit of auto with AuRE (S1)"
    log = page.text("expt-job-log")
    assert "aure new --name=auto --run=234280 -- S1" in log
    assert log.index("aure run") < log.index("aure import") < log.index("fit run")
