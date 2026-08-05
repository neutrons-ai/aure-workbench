"""The web layer: ProjectData without Flask, then Flask over it.

The split matters enough to test directly. :class:`ProjectData` must stay
importable and usable with no web framework present, because that is what keeps
it testable and what stops the route handlers from accumulating analysis --
the failure mode this design exists to avoid.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

from nr_workbench.web.project import ProjectData, _numbered
from nr_workbench.web.prose import render, strip_comments
from nr_workbench.web.readers import (
    DataFormatError,
    read_amplitude_txt,
    read_profile_dat,
    read_reduced,
)

from . import tnr_fixture


def write_reduced(path: Path, *, n: int = 40, scale: float = 1.0) -> None:
    """Write a four-column reduced file with a plausible decay."""
    q = np.linspace(0.01, 0.2, n)
    r = scale * 1e-3 * (0.01 / q) ** 4
    dr = 0.05 * r
    dq = 0.02 * q
    path.write_text(
        "\n".join(
            f"{a:.6e} {b:.6e} {c:.6e} {d:.6e}"
            for a, b, c, d in zip(q, r, dr, dq, strict=True)
        ),
        encoding="utf-8",
    )


@pytest.fixture
def populated(project: Path) -> Path:
    """The scaffolded project, plus two steady runs and a tNR series."""
    steady = project / "samples" / "Sample1" / "data" / "steady"
    steady.mkdir(parents=True, exist_ok=True)
    for segment in (1, 2, 3):
        write_reduced(
            steady / f"REFL_100001_{segment}_10000{segment}_partial.txt",
            scale=1.0 + segment / 10,
        )
    write_reduced(steady / "REFL_100002_combined_data_auto.txt")

    series = project / "samples" / "Sample1" / "data" / "tnr" / "100003"
    series.mkdir(parents=True, exist_ok=True)
    tnr_fixture.build(series)
    return project


# --------------------------------------------------------------------------
# ProjectData works with no web framework
# --------------------------------------------------------------------------


def test_project_data_imports_without_flask() -> None:
    """The data layer must not drag a web framework in.

    Guards the boundary the whole module split exists to hold: if a route
    helper ever leaks into project.py, this fails immediately rather than in
    six months when someone tries to reuse ProjectData from a script.
    """
    import subprocess

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import nr_workbench.web.project as p; "
            "assert 'flask' not in sys.modules, sorted("
            "m for m in sys.modules if 'flask' in m); print('clean')",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "clean" in result.stdout


def test_overview_of_an_empty_project_lists_the_sample(project: Path) -> None:
    """A scaffolded project with no data still renders."""
    overview = ProjectData(project).overview()

    assert overview["name"] == "test-project"
    assert overview["beamtime"] == "jen-june2026"
    assert overview["n_fits"] == 0
    assert [card["id"] for card in overview["samples"]] == ["Sample1"]
    assert overview["samples"][0]["n_steady"] == 0


def test_overview_counts_data_found_on_disk(populated: Path) -> None:
    """Sample cards report what the scan found."""
    card = ProjectData(populated).overview()["samples"][0]

    assert card["n_steady"] == 2
    assert card["n_series"] == 1


def test_sample_reports_prose_and_register(populated: Path) -> None:
    """The sample view carries both the human and machine records."""
    detail = ProjectData(populated).sample("Sample1")

    assert detail["id"] == "Sample1"
    assert "# Sample1" in detail["markdown"]
    assert [entry["run"] for entry in detail["steady"]] == [100001, 100002]
    assert detail["series"][0]["n_slices"] > 1
    assert detail["series"][0]["plottable"] is True


def test_sample_reports_data_the_prose_does_not_mention(populated: Path) -> None:
    """The gap between sample.md and disk is surfaced, not hidden."""
    detail = ProjectData(populated).sample("Sample1")

    assert 100001 in detail["present_but_undocumented"]


def test_missing_sample_raises_rather_than_returning_empty(project: Path) -> None:
    """Asking for a sample that does not exist is an error, not an empty page."""
    with pytest.raises(FileNotFoundError, match="Nope"):
        ProjectData(project).sample("Nope")


# --------------------------------------------------------------------------
# Curves
# --------------------------------------------------------------------------


def test_steady_curves_keeps_segments_separate(populated: Path) -> None:
    """Angle segments are not stitched: the overlap is diagnostic."""
    payload = ProjectData(populated).steady_curves("Sample1")

    labels = [curve["label"] for curve in payload["curves"]]
    assert labels == ["100001#1", "100001#2", "100001#3", "100002 combined"]
    assert not payload["problems"]


def test_steady_curves_reports_an_unreadable_file_and_keeps_going(
    populated: Path,
) -> None:
    """One corrupt file must not blank the whole panel."""
    broken = (
        populated
        / "samples"
        / "Sample1"
        / "data"
        / "steady"
        / "REFL_100001_2_100002_partial.txt"
    )
    broken.write_text("not numbers at all\n", encoding="utf-8")

    payload = ProjectData(populated).steady_curves("Sample1")

    assert len(payload["curves"]) == 3
    assert any("100001#2" in p["scope"] for p in payload["problems"])


def test_reduced_reader_rejects_a_file_with_too_few_columns(tmp_path: Path) -> None:
    """A two-column file is not a reduced file, and saying so beats guessing."""
    path = tmp_path / "bad.txt"
    path.write_text("0.01 1.0\n0.02 0.5\n", encoding="utf-8")

    with pytest.raises(DataFormatError, match="at least Q, R and dR"):
        read_reduced(path, label="x")


# --------------------------------------------------------------------------
# The tNR series and its assessment
# --------------------------------------------------------------------------


def test_series_data_returns_a_residual_map_against_the_shared_reference(
    populated: Path,
) -> None:
    """The heatmap uses the same reference block the assessment resolves."""
    payload = ProjectData(populated).series_data("Sample1", "100003")

    assert len(payload["residual"]) == payload["n_intervals"]
    assert len(payload["residual"][0]) == len(payload["q"])
    assert payload["reference"]["indices"]
    assert (
        "reference" in payload["reference"]["description"]
        or payload["reference"]["description"]
    )


def test_series_residual_is_near_zero_across_the_reference_block(
    populated: Path,
) -> None:
    """A reference interval differs from its own coadd only by noise.

    Anchors the sign and normalisation of the map: if the residual were
    inverted or scaled, the reference rows would not sit at zero.
    """
    payload = ProjectData(populated).series_data("Sample1", "100003")

    for index in payload["reference"]["indices"]:
        row = [v for v in payload["residual"][index] if v is not None]
        assert abs(float(np.median(row))) < 0.05


def test_series_warnings_are_returned_not_printed(
    populated: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Loader diagnostics belong in the response, where a reader will see them."""
    payload = ProjectData(populated).series_data("Sample1", "100003")

    captured = capsys.readouterr()
    assert "Skipped" not in captured.err
    assert isinstance(payload["problems"], list)


def test_series_directory_cannot_escape_the_sample(populated: Path) -> None:
    """A crafted series name must not read outside the tNR directory."""
    with pytest.raises(FileNotFoundError):
        ProjectData(populated).series_data("Sample1", "../../../etc")


@pytest.fixture
def assessed(populated: Path) -> Path:
    """The populated project with a real tNR assessment written."""
    from nr_workbench.tnr.notify import collect
    from nr_workbench.tnr.report import assess
    from nr_workbench.tnr.run import load

    out = populated / "samples" / "Sample1" / "assessments" / "r900001"
    series = populated / "samples" / "Sample1" / "data" / "tnr" / "100003"
    with collect():
        assess(load(series), out, label="r900001", plots=False)
    return populated


def test_assessment_amplitude_tracks_the_injected_trajectory(
    assessed: Path,
) -> None:
    """The a(t) the UI plots is the sigmoid that was put into the data.

    Compared by correlation, not value: ``a`` is normalised so the late block
    projects to 1, and the fixture's late block is not the asymptote, so the
    scale differs by a constant. Same reasoning as the metric's own test --
    what matters here is that no step between the file and the plot reorders,
    drops, or rescales the series.
    """
    payload = ProjectData(assessed).assessment("Sample1", "r900001")
    recovered = np.array(payload["amplitude_series"]["a"], dtype=float)
    expected = tnr_fixture.expected_amplitudes()
    finite = np.isfinite(recovered)

    assert recovered.shape == expected.shape
    correlation = float(np.corrcoef(recovered[finite], expected[finite])[0, 1])
    assert correlation > 0.97, (
        f"a(t) does not track the injection (r={correlation:.3f})"
    )


def test_assessment_series_matches_the_file_it_came_from(assessed: Path) -> None:
    """The parsed series must equal the ASCII the scientist reads.

    The plot and the file are the same numbers or the UI is lying about the
    data -- so this compares against the file directly rather than against
    another copy of the parser.
    """
    directory = assessed / "samples" / "Sample1" / "assessments" / "r900001"
    rows = [
        line.split("\t")
        for line in (directory / "r900001_amplitude.txt")
        .read_text(encoding="utf-8")
        .splitlines()
        if line and not line.startswith("#")
    ]

    payload = ProjectData(assessed).assessment("Sample1", "r900001")
    series = payload["amplitude_series"]

    assert len(series["times"]) == len(rows)
    assert series["times"] == [float(row[0]) for row in rows]
    assert series["a"] == [float(row[1]) for row in rows]
    assert series["labels"] == [row[7] for row in rows]


def test_assessment_exposes_the_verdict_and_figures(assessed: Path) -> None:
    """The verdict is the headline; the UI must not have to recompute it."""
    payload = ProjectData(assessed).assessment("Sample1", "r900001")

    assert payload["assessment"]["verdict"]
    assert payload["label"] == "r900001"


def test_assessment_label_is_discovered_despite_the_filename_prefix(
    assessed: Path,
) -> None:
    """Outputs are named ``<label>_assessment.json``, not ``assessment.json``."""
    assert ProjectData(assessed).assessment_labels("Sample1") == ["r900001"]


def test_amplitude_reader_rejects_a_truncated_row(tmp_path: Path) -> None:
    """A short data line means the format changed; fail loudly."""
    path = tmp_path / "amplitude.txt"
    path.write_text("# reference: leading\n0.0\t1.0\t0.1\n", encoding="utf-8")

    with pytest.raises(DataFormatError, match="expected 8"):
        read_amplitude_txt(path)


def test_amplitude_reader_keeps_the_provenance_header(tmp_path: Path) -> None:
    """The header says what a=0 and a=1 mean; the plot is wrong without it."""
    path = tmp_path / "amplitude.txt"
    path.write_text(
        "# reference: leading 3 intervals\n"
        "# late_block: trailing block\n"
        "# time_s\ta\tsigma_a\tsignificance\tchi2_res\tn_q\tinterval_type\tlabel\n"
        "0.0\t0.1\t0.01\t10.0\t1.02\t200\thold\tfirst\n",
        encoding="utf-8",
    )

    series = read_amplitude_txt(path)

    assert series.header["reference"] == "leading 3 intervals"
    assert series.labels == ["first"]
    assert series.times == [0.0]


# --------------------------------------------------------------------------
# Fits
# --------------------------------------------------------------------------


def test_numbered_sorts_by_integer_not_lexically(tmp_path: Path) -> None:
    """Model 10 must not sort between 1 and 2.

    Lexical ordering here would pair curves with the wrong labels -- a plot
    that looks entirely reasonable and is simply wrong.
    """
    for index in (1, 2, 10, 11, 21):
        (tmp_path / f"run-{index}-refl.dat").write_text("", encoding="utf-8")

    assert [i for i, _ in _numbered(tmp_path, "-refl.dat")] == [1, 2, 10, 11, 21]


def test_profile_thinning_preserves_the_curve(tmp_path: Path) -> None:
    """Display thinning must not move an interface.

    refl1d writes its profile on the grid its integration needs, which is far
    finer than any feature in the model. Thinning it is worth doing, but only
    if the shape survives -- so compare the thinned curve, interpolated back,
    against every original point.
    """
    z = np.arange(-50.0, 650.0, 0.1)
    rho = 3.0 + 3.0 * np.tanh((z - 100.0) / 8.0) - 2.0 * np.tanh((z - 400.0) / 5.0)
    path = tmp_path / "m-1-profile.dat"
    path.write_text(
        "# z rho irho\n"
        + "\n".join(f"{a:.6f} {b:.6f} 0.0" for a, b in zip(z, rho, strict=True)),
        encoding="utf-8",
    )

    full = read_profile_dat(path, label="x", max_spacing=0)
    thin = read_profile_dat(path, label="x")

    assert thin.stride > 1
    assert len(thin.z) < len(full.z) / 2
    worst = np.abs(np.interp(full.z, thin.z, thin.rho) - np.array(full.rho)).max()
    assert worst < 0.01 * (max(full.rho) - min(full.rho))


def test_fit_curves_are_labelled_from_the_recorded_model_names() -> None:
    """Export position maps to a name via the manifest, not via build order."""
    from nr_workbench.web.project import _model_names

    names = _model_names(
        [{"index": 1, "name": "ocv1#0"}, {"index": 2}, {"index": 3, "name": "tnr#0"}]
    )

    assert names == {1: "ocv1#0", 3: "tnr#0"}


def test_describe_models_records_names_and_positions() -> None:
    """The runner reads the ordering off the object it actually fitted."""
    from nr_workbench.fitting.runner import describe_models

    class Model:
        def __init__(self, name: str | None) -> None:
            self.name = name

        def numpoints(self) -> int:
            return 42

    class Problem:
        models = [Model("ocv1#0"), Model(None), Model("tnr#3")]

    assert describe_models(Problem()) == [
        {"index": 1, "name": "ocv1#0", "n_points": 42},
        {"index": 2, "n_points": 42},
        {"index": 3, "name": "tnr#3", "n_points": 42},
    ]


def test_describe_models_tolerates_a_problem_without_models() -> None:
    """A bumps object that exposes no models must not break the record."""
    from nr_workbench.fitting.runner import describe_models

    assert describe_models(object()) == []


# --------------------------------------------------------------------------
# Prose rendering
# --------------------------------------------------------------------------


def test_markdown_drops_commented_examples(populated: Path) -> None:
    """The template's worked example must not appear as real measurements."""
    source = (populated / "samples" / "Sample1" / "sample.md").read_text(
        encoding="utf-8"
    )

    rendered = render(source)

    assert "<h1>Sample1</h1>" in rendered
    assert "230594" not in rendered
    assert "One line per run" not in rendered


def test_markdown_renders_the_measurement_table() -> None:
    """The register in sample.md is a pipe table and must render as one."""
    rendered = render("| Run | Type |\n|-----|------|\n| 1 | full Q |\n")

    assert "<table>" in rendered
    assert "<td>full Q</td>" in rendered


def test_markdown_does_not_pass_through_raw_html() -> None:
    """Prose in a side panel has no reason to be able to run script."""
    rendered = render("Hello <script>alert(1)</script> there")

    assert "<script>" not in rendered


def test_strip_comments_handles_multiline_blocks() -> None:
    """The scaffold's comments span many lines."""
    assert strip_comments("a\n<!-- x\ny\nz -->\nb").strip() == "a\n\nb"


# --------------------------------------------------------------------------
# Flask
# --------------------------------------------------------------------------


@pytest.fixture
def client(populated: Path):
    """A test client over the populated project."""
    from nr_workbench.web.app import create_app

    return create_app(populated).test_client()


def test_create_app_refuses_a_directory_that_is_not_a_project(
    tmp_path: Path,
) -> None:
    """Pointing the server at the wrong directory must say so."""
    from nr_workbench.web.app import create_app

    with pytest.raises(FileNotFoundError, match="nrw init"):
        create_app(tmp_path)


@pytest.mark.parametrize(
    "url",
    [
        "/",
        "/fits",
        "/s/Sample1",
        "/api/overview",
        "/api/samples/Sample1",
        "/api/samples/Sample1/curves",
        "/api/samples/Sample1/series/100003",
        "/api/samples/Sample1/series/100003/curves",
        "/api/samples/Sample1/assessments",
        "/api/fits",
    ],
)
def test_route_renders(client, url: str) -> None:
    """Every route serves without error on a real project."""
    assert client.get(url).status_code == 200


@pytest.mark.parametrize(
    "url",
    ["/s/Nope", "/api/samples/Nope", "/api/fits/nosuchfit", "/f/nosuchfit"],
)
def test_missing_things_are_404_not_500(client, url: str) -> None:
    """A wrong URL is the user's mistake; it should read like one."""
    assert client.get(url).status_code == 404


def test_api_errors_are_json_not_html(client) -> None:
    """The API's consumer is a script, so its errors must parse."""
    response = client.get("/api/samples/Nope")

    assert response.status_code == 404
    assert "error" in response.get_json()


def test_api_mirrors_project_data(populated: Path, client) -> None:
    """The API adds nothing of its own, so the two must agree exactly."""
    direct = ProjectData(populated).overview()
    served = client.get("/api/overview").get_json()

    assert served == json.loads(json.dumps(direct, default=str))


def test_sample_page_embeds_parseable_payloads(client) -> None:
    """A malformed embedded payload is a blank page, so parse it here."""
    import re

    html = client.get("/s/Sample1").get_data(as_text=True)

    for name in ("curves", "series", "sampleId"):
        match = re.search(rf"const {name} = (.*?);\n", html, re.S)
        assert match, f"{name} is not embedded in the page"
        json.loads(match.group(1))


def test_embedded_json_cannot_break_out_of_the_script_tag() -> None:
    """A label containing </script> must not end the block early."""
    from nr_workbench.web.app import _compact_json

    encoded = _compact_json({"label": "</script><img src=x onerror=alert(1)>"})

    assert "</script>" not in encoded
    assert json.loads(encoded)["label"].startswith("</script>")


def test_figure_route_rejects_traversal(client) -> None:
    """A crafted filename must not read outside the assessment directory."""
    response = client.get("/figures/Sample1/r900001/..%2f..%2f..%2fnrw.toml")

    assert response.status_code in (301, 400, 404)


def test_help_does_not_import_flask() -> None:
    """`nrw --help` must not pay for the web stack.

    The serve command lazy-imports Flask for the same reason every other
    command lazy-imports its dependencies: the CLI is used constantly and a
    slow --help is a tax on every single invocation.
    """
    import subprocess

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from click.testing import CliRunner; "
            "from nr_workbench.cli import main; "
            "CliRunner().invoke(main, ['--help']); "
            "bad = [m for m in ('flask', 'aure') if m in sys.modules]; "
            "assert not bad, bad; print('clean')",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "clean" in result.stdout


def test_serve_is_listed_in_help() -> None:
    """The command must be discoverable."""
    from click.testing import CliRunner

    from nr_workbench.cli import main

    result = CliRunner().invoke(main, ["--help"])

    assert "serve" in result.output


# --------------------------------------------------------------------------
# The fit view, against a real fit
# --------------------------------------------------------------------------


@pytest.mark.integration
def test_fit_view_labels_curves_from_a_generated_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A generated script's spec slots survive all the way to the plot legend.

    bumps names its exports by position only, so this is the end-to-end check
    that the name recorded at fit time is what the UI shows -- the alternative
    is a legend reconstructed from build order, which is wrong silently.
    """
    pytest.importorskip("refl1d")
    from click.testing import CliRunner

    from nr_workbench.cli import main

    runner = CliRunner()
    root = tmp_path / "proj"
    assert runner.invoke(main, ["init", str(root), "--sample", "S1"]).exit_code == 0

    steady = root / "samples" / "S1" / "data" / "steady"
    steady.mkdir(parents=True, exist_ok=True)
    for segment in (1, 2):
        write_reduced(steady / f"REFL_100001_{segment}_10000{segment}_partial.txt")

    spec = root / "samples" / "S1" / "models" / "m.yaml"
    spec.write_text(
        "schema: nrw-model/1\n"
        "name: m\n"
        "sample: S1\n"
        "materials:\n"
        "  D2O: {rho: 6.19}\n"
        "  Film: {rho: 4.0}\n"
        "  Si: {rho: 2.07}\n"
        "stack:\n"
        "  - {name: D2O, material: D2O, thickness: 0, roughness: 5}\n"
        "  - {name: Film, material: Film, thickness: 120, roughness: 5}\n"
        "  - {name: Si, material: Si}\n"
        "probe: {resolution: angular_only}\n"
        "states:\n"
        "  - name: ocv1\n"
        "    run: 100001\n"
        "    segments: auto\n"
        "    thetas: [0.45, 1.2]\n"
        "    data_dir: samples/S1/data/steady\n"
        "parameters:\n"
        "  - {path: Film.thickness, range: [50, 200], per: model}\n",
        encoding="utf-8",
    )

    monkeypatch.chdir(root)
    generated = runner.invoke(main, ["model", "generate", "samples/S1/models/m.yaml"])
    assert generated.exit_code == 0, generated.output

    fitted = runner.invoke(
        main,
        ["fit", "run", "samples/S1/models/m.py", "--method", "amoeba", "--steps", "6"],
    )
    assert fitted.exit_code == 0, fitted.output

    data = ProjectData(root)
    fit_id = data.fits("S1")[0]["fit_id"]
    detail = data.fit(fit_id)

    assert [curve["label"] for curve in detail["curves"]] == ["ocv1#0", "ocv1#1"]
    assert all(curve["named"] for curve in detail["curves"])
    assert not detail["problems"]
    assert detail["curves"][0]["theory"], "the model curve must be plotted too"
    assert detail["profiles"], "an SLD profile must be available"

    from nr_workbench.web.app import create_app

    client = create_app(root).test_client()
    page = client.get(f"/f/{fit_id}")
    assert page.status_code == 200
    assert b"ocv1#0" in page.data
    assert b"Provenance" in page.data


@pytest.mark.integration
def test_fit_view_says_so_when_a_script_named_no_models(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hand-written script may not name its experiments; admit it.

    Labelling those curves "ocv1#0" would be an invention. Position is all the
    fit recorded, so position is all the UI claims.
    """
    pytest.importorskip("refl1d")
    from click.testing import CliRunner

    from nr_workbench.cli import main

    runner = CliRunner()
    root = tmp_path / "proj"
    assert runner.invoke(main, ["init", str(root), "--sample", "S1"]).exit_code == 0

    steady = root / "samples" / "S1" / "data" / "steady"
    steady.mkdir(parents=True, exist_ok=True)
    write_reduced(steady / "REFL_100001_combined_data_auto.txt")

    models = root / "samples" / "S1" / "models"
    models.mkdir(parents=True, exist_ok=True)
    (models / "hand.py").write_text(
        "import os\n"
        "import numpy as np\n"
        "from refl1d.names import SLD, Experiment, FitProblem, QProbe\n"
        "DATA = os.path.join(os.path.dirname(__file__), '..', 'data', 'steady',\n"
        "                    'REFL_100001_combined_data_auto.txt')\n"
        "q, r, dr, dq = np.loadtxt(DATA).T\n"
        "probe = QProbe(q, dq / 2.355, data=(r, dr))\n"
        "sample = SLD('D2O', rho=6.19)(0, 5) | SLD('Film', rho=4.0)(120, 5) "
        "| SLD('Si', rho=2.07)\n"
        "sample['Film'].thickness.range(50, 200)\n"
        "problem = FitProblem(Experiment(sample=sample, probe=probe))\n",
        encoding="utf-8",
    )

    monkeypatch.chdir(root)
    fitted = runner.invoke(
        main,
        [
            "fit",
            "run",
            "samples/S1/models/hand.py",
            "--method",
            "amoeba",
            "--steps",
            "6",
        ],
    )
    assert fitted.exit_code == 0, fitted.output

    data = ProjectData(root)
    detail = data.fit(data.fits("S1")[0]["fit_id"])

    assert detail["curves"][0]["label"] == "model 1"
    assert detail["curves"][0]["named"] is False
    assert any("no model names" in p["message"] for p in detail["problems"])
