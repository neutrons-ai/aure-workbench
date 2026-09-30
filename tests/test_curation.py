"""Curating fits: star, finalize, discard, restore, and delete their files.

Fits here are recorded the way `nrw fit run` records them -- an index entry and
a result directory holding a manifest and its inputs -- without fitting
anything: what is under test is what a person may say about a fit, and what
that may and may not do to it. The rules are one module's; the terminal and the
page are each tested for reaching them, and for nothing else.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from nr_workbench.cli import main
from nr_workbench.project.layout import ProjectLayout
from nr_workbench.provenance import curation
from nr_workbench.provenance.index import FitIndex
from nr_workbench.web.app import create_app

TOKEN = "t" * 32
ORIGIN = "http://localhost"

A = "20260930-100000Z-aaaaaaaa"
B = "20260930-110000Z-bbbbbbbb"
C = "20260930-120000Z-cccccccc"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    result = CliRunner().invoke(main, ["init", str(root), "--sample", "S1"])
    assert result.exit_code == 0, result.output
    return root


def layout_of(project: Path) -> ProjectLayout:
    return ProjectLayout(root=project)


def index_of(project: Path) -> FitIndex:
    return FitIndex(project / ".nrw" / "index.jsonl")


def recorded(
    project: Path,
    fit_id: str,
    *,
    sample: str = "S1",
    status: str = "ok",
    inputs: tuple[str, ...] = (),
) -> Path:
    """A fit as `nrw fit run` leaves one: its entry, its directory, its inputs."""
    directory = project / "samples" / sample / "results" / fit_id
    directory.mkdir(parents=True)
    # What the pages read of a real one: its chi-squared, and how many free.
    (directory / "manifest.json").write_text(
        json.dumps({"info": {"chisq": 1.2, "n_free": 3, "n_points": 30}})
    )
    listed = []
    for relative in inputs:
        data = (project / relative).read_bytes()
        listed.append(
            {
                "role": "data",
                "path": relative,
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    (directory / "inputs.json").write_text(json.dumps({"inputs": listed}))
    index_of(project).append(
        {
            "fit_id": fit_id,
            "sample": sample,
            "model": "film",
            "status": status,
            "started_at": fit_id[:15],
            "chisq": 1.2,
            "n_free": 3,
        }
    )
    return directory


def events(project: Path, kind: str) -> list[dict]:
    return [e for e in index_of(project).entries() if e.get("event") == kind]


# --------------------------------------------------------------------------
# What a fit is now: the last word on it
# --------------------------------------------------------------------------


def test_each_fit_is_what_was_last_said_about_it(project: Path) -> None:
    for fit_id in (A, B):
        recorded(project, fit_id)
    layout = layout_of(project)

    curation.star(layout, A)
    curation.star(layout, A, starred=False)
    curation.star(layout, B)
    curation.discard(layout, A, reason="diverged")
    curation.restore(layout, A)

    said = curation.curation_of(index_of(project).entries())
    assert said[A] == curation.Curation()  # unstarred, restored
    assert said[B].starred is True
    # Saying it again adds nothing to the history.
    assert curation.star(layout, B) is False
    assert len(events(project, "star")) == 2
    assert all(e["who"] and e["at"] for e in events(project, "star"))


def test_a_label_is_held_by_the_last_fit_of_its_sample_promoted_to_it(
    project: Path,
) -> None:
    recorded(project, A)
    recorded(project, B, sample="S2")
    recorded(project, C)
    layout = layout_of(project)

    for fit_id in (A, B, C):
        curation.promote(layout, fit_id, reason="the best of the three")

    said = curation.curation_of(index_of(project).entries())
    # A was superseded by C in S1; B is S2's own, untouched by either.
    assert A not in said or said[A].labels == ()
    assert (said[B].labels, said[C].labels) == (("final",), ("final",))
    (superseded,) = events(project, "supersede")
    assert (superseded["fit_id"], superseded["superseded_by"]) == (A, C)


def test_finalizing_says_when_the_inputs_changed_and_goes_ahead_only_when_told(
    project: Path,
) -> None:
    data = project / "samples" / "S1" / "data" / "steady" / "REFL_1_combined.txt"
    data.parent.mkdir(parents=True, exist_ok=True)
    data.write_text("0.01 1.0 0.1\n")
    recorded(project, A, inputs=(data.relative_to(project).as_posix(),))
    data.write_text("0.01 2.0 0.1\n")  # the data moved on
    layout = layout_of(project)

    with pytest.raises(curation.InputsChanged, match="STALE") as refused:
        curation.promote(layout, A, reason="looks right")
    promotion = curation.promote(layout, A, reason="looks right", force=True)

    assert refused.value.needs == "force"
    assert promotion["forced"] is True


# --------------------------------------------------------------------------
# Discard, then delete
# --------------------------------------------------------------------------


def test_a_discarded_fit_keeps_every_file_and_its_reason(project: Path) -> None:
    directory = recorded(project, A)

    curation.discard(
        curation_layout := layout_of(project), A, reason="  wrong \n model "
    )

    said = curation.curation_of(index_of(project).entries())[A]
    assert said.discarded["reason"] == "wrong model"
    assert (directory / "manifest.json").is_file()
    assert curation.restore(curation_layout, A) is True


@pytest.mark.parametrize(
    ("reason", "message"), [("  ", "required"), ("x" * 501, "limit")]
)
def test_a_discard_needs_a_short_reason(
    project: Path, reason: str, message: str
) -> None:
    recorded(project, A)

    with pytest.raises(curation.CurationRefused, match=message):
        curation.discard(layout_of(project), A, reason=reason)

    assert events(project, "discard") == []


def test_the_answer_is_never_set_aside(project: Path) -> None:
    recorded(project, A)
    layout = layout_of(project)
    curation.promote(layout, A, reason="the answer")

    with pytest.raises(curation.CurationRefused, match="Finalize another fit first"):
        curation.discard(layout, A, reason="changed my mind")


def test_a_discarded_fit_is_neither_starred_nor_finalized_until_restored(
    project: Path,
) -> None:
    recorded(project, A)
    layout = layout_of(project)
    curation.discard(layout, A, reason="noisy")

    with pytest.raises(curation.CurationRefused, match="restore it"):
        curation.star(layout, A)
    with pytest.raises(curation.CurationRefused, match="restore it"):
        curation.promote(layout, A, reason="after all")


def test_files_go_only_after_a_discard_and_the_record_that_it_ran_stays(
    project: Path,
) -> None:
    directory = recorded(project, A)
    layout = layout_of(project)

    with pytest.raises(curation.CurationRefused, match="Discard .* first"):
        curation.delete_files(layout, A)
    curation.discard(layout, A, reason="diverged")
    removed = curation.delete_files(layout, A)

    assert removed.as_posix() == f"samples/S1/results/{A}"
    assert not directory.exists()
    assert index_of(project).find(A) is not None  # it ran; that stays true
    (deleted,) = events(project, "delete")
    assert deleted["reason"] == "diverged"
    with pytest.raises(curation.CurationRefused, match="nothing to restore"):
        curation.restore(layout, A)


def cited_by_a_report(project: Path) -> None:
    reports = project / "samples" / "S1" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "summary-technical.md").write_text(
        f"# S1\n\nThe film is 80 A thick ({A}).\n"
    )


def read_by_a_figure(project: Path) -> None:
    reports = project / "samples" / "S1" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "plot.py.figures.json").write_text(json.dumps({"fits": [A]}))


def read_by_another_fit(project: Path) -> None:
    # A fit started from A's fitted values, which it recorded as an input.
    values = project / "samples" / "S1" / "results" / A / "fit" / "best.par"
    values.parent.mkdir(parents=True)
    values.write_text("thickness 80\n")
    recorded(project, B, inputs=(values.relative_to(project).as_posix(),))


def exported(project: Path) -> None:
    (project / "samples" / "S1" / "results" / A / "isaac").mkdir()


@pytest.mark.parametrize(
    ("use", "said"),
    [
        (cited_by_a_report, "the report samples/S1/reports/summary-technical.md"),
        (read_by_a_figure, "the figures of samples/S1/reports/plot.py.figures.json"),
        (read_by_another_fit, f"fit {B}, which read its files"),
        (exported, "the ISAAC records made from it"),
    ],
)
def test_files_something_uses_are_never_deleted(project: Path, use, said: str) -> None:
    directory = recorded(project, A)
    use(project)
    layout = layout_of(project)
    curation.discard(layout, A, reason="superseded by a better model")

    with pytest.raises(curation.CurationRefused, match="Its files stay") as refused:
        curation.delete_files(layout, A)

    assert said in str(refused.value)
    assert (directory / "manifest.json").is_file()
    assert events(project, "delete") == []


@pytest.mark.skipif(os.name == "nt", reason="symbolic links need privileges")
def test_a_fit_directory_that_is_a_link_is_never_deleted_through(
    project: Path, tmp_path: Path
) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "manifest.json").write_text("{}\n")
    (elsewhere / "precious.txt").write_text("keep me")
    results = project / "samples" / "S1" / "results"
    results.mkdir(parents=True, exist_ok=True)
    (results / A).symlink_to(elsewhere)
    index_of(project).append({"fit_id": A, "sample": "S1", "status": "ok"})
    layout = layout_of(project)
    curation.discard(layout, A, reason="planted")

    with pytest.raises(curation.CurationRefused, match="not a fit directory"):
        curation.delete_files(layout, A)

    assert (elsewhere / "precious.txt").read_text() == "keep me"


# --------------------------------------------------------------------------
# From the terminal
# --------------------------------------------------------------------------


def nrw(project: Path, monkeypatch, *args: str):
    monkeypatch.chdir(project)
    return CliRunner().invoke(main, list(args))


def test_the_fit_commands_curate_and_nrw_ls_leaves_the_discarded_out(
    project: Path, monkeypatch
) -> None:
    recorded(project, A)
    recorded(project, B)

    starred = nrw(project, monkeypatch, "fit", "star", A)
    early = nrw(project, monkeypatch, "fit", "delete", B, "--yes")
    discarded = nrw(project, monkeypatch, "fit", "discard", B, "--reason", "diverged")
    listed = nrw(project, monkeypatch, "ls")
    everything = nrw(project, monkeypatch, "ls", "--all")
    deleted = nrw(project, monkeypatch, "fit", "delete", B, "--yes")

    assert starred.exit_code == 0 and "Starred" in starred.output
    assert early.exit_code != 0 and "first" in early.output
    assert discarded.exit_code == 0, discarded.output
    assert f"{A}" in listed.output and "★" in listed.output
    assert B not in listed.output and "nrw ls --all" in listed.output
    assert f"{B}" in everything.output and "(discarded)" in everything.output
    assert deleted.exit_code == 0, deleted.output
    assert not (project / "samples" / "S1" / "results" / B).exists()


def test_deleting_from_the_terminal_says_what_goes_and_asks(
    project: Path, monkeypatch
) -> None:
    directory = recorded(project, A)
    nrw(project, monkeypatch, "fit", "discard", A, "--reason", "diverged")

    monkeypatch.chdir(project)
    declined = CliRunner().invoke(main, ["fit", "delete", A], input="n\n")

    assert f"samples/S1/results/{A}/" in declined.output
    assert "Nothing deleted" in declined.output
    assert directory.is_dir()


def test_nrw_ls_marks_each_samples_final_fit(project: Path, monkeypatch) -> None:
    # The final fit of every sample -- not only the last one promoted anywhere.
    recorded(project, A)
    recorded(project, B, sample="S2")
    layout = layout_of(project)
    curation.promote(layout, A, reason="S1's answer")
    curation.promote(layout, B, reason="S2's answer")

    rows = [
        line
        for line in nrw(project, monkeypatch, "ls").output.splitlines()
        if line.strip().startswith("2026")
    ]

    assert all(line.rstrip().endswith("*") for line in rows), rows


def test_an_agent_curates_nothing(project: Path, monkeypatch) -> None:
    from nr_workbench.agent.guard import judge

    recorded(project, A)
    monkeypatch.setenv("NRW_AGENT", "1")

    refused = nrw(project, monkeypatch, "fit", "star", A)

    assert refused.exit_code != 0
    assert "a person's judgement" in refused.output
    assert events(project, "star") == []
    assert judge(f"nrw fit discard {A} --reason x").rule == "curate"
    assert judge(f"cd proj && nrw fit delete {A}").rule == "curate"
    # A fit run whose note happens to say "discard" is a fit run.
    assert judge("nrw fit run model.py --note discard").allowed


def test_nrw_check_says_nothing_of_a_fit_deleted_on_purpose(
    project: Path, monkeypatch
) -> None:
    from nr_workbench.commands.provenance_cmd import collect_problems

    recorded(project, A)
    layout = layout_of(project)
    curation.discard(layout, A, reason="diverged")
    curation.delete_files(layout, A)

    _, problems = collect_problems(layout, index_of(project))

    assert [p for p in problems if p["fit_id"] == A] == []


# --------------------------------------------------------------------------
# From the page
# --------------------------------------------------------------------------


@pytest.fixture
def app(project: Path):
    return create_app(project, token=TOKEN, autostart=False)


@pytest.fixture
def writer(app):
    client = app.test_client()
    assert client.get(f"/auth/{TOKEN}").status_code == 303
    return client


def post(client, app, fit_id: str, action: str, body: dict | None = None):
    return client.post(
        f"/api/experiment/fits/{fit_id}/{action}",
        json=body or {},
        headers={"X-NRW-Token": app.config["NRW_PAGE_TOKEN"], "Origin": ORIGIN},
    )


def test_curating_from_the_page_needs_the_link_and_a_writable_server(
    app, project: Path
) -> None:
    recorded(project, A)
    read_only = create_app(project, token=TOKEN, autostart=False, writable=False)
    viewer = read_only.test_client()
    viewer.get(f"/auth/{TOKEN}")

    stranger = post(app.test_client(), app, A, "star", {"starred": True})
    refused = post(viewer, read_only, A, "star", {"starred": True})

    assert (stranger.status_code, refused.status_code) == (403, 403)
    assert events(project, "star") == []


def test_the_page_names_a_fit_whole_never_by_a_prefix(app, writer, project) -> None:
    recorded(project, A)

    answer = post(writer, app, A[:10], "star", {"starred": True})

    assert answer.status_code == 404
    assert events(project, "star") == []


def test_the_page_stars_discards_restores_and_deletes(
    app, writer, project: Path
) -> None:
    directory = recorded(project, A)

    starred = post(writer, app, A, "star", {"starred": True}).json
    discarded = post(writer, app, A, "discard", {"reason": "diverged"}).json
    unconfirmed = post(writer, app, A, "delete", {})
    wrong = post(writer, app, A, "delete", {"confirm": B})
    deleted = post(writer, app, A, "delete", {"confirm": A})

    assert starred["curation"]["starred"] is True
    assert discarded["curation"]["discarded"]["reason"] == "diverged"
    assert discarded["delete_refusal"] is None  # nothing uses it: it may go
    assert (unconfirmed.status_code, wrong.status_code) == (400, 400)
    assert deleted.status_code == 200, deleted.json
    assert deleted.json["removed"] == f"samples/S1/results/{A}"
    assert not directory.exists()


def test_the_page_says_before_asking_why_a_fits_files_must_stay(
    app, writer, project: Path
) -> None:
    recorded(project, A)
    exported(project)

    discarded = post(writer, app, A, "discard", {"reason": "old"}).json
    refused = post(writer, app, A, "delete", {"confirm": A})

    assert "ISAAC records" in discarded["delete_refusal"]
    assert refused.status_code == 409


def test_finalizing_from_the_page_asks_again_when_the_inputs_changed(
    app, writer, project: Path
) -> None:
    data = project / "samples" / "S1" / "data" / "steady" / "REFL_1_combined.txt"
    data.parent.mkdir(parents=True, exist_ok=True)
    data.write_text("0.01 1.0 0.1\n")
    recorded(project, A, inputs=(data.relative_to(project).as_posix(),))
    recorded(project, B)
    post(writer, app, B, "finalize", {"reason": "first answer"})
    data.write_text("0.01 2.0 0.1\n")

    asked = post(writer, app, A, "finalize", {"reason": "better"})
    forced = post(writer, app, A, "finalize", {"reason": "better", "force": True})

    assert (asked.status_code, asked.json["needs"]) == (409, "force")
    assert forced.status_code == 200, forced.json
    assert forced.json["curation"]["labels"] == ["final"]
    assert forced.json["supersedes"] == B
    listed = {r["fit_id"]: r["labels"] for r in app.config["NRW_DATA"].fits()}
    assert (listed[A], listed[B]) == (["final"], [])  # B is final no longer


def test_the_fit_page_gives_its_token_only_to_a_writer(app, writer, project) -> None:
    recorded(project, A)
    token = app.config["NRW_PAGE_TOKEN"]

    seen = app.test_client().get(f"/f/{A}")
    written = writer.get(f"/f/{A}")

    for page in (seen, written):
        assert page.status_code == 200
        # Only the page's own scripts run where a token can be.
        assert "script-src 'self' 'nonce-" in page.headers["Content-Security-Policy"]
    assert token not in seen.get_data(as_text=True)
    assert token in written.get_data(as_text=True)


def test_the_fits_list_keeps_the_discarded_behind_a_toggle(
    app, writer, project: Path
) -> None:
    recorded(project, A)
    recorded(project, B)
    post(writer, app, B, "discard", {"reason": "diverged"})

    page = writer.get("/fits").get_data(as_text=True)

    assert "Show the discarded" in page
    assert "1 recorded, and 1 discarded" in page
    assert page.count("data-discarded") == 1
    sample = writer.get("/s/S1").get_data(as_text=True)
    assert A in sample and B not in sample
