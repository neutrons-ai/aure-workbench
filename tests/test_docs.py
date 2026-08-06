"""Guards against documentation drifting away from the code.

A guide that names a command which no longer exists is worse than no guide:
the reader assumes they typed it wrong. These tests are cheap and they are the
only thing standing between a rename and a broken walkthrough.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from click.testing import CliRunner

from nr_workbench.cli import main

DOCS = Path(__file__).resolve().parent.parent / "docs"

#: Command groups, so `nrw model generate` is read as two words and
#: `nrw whence <path>` as one followed by an argument.
GROUPS = {"data", "model", "sample", "fit", "tnr", "skills"}


def commands_in(text: str) -> set[tuple[str, ...]]:
    """Extract the `nrw ...` invocations from a document."""
    found: set[tuple[str, ...]] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("nrw "):
            continue
        parts = stripped.split()
        if len(parts) < 2 or parts[1].startswith("-"):
            continue
        command = [parts[1]]
        if parts[1] in GROUPS and len(parts) > 2 and not parts[2].startswith("-"):
            command.append(parts[2])
        found.add(tuple(command))
    return found


def markdown_files() -> list[Path]:
    """Every shipped markdown document."""
    return sorted(DOCS.rglob("*.md"))


def test_docs_directory_is_present() -> None:
    """A guard that silently checks nothing is worse than none."""
    assert markdown_files(), f"no markdown found under {DOCS}"


@pytest.mark.parametrize("path", markdown_files(), ids=lambda p: p.name)
def test_every_documented_command_exists(path: Path) -> None:
    """Every `nrw ...` in the docs must resolve to a real command."""
    runner = CliRunner()
    missing = []
    for command in sorted(commands_in(path.read_text(encoding="utf-8"))):
        result = runner.invoke(main, [*command, "--help"])
        if result.exit_code != 0:
            missing.append(" ".join(command))
    assert not missing, f"{path.name} documents commands that do not exist: {missing}"


def test_the_getting_started_guide_covers_the_core_workflow() -> None:
    """The guide is the adoption path; it must not lose a load-bearing step.

    Named explicitly rather than counted, because the failure mode is a step
    quietly disappearing in an edit and nobody noticing until a scientist gets
    stuck at it.
    """
    guide = DOCS / "getting-started.md"
    assert guide.is_file(), "docs/getting-started.md is the documented entry point"

    documented = commands_in(guide.read_text(encoding="utf-8"))
    for required in [
        ("init",),
        ("doctor",),
        ("sample", "new"),
        ("sample", "scan"),
        ("data", "overlap"),
        ("tnr", "assess"),
        ("model", "new"),
        ("model", "validate"),
        ("model", "generate"),
        ("fit", "run"),
        ("whence",),
        ("promote",),
        ("check",),
        ("serve",),
    ]:
        assert required in documented, (
            f"the guide no longer shows `nrw {' '.join(required)}`"
        )


def test_docs_carry_no_absolute_user_paths() -> None:
    """A path under someone's home directory is not reproducible.

    This is the exact defect the project exists to remove -- the script it
    replaces carried `/Users/jenni/OneDrive/...` -- so it must not reappear in
    the documentation.
    """
    offenders = []
    allowed = re.compile(r"~/|/Users/<|/home/<")
    for path in markdown_files():
        for number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            for match in re.finditer(r"(?:/Users|/home)/[A-Za-z0-9._-]+", line):
                if allowed.search(match.group(0)):
                    continue
                offenders.append(f"{path.name}:{number} {match.group(0)}")
    assert not offenders, f"absolute home paths in docs: {offenders}"
