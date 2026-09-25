"""The Settings page in a real browser: what settings.js does with each answer.

The API tests prove what the server says; these prove the page acts on it --
lands on Settings from the one-time link, shows the folder check's per-run
findings, reloads on a conflict and only then, asks before a change the server
wants confirmed and asks once, and shows the lines to add when nrw.toml cannot
be edited. Each also fails on any script error or security violation the page
raises, which is how a broken nonce or a typo in settings.js would show.

Headless Chrome, driven over the DevTools protocol against an in-process
server. Skipped where there is no Chrome, or no ``websockets`` to speak the
protocol with (it is not a dependency of nr-workbench).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from nr_workbench.cli import main
from nr_workbench.project.config import load_config

from .experiment_fixtures import write_autoreduced

pytestmark = pytest.mark.integration

TOKEN = "browser-token"


def _chrome() -> str | None:
    candidates = [
        os.environ.get("NRW_TEST_CHROME"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        *(
            shutil.which(name)
            for name in ("google-chrome", "google-chrome-stable", "chromium", "chrome")
        ),
    ]
    return next((c for c in candidates if c and Path(c).exists()), None)


# --------------------------------------------------------------------------
# A browser, a server, and a page
# --------------------------------------------------------------------------


class Page:
    """One tab, driven over the DevTools protocol.

    Collects what would tell a person the page is broken: uncaught script
    errors, ``console.error``, and anything the browser itself logs as an
    error -- a script refused by the page's security policy among them.
    Failed requests are not collected: a 409 the page shows is the point.
    """

    def __init__(self, connection: Any) -> None:
        self._ws = connection
        self._next = 0
        self.errors: list[str] = []

    def send(self, method: str, **params: Any) -> dict[str, Any]:
        self._next += 1
        ident = self._next
        self._ws.send(json.dumps({"id": ident, "method": method, "params": params}))
        deadline = time.monotonic() + 30
        while True:
            message = json.loads(self._ws.recv(timeout=deadline - time.monotonic()))
            if message.get("id") == ident:
                if "error" in message:
                    raise RuntimeError(f"{method}: {message['error']}")
                return message.get("result", {})
            self._note(message)

    def _note(self, message: dict[str, Any]) -> None:
        method, params = message.get("method"), message.get("params", {})
        if method == "Runtime.exceptionThrown":
            detail = params["exceptionDetails"]
            self.errors.append(
                detail.get("exception", {}).get("description") or detail.get("text")
            )
        elif method == "Runtime.consoleAPICalled" and params["type"] == "error":
            self.errors.append(" ".join(str(a.get("value")) for a in params["args"]))
        elif method == "Log.entryAdded":
            entry = params["entry"]
            if entry["level"] == "error" and entry.get("source") != "network":
                self.errors.append(f"{entry.get('source')}: {entry.get('text')}")

    def js(self, expression: str) -> Any:
        result = self.send(
            "Runtime.evaluate",
            expression=expression,
            awaitPromise=True,
            returnByValue=True,
        )
        if "exceptionDetails" in result:
            detail = result["exceptionDetails"]
            raise RuntimeError(detail.get("exception", {}).get("description"))
        return result["result"].get("value")

    def wait_for(self, expression: str, what: str, timeout: float = 15) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if self.js(expression):
                    return
            except RuntimeError:
                pass  # the page is still loading
            time.sleep(0.05)
        raise AssertionError(f"timed out waiting for {what}; errors: {self.errors}")

    def goto(self, url: str) -> None:
        self.send("Page.navigate", url=url)
        self.wait_for(
            "document.readyState === 'complete' && window.NRWPage !== undefined",
            what=f"{url} to load",
        )

    def text(self, element_id: str) -> str:
        return self.js(f"document.getElementById({json.dumps(element_id)}).textContent")

    def type(self, element_id: str, value: str) -> None:
        """Set a field as typing would, so the page's input listeners run."""
        self.js(
            f"""{{
              const box = document.getElementById({json.dumps(element_id)});
              box.value = {json.dumps(value)};
              box.dispatchEvent(new Event("input"));
            }}"""
        )

    def click(self, element_id: str) -> None:
        self.js(f"document.getElementById({json.dumps(element_id)}).click()")


@pytest.fixture(scope="module")
def devtools(tmp_path_factory) -> Iterator[int]:
    """A headless Chrome for this module's tests; yields its DevTools port."""
    pytest.importorskip("websockets.sync.client")
    chrome = _chrome()
    if chrome is None:
        pytest.skip("no Chrome or Chromium here (set NRW_TEST_CHROME to use one)")
    profile = tmp_path_factory.mktemp("chrome-profile")
    command = [
        chrome,
        "--headless=new",
        "--remote-debugging-port=0",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-extensions",
        "--window-size=1300,1000",
    ]
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        command.append("--no-sandbox")  # Chrome refuses root otherwise (containers)
    process = subprocess.Popen(
        [*command, "about:blank"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        # Port 0 lets Chrome choose; it writes the one it chose here.
        active = profile / "DevToolsActivePort"
        deadline = time.monotonic() + 30
        while not (active.exists() and len(active.read_text().splitlines()) >= 2):
            if time.monotonic() > deadline or process.poll() is not None:
                pytest.skip("Chrome did not start its DevTools server")
            time.sleep(0.1)
        yield int(active.read_text().splitlines()[0])
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


@pytest.fixture
def page(devtools: int) -> Iterator[Page]:
    """A fresh tab; the test fails if the page raised an error in it."""
    from websockets.sync.client import connect

    request = urllib.request.Request(
        f"http://127.0.0.1:{devtools}/json/new?about:blank", method="PUT"
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        target = json.load(response)
    try:
        with connect(target["webSocketDebuggerUrl"], max_size=None) as connection:
            tab = Page(connection)
            for domain in ("Runtime", "Page", "Log"):
                tab.send(f"{domain}.enable")
            yield tab
            assert tab.errors == []
    finally:
        urllib.request.urlopen(
            f"http://127.0.0.1:{devtools}/json/close/{target['id']}", timeout=10
        ).close()


@pytest.fixture
def fresh(tmp_path: Path) -> Path:
    """What `nrw init` makes in an empty folder: no IPTS, nothing to watch yet."""
    root = tmp_path / "analysis"
    result = CliRunner().invoke(main, ["init", str(root)])
    assert result.exit_code == 0, result.output
    return root


@pytest.fixture
def facility(tmp_path: Path) -> Path:
    """A beamtime folder: two runs of IPTS-34347 and one from another experiment."""
    folder = tmp_path / "facility" / "IPTS-34347" / "new_reduction"
    settled = time.time() - 3600
    for run in (234277, 234280):
        write_autoreduced(folder, run, [1, 2, 3], ipts="IPTS-34347", mtime=settled)
    write_autoreduced(folder, 234290, [1, 2, 3], ipts="IPTS-99999", mtime=settled)
    return folder


@pytest.fixture
def serve(fresh: Path) -> Iterator[str]:
    """The project served on a free port, as `nrw serve` would; yields its URL."""
    from werkzeug.serving import make_server

    from nr_workbench.web.app import create_app

    app = create_app(fresh, token=TOKEN, autostart=False)
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        app.config["NRW_EXPERIMENT"].stop()


def open_settings(page: Page, base: str) -> None:
    """Open the one-time link, then Settings, and wait for it to be editable."""
    page.goto(f"{base}/auth/{TOKEN}")
    page.goto(f"{base}/settings")
    page.wait_for("!document.getElementById('s-save').disabled", what="editable")


def location_of(facility: Path) -> str:
    return str(facility).replace("IPTS-34347", "{ipts}")


# --------------------------------------------------------------------------
# The scenarios
# --------------------------------------------------------------------------


def test_the_link_lands_on_settings_and_the_default_path_follows_the_ipts(
    page: Page, serve: str
) -> None:
    page.goto(f"{serve}/auth/{TOKEN}")
    page.wait_for("location.pathname === '/settings'", what="landing on Settings")
    page.wait_for("!document.getElementById('s-save').disabled", what="editable")
    assert "needs the IPTS" in page.text("s-default-path")

    page.type("s-ipts", "34347")

    assert "/IPTS-34347/" in page.text("s-default-path")


def test_a_folder_check_shows_what_the_server_found_run_by_run(
    page: Page, serve: str, facility: Path
) -> None:
    open_settings(page, serve)
    page.type("s-ipts", "34347")
    page.type("s-location", location_of(facility))

    page.click("s-check")

    page.wait_for(
        "document.getElementById('s-check-result').textContent.includes('3 run(s)')",
        what="the check's answer",
    )
    found = page.text("s-check-result")
    assert str(facility) in found
    # The server's judgement, made against the IPTS typed on the page.
    assert "names IPTS-99999, but this project is IPTS-34347" in found


def test_a_refused_value_says_why_and_writes_nothing_and_a_good_one_saves(
    page: Page, serve: str, fresh: Path, facility: Path
) -> None:
    before = (fresh / "nrw.toml").read_bytes()
    open_settings(page, serve)
    page.type("s-poll", "1")

    page.click("s-save")

    page.wait_for(
        "document.getElementById('set-message').textContent.includes('from 5 to 600')",
        what="the refusal",
    )
    assert (fresh / "nrw.toml").read_bytes() == before

    page.type("s-poll", "")
    page.type("s-ipts", "34347")
    page.type("s-location", location_of(facility))
    page.click("s-save")

    page.wait_for(
        "document.getElementById('set-message').textContent.startsWith('Saved.')",
        what="the save",
    )
    config = load_config(fresh)
    assert config.ipts == "IPTS-34347"
    assert config.raw["experiment"]["source"]["location"] == location_of(facility)
    # What the server now reads, as the server says it.
    assert f"now watches {facility}" in page.text("s-status")


def test_a_hand_edit_while_the_page_is_open_is_kept_and_the_page_reloads(
    page: Page, serve: str, fresh: Path
) -> None:
    open_settings(page, serve)
    toml = fresh / "nrw.toml"
    toml.write_text(
        toml.read_text(encoding="utf-8").replace(
            "[conventions]", "# a note made by hand\n[conventions]"
        ),
        encoding="utf-8",
    )
    page.type("s-label", "april 2026")

    page.click("s-save")

    page.wait_for(
        "document.getElementById('set-message').textContent.includes('reloaded')",
        what="the conflict",
    )
    assert "changed since" in page.text("set-message")
    assert page.js("document.getElementById('s-label').value") == ""  # reloaded

    page.type("s-label", "april 2026")
    page.click("s-save")

    page.wait_for(
        "document.getElementById('set-message').textContent.startsWith('Saved.')",
        what="the save after reloading",
    )
    after = toml.read_text(encoding="utf-8")
    assert "# a note made by hand" in after
    assert 'label = "april 2026"' in after


def test_a_change_the_server_wants_confirmed_is_asked_once_and_sent_again(
    page: Page, serve: str, fresh: Path
) -> None:
    from nr_workbench.experiment.model import RunChange, RunKey
    from nr_workbench.experiment.workspace import Workspace
    from nr_workbench.project.settings import save

    save(fresh, {"ipts": "IPTS-34347"})
    Workspace(fresh).store.update(
        runs=[RunChange(RunKey(234277), 0, {"sample_id": "Sample1"})]
    )
    open_settings(page, serve)
    page.js("window.asked = []; window.confirm = m => (window.asked.push(m), false)")
    page.type("s-ipts", "34348")

    page.click("s-save")

    page.wait_for("window.asked.length === 1", what="the question")
    page.wait_for(
        "document.getElementById('s-status').textContent === 'Not saved.'",
        what="the declined save",
    )
    assert load_config(fresh).ipts == "IPTS-34347"

    page.js("window.asked = []; window.confirm = m => (window.asked.push(m), true)")
    page.click("s-save")

    page.wait_for(
        "document.getElementById('set-message').textContent.startsWith('Saved.')",
        what="the confirmed save",
    )
    assert page.js("window.asked.length") == 1
    assert load_config(fresh).ipts == "IPTS-34348"


def test_a_file_nrw_cannot_edit_shows_the_lines_to_add_and_keeps_the_form(
    page: Page, serve: str, fresh: Path
) -> None:
    toml = fresh / "nrw.toml"
    toml.write_text(
        toml.read_text(encoding="utf-8")
        + "\n[experiment.source]\nsettle_seconds = '''300'''\n",
        encoding="utf-8",
    )
    before = toml.read_bytes()
    open_settings(page, serve)
    page.type("s-settle", "60")

    page.click("s-save")

    page.wait_for(
        "!document.getElementById('s-lines').classList.contains('d-none')",
        what="the lines to add",
    )
    assert "settle_seconds = 60" in page.text("s-lines")
    # Not a conflict, so not reloaded: what the person typed is still there.
    assert page.js("document.getElementById('s-settle').value") == "60"
    assert toml.read_bytes() == before
