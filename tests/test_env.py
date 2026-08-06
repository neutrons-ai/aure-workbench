"""Loading configuration from `.env`, `~/.nrw` and `~/.aure`."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from nr_workbench import env as env_module
from nr_workbench.env import describe, load_env


@pytest.fixture(autouse=True)
def fresh(monkeypatch: pytest.MonkeyPatch):
    """Reset the load-once flag and clear the variables under test."""
    monkeypatch.setattr(env_module, "_loaded", False)
    for name in env_module.KNOWN_VARS:
        monkeypatch.delenv(name, raising=False)
    # Point the per-user files somewhere empty unless a test says otherwise.
    monkeypatch.setattr(env_module, "USER_ENV_PATH", Path("/nonexistent/.nrw"))
    monkeypatch.setattr(env_module, "AURE_ENV_PATH", Path("/nonexistent/.aure"))


def test_a_project_env_file_is_read(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "nrw.toml").write_text("", encoding="utf-8")
    (tmp_path / ".env").write_text("LLM_PROVIDER=openai\nLLM_MODEL=gpt-4o\n")

    sources = load_env(tmp_path)

    assert os.environ["LLM_PROVIDER"] == "openai"
    assert tmp_path / ".env" in sources.files


def test_the_shell_always_wins(tmp_path: Path, monkeypatch) -> None:
    """`LLM_MODEL=x nrw ...` must override the file, not the other way round."""
    (tmp_path / "nrw.toml").write_text("", encoding="utf-8")
    (tmp_path / ".env").write_text("LLM_MODEL=from-file\n")
    monkeypatch.setenv("LLM_MODEL", "from-shell")

    load_env(tmp_path)

    assert os.environ["LLM_MODEL"] == "from-shell"


def test_a_user_file_fills_in_what_the_project_did_not_set(
    tmp_path: Path, monkeypatch
) -> None:
    """Per-user defaults are for the key; the project file is for the model."""
    (tmp_path / "nrw.toml").write_text("", encoding="utf-8")
    (tmp_path / ".env").write_text("LLM_MODEL=project-model\n")
    user = tmp_path / "user-nrw"
    user.write_text("LLM_API_KEY=secret\nLLM_MODEL=user-model\n")
    monkeypatch.setattr(env_module, "USER_ENV_PATH", user)

    load_env(tmp_path)

    assert os.environ["LLM_MODEL"] == "project-model", "the project file wins"
    assert os.environ["LLM_API_KEY"] == "secret"


def test_an_existing_aure_setup_is_reused(tmp_path: Path, monkeypatch) -> None:
    """A machine already configured for AuRE should work here unchanged."""
    (tmp_path / "nrw.toml").write_text("", encoding="utf-8")
    aure = tmp_path / "user-aure"
    aure.write_text("LLM_PROVIDER=gemini\n")
    monkeypatch.setattr(env_module, "AURE_ENV_PATH", aure)

    sources = load_env(tmp_path)

    assert os.environ["LLM_PROVIDER"] == "gemini"
    assert aure in sources.files, "and it is reported, not silent"


def test_the_search_stops_at_the_project_root(tmp_path: Path) -> None:
    """A .env above the project belongs to something else."""
    outer = tmp_path / "outer"
    project = outer / "proj"
    project.mkdir(parents=True)
    (outer / ".env").write_text("LLM_MODEL=not-ours\n")
    (project / "nrw.toml").write_text("", encoding="utf-8")

    load_env(project)

    assert "LLM_MODEL" not in os.environ


def test_loading_happens_once(tmp_path: Path) -> None:
    """Repeated calls must not re-read on every LLM check."""
    (tmp_path / "nrw.toml").write_text("", encoding="utf-8")
    (tmp_path / ".env").write_text("LLM_MODEL=first\n")

    load_env(tmp_path)
    (tmp_path / ".env").write_text("LLM_MODEL=second\n")
    second = load_env(tmp_path)

    assert second.files == [], "the second call is a no-op"
    assert load_env(tmp_path, force=True).files, "unless forced"


def test_secrets_are_redacted_to_a_shape(monkeypatch) -> None:
    """`nrw doctor` prints this. It must distinguish two keys, not reveal one."""
    monkeypatch.setenv("LLM_API_KEY", "sk-abcdefghijklmnop1234")
    monkeypatch.setenv("LLM_MODEL", "gpt-4o")

    report = describe()

    assert "sk-abcdefghijklmnop1234" not in report["LLM_API_KEY"]
    assert report["LLM_API_KEY"].endswith("1234)")
    assert "23 chars" in report["LLM_API_KEY"]
    assert report["LLM_MODEL"] == "gpt-4o", "non-secrets are shown in full"


def test_a_short_secret_is_not_partially_revealed(monkeypatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "abcd")

    assert describe()["LLM_API_KEY"] == "set (short)"


def test_help_does_not_import_dotenv() -> None:
    """Loading costs ~40 ms, and `nrw --help` is run constantly."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from click.testing import CliRunner; "
            "from nr_workbench.cli import main; "
            "CliRunner().invoke(main, ['--help']); "
            "bad = [m for m in ('dotenv', 'aure', 'flask') if m in sys.modules]; "
            "assert not bad, bad; print('clean')",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "clean" in result.stdout


def test_the_scaffold_gitignores_dotenv() -> None:
    """An API key in a beamtime repository is a real problem.

    These directories get shared with collaborators, archived by the facility,
    and sometimes published alongside a paper.
    """
    template = (
        Path(__file__).resolve().parent.parent
        / "src/nr_workbench/templates/project/.gitignore"
    )

    ignored = template.read_text(encoding="utf-8")

    assert "\n.env\n" in ignored


def test_the_scaffold_ships_an_env_example(project: Path) -> None:
    """Nobody guesses variable names. The template lists them."""
    example = project / ".env.example"

    assert example.is_file()
    text = example.read_text(encoding="utf-8")
    assert "LLM_PROVIDER" in text
    assert "LLM_BASE_URL" in text
    assert "--print-prompt" in text, "and says what to do without an endpoint"
