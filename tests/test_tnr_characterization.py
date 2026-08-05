"""Characterization: prove the refactor did not change the numbers.

``tnr_chi2.py`` was 1992 lines in one module with a single Click command
carrying ~60 options. Splitting it was worth doing, but only if the science
came through untouched -- a subtle change in a coadd weight or a variance sign
would be invisible in review and would quietly corrupt every future
assessment.

So the ASCII tables in ``tests/data/tnr_golden/`` were produced by the
*original* tool on the synthetic run in ``tests/tnr_fixture.py``, and these
tests compare this package's output against them byte-for-byte. Regenerate
them only when a change to the numerics is deliberate, and say so in the
commit.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

pytest.importorskip("numpy")

from tests import tnr_fixture  # noqa: E402

GOLDEN_DIR = Path(__file__).parent / "data" / "tnr_golden"

#: Tables the original tool emits. The PNGs are not compared: they vary with
#: the matplotlib version and carry no numbers the tables do not.
GOLDEN_TABLES = (
    "amplitude",
    "chi2",
    "kl",
    "pca_scores",
    "qbands",
    "variogram",
)


@pytest.fixture(scope="module")
def assessed(tmp_path_factory: pytest.TempPathFactory):
    """Build the fixture run and assess it with this package."""
    from nr_workbench.tnr.report import assess
    from nr_workbench.tnr.run import load

    root = tmp_path_factory.mktemp("tnr")
    data_dir = root / "data"
    tnr_fixture.build(data_dir)

    # The golden headers record the source directory as the relative "data",
    # which is how the original tool was invoked. Match that so the header
    # lines compare equal.
    import os

    previous = Path.cwd()
    os.chdir(root)
    try:
        run = load(Path("data"))
        result = assess(run, root / "out", label="ref")
    finally:
        os.chdir(previous)
    return result, root / "out"


@pytest.mark.parametrize("table", GOLDEN_TABLES)
def test_ascii_output_matches_the_original_tool(assessed, table: str) -> None:
    """Every number and every byte of formatting must be unchanged."""
    _, out_dir = assessed
    produced = (out_dir / f"ref_{table}.txt").read_text(encoding="utf-8")
    expected = (GOLDEN_DIR / f"ref_{table}.txt").read_text(encoding="utf-8")

    assert produced == expected, (
        f"ref_{table}.txt differs from the output of the original tnr_chi2.py. "
        "The refactor was supposed to preserve the numerics exactly. If the "
        "change was deliberate, regenerate tests/data/tnr_golden/ and say so."
    )


def test_output_filenames_are_preserved(assessed) -> None:
    """Existing notes and habits reference these names."""
    _, out_dir = assessed
    names = {p.name for p in out_dir.iterdir()}

    for table in GOLDEN_TABLES:
        assert f"ref_{table}.txt" in names
    for plot in (
        "amplitude",
        "chi2",
        "variogram",
        "qbands",
        "pca",
        "kl",
        "residual_heatmap",
        "rq4_heatmap",
        "chi2_split",
        "delta2",
    ):
        assert f"ref_{plot}.png" in names, f"missing ref_{plot}.png"


def test_assessment_json_is_the_only_new_artifact(assessed) -> None:
    """The refactor adds exactly one output: the machine-readable summary."""
    _, out_dir = assessed
    original = {f"ref_{t}.txt" for t in GOLDEN_TABLES} | {
        f"ref_{p}.png"
        for p in (
            "amplitude",
            "chi2",
            "chi2_split",
            "delta2",
            "variogram",
            "qbands",
            "pca",
            "kl",
            "residual_heatmap",
            "rq4_heatmap",
        )
    }
    produced = {p.name for p in out_dir.iterdir()}

    assert produced - original == {"ref_assessment.json"}
