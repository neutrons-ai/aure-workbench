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
