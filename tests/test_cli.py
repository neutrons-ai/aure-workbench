"""CLI-level tests, including the import-cost guard."""

from __future__ import annotations

import subprocess
import sys
import textwrap

from click.testing import CliRunner

from nr_workbench import __version__
from nr_workbench.cli import main

#: Modules that must not be imported just to print help. `aure` heads the list:
#: `aure/__init__.py` eagerly imports its workflow package, which chains through
#: every node module into langchain-core, periodictable and scipy -- roughly
#: 1.5-3 seconds. refl1d and matplotlib are nearly as expensive.
FORBIDDEN_ON_HELP = ("aure", "refl1d", "bumps", "matplotlib", "scipy", "langchain_core")


def test_help_lists_the_command_surface() -> None:
    result = CliRunner().invoke(main, ["--help"])

    assert result.exit_code == 0
    for command in ("init", "doctor", "sample", "skills"):
        assert command in result.output


def test_version_flag_reports_the_package_version() -> None:
    result = CliRunner().invoke(main, ["--version"])

    assert result.exit_code == 0
    assert __version__ in result.output


def test_help_does_not_import_heavy_modules() -> None:
    """`nrw --help` must stay instant.

    Every subcommand is lazily imported for this reason. A single top-level
    `import aure` in cli.py or a commands module would add seconds to every
    invocation, including tab-completion and `--help`. Run in a subprocess so
    the check is not fooled by modules the test session already imported.
    """
    script = textwrap.dedent(
        f"""
        import sys
        from click.testing import CliRunner
        from nr_workbench.cli import main

        result = CliRunner().invoke(main, ["--help"])
        assert result.exit_code == 0, result.output

        leaked = [m for m in {FORBIDDEN_ON_HELP!r} if m in sys.modules]
        if leaked:
            print("LEAKED:" + ",".join(leaked))
            sys.exit(1)
        """
    )

    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=120
    )

    assert result.returncode == 0, (
        f"`nrw --help` imported heavy modules: {result.stdout.strip()}\n"
        "Move the import inside the command callback that needs it."
    )


def test_subcommands_have_help_text() -> None:
    """Each command needs a usable one-liner; the CLI is the primary UI."""
    runner = CliRunner()

    for command in ("init", "doctor", "sample", "skills"):
        result = runner.invoke(main, [command, "--help"])
        assert result.exit_code == 0, f"{command}: {result.output}"
        assert result.output.strip()


def test_skills_list_bundled_works_without_a_project(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(main, ["skills", "list", "--bundled"])

    assert result.exit_code == 0, result.output
    assert "nr-workbench-project" in result.output


def test_skills_list_without_a_project_suggests_bundled(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(main, ["skills", "list"])

    assert result.exit_code != 0
    assert "--bundled" in result.output


def test_skills_path_prints_an_existing_directory() -> None:
    from pathlib import Path

    result = CliRunner().invoke(main, ["skills", "path"])

    assert result.exit_code == 0
    assert Path(result.output.strip()).is_dir()
