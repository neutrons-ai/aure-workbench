"""The language model the page's jobs use, set on the Settings page.

Three layers, each with its own promise:

- ``env.where_set`` says what an nrw command started now would read, and where
  each value comes from, without loading anything into the process asking.
- ``project.envfile`` changes only the lines it is asked to in the project's
  ``.env``, the person's file, and never writes through a link or over a hand
  edit made meanwhile.
- The Settings API: reading is open and shows no key; choosing and checking
  need the link. What a choice changes is proved by a real child ``nrw`` --
  what a job reads -- not by the server's own view of it.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from nr_workbench import env as env_module
from nr_workbench.cli import main
from nr_workbench.project.envfile import (
    ABSENT,
    HEADER,
    EnvFileConflict,
    EnvFileError,
    read_env_file,
    set_values,
)
from nr_workbench.web import jobs as jobs_module
from nr_workbench.web import settings as settings_module
from nr_workbench.web.app import create_app

TOKEN = "t" * 32
ORIGIN = "http://localhost"
LLM = "/api/experiment/settings/llm"

#: What ~/.aure holds for LLM_API_KEY: it must never reach the page, redacted
#: or not. Plainly not a real one.
PLANTED = "not-a-real-one-zq7x"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    result = CliRunner().invoke(main, ["init", str(root)])
    assert result.exit_code == 0, result.output
    return root


@pytest.fixture
def home(tmp_path: Path, monkeypatch) -> Path:
    """A home whose ~/.aure is set up for another provider, as a machine that
    has run AuRE before often is. The child processes read it through HOME,
    this one through the paths conftest points elsewhere."""
    home = tmp_path / "home"
    home.mkdir()
    (home / ".aure").write_text(
        f"LLM_PROVIDER=openai\nLLM_MODEL=gpt-4o\nLLM_API_KEY={PLANTED}\nLLM_TIMEOUT=99\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(env_module, "USER_ENV_PATH", home / ".nrw")
    monkeypatch.setattr(env_module, "AURE_ENV_PATH", home / ".aure")
    return home


@pytest.fixture
def app(project: Path, home: Path):
    return create_app(project, token=TOKEN, autostart=False)


@pytest.fixture
def writer(app):
    client = app.test_client()
    assert client.get(f"/auth/{TOKEN}").status_code == 303
    return client


def headers(app) -> dict[str, str]:
    return {"X-NRW-Token": app.config["NRW_PAGE_TOKEN"], "Origin": ORIGIN}


def choose(client, app, provider, model: str = "", revision: str | None = None):
    if revision is None:
        revision = client.get(LLM).json["revision"]
    return client.put(
        LLM,
        json={"revision": revision, "provider": provider, "model": model},
        headers=headers(app),
    )


def as_a_job_sees_it(project: Path) -> str:
    """What a job's nrw reads: `nrw doctor` in a child, run as a job step is."""
    import subprocess

    completed = subprocess.run(
        jobs_module.nrw_command("doctor", "--json"),
        cwd=project,
        env=jobs_module.child_environment(),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    checks = json.loads(completed.stdout)
    return next(c["detail"] for c in checks if c["name"] == "llm settings")


# --------------------------------------------------------------------------
# Where each setting comes from
# --------------------------------------------------------------------------


def test_each_setting_comes_from_the_first_place_that_sets_it(
    project: Path, home: Path
) -> None:
    (project / ".env").write_text("LLM_PROVIDER=claude_code\nLLM_MODEL=\n")

    found = env_module.where_set(project)
    outside = env_module.where_set(project, skip_project=True)

    assert found["LLM_PROVIDER"] == env_module.Setting("claude_code", project / ".env")
    # Set empty, and so not taken from ~/.aure: gpt-4o is not a Claude model.
    assert found["LLM_MODEL"] == env_module.Setting("", project / ".env")
    assert found["LLM_TIMEOUT"] == env_module.Setting("99", home / ".aure")
    assert outside["LLM_PROVIDER"] == env_module.Setting("openai", home / ".aure")
    assert env_module.shown_source(home / ".aure", project) == "~/.aure"
    assert env_module.shown_source(project / ".env", project) == ".env"


def test_the_environment_wins_and_reading_it_changes_nothing(
    project: Path, home: Path, monkeypatch
) -> None:
    (project / ".env").write_text("LLM_PROVIDER=claude_code\n")
    monkeypatch.setenv("LLM_MODEL", "opus")

    found = env_module.where_set(project)

    assert found["LLM_MODEL"] == env_module.Setting("opus", env_module.ENVIRONMENT)
    assert "LLM_PROVIDER" not in os.environ
    assert env_module._loaded is False


def test_a_value_this_process_loaded_is_not_passed_to_a_child_as_the_shells(
    project: Path, home: Path, monkeypatch
) -> None:
    # Loaded into this process, a value would otherwise reach every job as
    # though the shell had set it, and win over a .env written afterwards.
    for name in env_module.KNOWN_VARS:  # so that teardown unsets what loads
        monkeypatch.setenv(name, "x")
        monkeypatch.delenv(name)
    (project / ".env").write_text("LLM_PROVIDER=claude_code\n")
    env_module.load_env(project, force=True)
    (project / ".env").write_text("LLM_PROVIDER=local\n")

    assert os.environ["LLM_PROVIDER"] == "claude_code"  # loaded, and stale
    assert env_module.where_set(project)["LLM_PROVIDER"].value == "local"
    assert "LLM_PROVIDER" not in jobs_module.child_environment()


# --------------------------------------------------------------------------
# Changing the project's .env
# --------------------------------------------------------------------------


def test_a_new_env_holds_only_the_lines_asked_for_and_is_its_owners_alone(
    tmp_path: Path,
) -> None:
    changes = set_values(
        tmp_path,
        {"LLM_PROVIDER": "claude_code", "LLM_MODEL": ""},
        base_revision=ABSENT,
    )

    env = tmp_path / ".env"
    assert env.read_text() == f"{HEADER}\nLLM_PROVIDER=claude_code\nLLM_MODEL=\n"
    assert changes == ["added LLM_PROVIDER=claude_code", "added LLM_MODEL="]
    if os.name != "nt":
        assert env.stat().st_mode & 0o777 == 0o600


def test_every_other_line_is_kept_and_a_variable_is_said_once(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "# my endpoint\n"
        "export LLM_PROVIDER=openai\n"
        f"LLM_API_KEY={PLANTED}\n"
        "LLM_MODEL=gpt-4o\n"
        "# LLM_PROVIDER=claude_code  (a comment, left alone)\n"
        "LLM_MODEL=gpt-4o-mini\n"
        "OTHER_TOOL=1\n"
    )

    set_values(
        tmp_path,
        {"LLM_PROVIDER": "claude_code", "LLM_MODEL": "sonnet"},
        base_revision=read_env_file(tmp_path).revision,
    )

    assert env.read_text() == (
        "# my endpoint\n"
        "LLM_PROVIDER=claude_code\n"
        f"LLM_API_KEY={PLANTED}\n"
        "LLM_MODEL=sonnet\n"
        "# LLM_PROVIDER=claude_code  (a comment, left alone)\n"
        "OTHER_TOOL=1\n"
    )


def test_removing_nrws_lines_leaves_the_rest_as_it_was(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    before = f"# mine\nLLM_API_KEY={PLANTED}\n"
    env.write_text(before)
    both = {"LLM_PROVIDER": "claude_code", "LLM_MODEL": ""}
    set_values(tmp_path, both, base_revision=read_env_file(tmp_path).revision)

    changes = set_values(
        tmp_path,
        dict.fromkeys(both),
        base_revision=read_env_file(tmp_path).revision,
    )

    assert env.read_text() == before
    assert changes == ["removed LLM_PROVIDER=claude_code", "removed LLM_MODEL="]


def test_a_file_written_on_windows_keeps_its_line_endings(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_bytes(b"# mine\r\nLLM_MODEL=gpt-4o\r\n")

    set_values(
        tmp_path,
        {"LLM_PROVIDER": "claude_code", "LLM_MODEL": ""},
        base_revision=read_env_file(tmp_path).revision,
    )

    assert env.read_bytes() == (
        b"# mine\r\nLLM_MODEL=\r\n\r\n" + HEADER.encode() + b"\r\n"
        b"LLM_PROVIDER=claude_code\r\n"
    )


@pytest.mark.skipif(os.name == "nt", reason="symbolic links need privileges")
def test_a_symbolic_link_is_never_written_through(tmp_path: Path) -> None:
    # Planted in a shared project, a link would have nrw write into whatever
    # file it names -- a shell profile, another person's settings.
    target = tmp_path / "profile"
    target.write_text("export PATH=/usr/bin\n")
    project = tmp_path / "proj"
    project.mkdir()
    (project / ".env").symlink_to(target)

    with pytest.raises(EnvFileError, match="symbolic link"):
        set_values(
            project,
            {"LLM_PROVIDER": "claude_code"},
            base_revision=read_env_file(project).revision,
        )

    assert target.read_text() == "export PATH=/usr/bin\n"
    assert (project / ".env").is_symlink()


def test_a_change_made_by_hand_meanwhile_is_never_written_over(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("LLM_PROVIDER=openai\n")
    seen = read_env_file(tmp_path).revision
    env.write_text("LLM_PROVIDER=openai\nLLM_API_KEY=typed-just-now\n")

    with pytest.raises(EnvFileConflict, match="has changed"):
        set_values(tmp_path, {"LLM_PROVIDER": "claude_code"}, base_revision=seen)

    assert env.read_text() == "LLM_PROVIDER=openai\nLLM_API_KEY=typed-just-now\n"


@pytest.mark.parametrize(
    "value", ["a b", "x#y", "$HOME", "sonnet\nLLM_API_KEY=x", '"q"', "x" * 129]
)
def test_a_value_a_dotenv_reader_would_misread_is_refused(
    tmp_path: Path, value: str
) -> None:
    with pytest.raises(EnvFileError, match="one word"):
        set_values(tmp_path, {"LLM_MODEL": value}, base_revision=ABSENT)

    assert not (tmp_path / ".env").exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX modes")
def test_an_existing_env_keeps_its_mode(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("OTHER=1\n")
    env.chmod(0o640)

    set_values(
        tmp_path,
        {"LLM_PROVIDER": "claude_code"},
        base_revision=read_env_file(tmp_path).revision,
    )

    assert env.stat().st_mode & 0o777 == 0o640


# --------------------------------------------------------------------------
# The Settings page's API
# --------------------------------------------------------------------------


def test_the_section_says_what_applies_and_where_without_the_link_or_a_key(
    app,
) -> None:
    shown = app.test_client().get(LLM).json

    assert (shown["choice"], shown["revision"]) == ("outside", ABSENT)
    assert shown["effective"] == {
        "provider": "openai",
        "provider_from": "~/.aure",
        "model": "gpt-4o",
        "model_from": "~/.aure",
        "key": True,
    }
    assert shown["outside"] == shown["effective"]
    assert shown["environment"] == []
    # Not even its shape: reading this needs no link.
    assert PLANTED[-4:] not in json.dumps(shown)


def test_choosing_claude_is_what_the_next_job_reads(app, writer, project: Path) -> None:
    before = as_a_job_sees_it(project)

    response = choose(writer, app, "claude_code")

    assert response.status_code == 200, response.json
    assert response.json["changes"] == [
        "added LLM_PROVIDER=claude_code",
        "added LLM_MODEL=",
    ]
    shown = response.json["llm"]
    assert (shown["choice"], shown["effective"]["provider"]) == (
        "claude_code",
        "claude_code",
    )
    assert shown["effective"]["model_from"] == ".env"
    after = as_a_job_sees_it(project)
    assert "LLM_PROVIDER=openai" in before and "LLM_MODEL=gpt-4o" in before
    # And not ~/.aure's model, which `claude --model gpt-4o` would refuse.
    assert "LLM_PROVIDER=claude_code" in after and "gpt-4o" not in after
    assert "LLM_PROVIDER" not in os.environ  # the server itself loaded nothing


def test_a_model_chosen_is_written_and_what_is_set_outside_can_be_used_again(
    app, writer, project: Path
) -> None:
    chosen = choose(writer, app, "claude_code", model="sonnet").json["llm"]

    assert "LLM_MODEL=sonnet" in (project / ".env").read_text()
    assert chosen["project"] == {"LLM_PROVIDER": "claude_code", "LLM_MODEL": "sonnet"}

    back = choose(writer, app, None)

    assert back.status_code == 200, back.json
    assert back.json["llm"]["effective"]["provider"] == "openai"
    assert (project / ".env").read_text() == ""
    assert "LLM_PROVIDER=openai" in as_a_job_sees_it(project)


def test_a_provider_written_by_hand_is_shown_as_the_projects_own(
    app, project: Path
) -> None:
    (project / ".env").write_text("LLM_PROVIDER=local\nLLM_BASE_URL=http://gpu:8000\n")

    shown = app.test_client().get(LLM).json

    assert shown["choice"] == "other"
    assert shown["project"] == {"LLM_PROVIDER": "local"}
    assert shown["effective"]["provider_from"] == ".env"


def test_saving_or_checking_needs_the_link_and_a_writable_server(
    app, project: Path, home: Path
) -> None:
    stranger = app.test_client()
    refused = stranger.put(
        LLM,
        json={"revision": ABSENT, "provider": "claude_code"},
        headers=headers(app),
    )
    read_only = create_app(project, token=TOKEN, autostart=False, writable=False)
    data = read_only.config["NRW_SETTINGS"]

    assert refused.status_code == 403
    with pytest.raises(PermissionError):
        data.save_llm(ABSENT, "claude_code")
    with pytest.raises(PermissionError):
        data.check_llm()
    assert not (project / ".env").exists()


def test_a_stale_revision_is_refused_and_env_is_unchanged(
    app, writer, project: Path
) -> None:
    seen = writer.get(LLM).json["revision"]
    (project / ".env").write_text("LLM_API_KEY=typed-just-now\n")

    response = choose(writer, app, "claude_code", revision=seen)

    assert response.status_code == 409
    assert response.json["kind"] == "EnvFileConflict"
    assert (project / ".env").read_text() == "LLM_API_KEY=typed-just-now\n"


@pytest.mark.parametrize(
    ("provider", "model"), [("openai", ""), ("claude_code", "a b"), ("claude_code", 3)]
)
def test_only_claude_or_nothing_is_chosen_and_a_model_is_one_word(
    app, writer, project: Path, provider, model
) -> None:
    response = choose(writer, app, provider, model=model)

    assert response.status_code == 400, response.json
    assert not (project / ".env").exists()


def test_the_environment_nrw_serve_was_started_with_is_said_to_win(
    app, project: Path, monkeypatch
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "gemini")

    shown = app.test_client().get(LLM).json

    assert shown["environment"] == ["LLM_PROVIDER"]
    assert shown["effective"]["provider_from"] == "the environment"


def reports(monkeypatch, code: str) -> None:
    """Replace the command a check runs with *code*, run in a child as it is."""
    monkeypatch.setattr(
        jobs_module, "nrw_command", lambda *args: [sys.executable, "-c", code]
    )


def test_a_check_runs_in_the_project_as_a_job_would_and_reports_its_probe(
    app, writer, project: Path, monkeypatch
) -> None:
    choose(writer, app, "claude_code", model="haiku")
    # Says what it would have asked: the model the job's own nrw reads.
    reports(
        monkeypatch,
        "import json, os; from nr_workbench.env import load_env; load_env();"
        "print(json.dumps({'probes': [{'status': 'ok', 'detail': 'answered',"
        " 'model': os.environ['LLM_PROVIDER'] + '/' + os.environ['LLM_MODEL']"
        " + ' in ' + os.getcwd()}]}))",
    )

    response = writer.post(f"{LLM}/check", json={}, headers=headers(app))

    assert response.status_code == 200, response.json
    probe = response.json["probe"]
    assert probe["status"] == "ok"
    assert probe["model"] == f"claude_code/haiku in {os.path.realpath(project)}"


def test_a_check_that_gives_no_report_says_so(app, writer, monkeypatch) -> None:
    reports(monkeypatch, "import sys; print('Traceback: it broke', file=sys.stderr)")

    probe = writer.post(f"{LLM}/check", json={}, headers=headers(app)).json["probe"]

    assert probe == {
        "status": "error",
        "detail": "nrw check-llm gave no report: Traceback: it broke",
    }


def test_a_check_that_does_not_answer_is_504(app, writer, monkeypatch) -> None:
    monkeypatch.setattr(settings_module, "LLM_CHECK_TIMEOUT", 0.5)
    reports(monkeypatch, "import time; time.sleep(30)")

    response = writer.post(f"{LLM}/check", json={}, headers=headers(app))

    assert response.status_code == 504
    assert "did not answer" in response.json["error"]
