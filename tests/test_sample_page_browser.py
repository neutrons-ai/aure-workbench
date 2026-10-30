"""The sample page in a real browser: the Measurements list drives the R(Q) plot.

Skipped where there is no Chrome, as the other browser tests are.
"""

# The browser fixtures are imported and requested by name, which ruff reads
# as unused imports and then as redefinitions.
# ruff: noqa: F811

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from .test_fits_page_browser import serving
from .test_settings_page_browser import (  # noqa: F401 - fixtures
    Page,
    Site,
    browser,
    page,
)
from .test_web import write_reduced

pytestmark = pytest.mark.integration


@pytest.fixture
def site(project: Path) -> Iterator[Site]:
    """The scaffolded project, with two steady runs in Sample1, served."""
    from nr_workbench.web.app import create_app

    steady = project / "samples" / "Sample1" / "data" / "steady"
    steady.mkdir(parents=True, exist_ok=True)
    for segment in (1, 2):
        write_reduced(steady / f"REFL_100001_{segment}_10000{segment}_partial.txt")
    write_reduced(steady / "REFL_100002_combined_data_auto.txt")
    with serving(create_app(project, autostart=False)) as served:
        yield served


def shown(page: Page) -> list[str]:
    return page.js(
        "document.getElementById('plot-refl').data"
        ".filter(t => t.visible !== 'legendonly').map(t => t.name)"
    )


def test_unticking_a_run_takes_it_off_the_plot_and_ticking_puts_it_back(
    page: Page, site: Site
) -> None:
    # The sample page loads nrw.js alone, not the nrwpage.js that `goto` waits
    # for, so the wait here is for the plot itself.
    page.send("Page.navigate", url=f"{site.url}/s/Sample1")
    page.wait_for(
        "document.readyState === 'complete' && "
        "(document.getElementById('plot-refl') || {}).data !== undefined",
        what="the plot",
    )
    assert shown(page) == ["100001#1", "100001#2", "100002 combined"]

    page.js("document.querySelector('[data-run=\"100001\"]').click()")
    page.wait_for(
        "document.getElementById('plot-refl').data.some(t => t.visible === 'legendonly')",
        what="the run to be hidden",
    )
    assert shown(page) == ["100002 combined"]
    # The remaining run keeps its colour: every trace is still built.
    assert page.js("document.getElementById('plot-refl').data.length") == 3

    page.js("document.querySelector('[data-run=\"100001\"]').click()")
    page.wait_for(
        "!document.getElementById('plot-refl').data.some(t => t.visible === 'legendonly')",
        what="the run to be back",
    )
    assert shown(page) == ["100001#1", "100001#2", "100002 combined"]
