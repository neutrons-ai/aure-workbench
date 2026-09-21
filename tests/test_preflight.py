"""The setup guardrail: a request that arrives before the project is ready.

A scientist asks for something reasonable -- "fit my data" -- in a directory
that is not a project yet, or names a sample that does not exist. The danger
is not the error; it is an assistant improvising around it, which produces
work that looks fine and carries no provenance.

Two halves guard that, and both are tested here:

* **the refusals**, which must name the samples that *do* exist, because that
  list is what turns a dead end into a question the scientist can answer;
* **the wiring**, because a skill nobody is told to read is inert. The
  scaffolded instructions have to point at it.

The skill's prose is checked for the v2 anatomy by ``tests/test_skills.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from nr_workbench.cli import main
from nr_workbench.project.layout import ProjectLayout

SKILL = "nrw-preflight"


def run(root: Path, monkeypatch: pytest.MonkeyPatch, *args: str):
    monkeypatch.chdir(root)
    return CliRunner().invoke(main, list(args))


# --------------------------------------------------------------------------
# The message
# --------------------------------------------------------------------------


def test_missing_sample_message_names_the_samples_that_exist(project: Path) -> None:
    """The one fact that makes the error answerable: a typo to fix, or a
    sample to create."""
    layout = ProjectLayout(root=project)

    message = layout.missing_sample_message("Smaple1")

    assert "Smaple1" in message
    assert "Sample1" in message, "the existing sample is what makes this a decision"
    assert "nrw sample new Smaple1" in message


def test_missing_sample_message_when_there_are_no_samples(tmp_path: Path) -> None:
    """A fresh project needs a different sentence: there is nothing to have
    meant instead, so listing nothing would read as a bug."""
    layout = ProjectLayout(root=tmp_path)

    message = layout.missing_sample_message("S1")

    assert "no samples yet" in message.lower()
    assert "nrw sample new S1" in message


# --------------------------------------------------------------------------
# The refusals
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        ("handoff", "S9"),
        ("model", "new", "S9", "--name", "m1"),
        ("data", "check", "S9"),
        ("sample", "reset", "S9"),
    ],
    ids=lambda c: " ".join(c),
)
def test_a_command_given_an_unknown_sample_says_which_ones_exist(
    project: Path, monkeypatch: pytest.MonkeyPatch, command: tuple[str, ...]
) -> None:
    """Every entry point, one wording. These used to be phrased nine
    different ways and none of them listed the alternatives."""
    result = run(project, monkeypatch, *command)

    assert result.exit_code != 0, result.output
    assert "No sample 'S9'" in result.output
    assert "Sample1" in result.output
    assert "nrw sample new S9" in result.output


def test_outside_a_project_the_error_names_the_fix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = run(tmp_path, monkeypatch, "handoff", "S1")

    assert result.exit_code != 0
    assert "No nrw.toml" in result.output
    assert "nrw init" in result.output


def test_scanning_an_unknown_sample_explains_itself_too(project: Path) -> None:
    """The library layer raises its own exception type, but a caller that
    prints it should get the same help as a CLI user."""
    from nr_workbench.project.scan import scan_sample

    with pytest.raises(FileNotFoundError) as caught:
        scan_sample(project, "S9")

    assert "Sample1" in str(caught.value)
    assert "nrw sample new S9" in str(caught.value)


# --------------------------------------------------------------------------
# The wiring
# --------------------------------------------------------------------------


def test_the_preflight_skill_ships(project: Path) -> None:
    """`nrw init` installs it, so it reaches a scientist's project rather
    than living only in this repository."""
    installed = project / "skills" / "reflectometry" / SKILL / "SKILL.md"

    assert installed.is_file(), "nrw init did not install the preflight skill"


def test_the_scaffolded_instructions_send_the_agent_to_it(project: Path) -> None:
    """A skill nobody is told to read is inert: none of the assistants
    auto-discovers `skills/`, so AGENTS.md is what makes this one load."""
    instructions = (project / "AGENTS.md").read_text(encoding="utf-8")

    assert SKILL in instructions
    # And it must come before the handoff, which assumes both facts are
    # already settled.
    assert instructions.index(SKILL) < instructions.index("nrw handoff <sample>")


def test_the_skill_tells_the_agent_not_to_scaffold_unasked(project: Path) -> None:
    """The single most costly thing an assistant can do here is run `nrw
    init` in a directory nobody chose -- seventy files, and a project root
    that shadows the real one."""
    skill = (project / "skills" / "reflectometry" / SKILL / "SKILL.md").read_text(
        encoding="utf-8"
    )

    assert "Do not run `nrw init`" in skill
    assert "Do not create the sample silently" in skill
