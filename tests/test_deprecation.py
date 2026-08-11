"""Marking a spec abandoned, without disturbing what it already produced.

The constraint that shapes this: every generated script records the sha256 of
its spec, and `nrw check` compares that against the file. A single added comment
line trips it -- so a naive deprecation banner would report a stale script for
every fit the spec ever produced, on exactly the specs whose history is being
preserved, and poison the one command you run before calling a result final.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from click.testing import CliRunner

from nr_workbench.cli import main
from nr_workbench.spec import deprecation

pytestmark = pytest.mark.integration

SPEC = """\
# yaml-language-server: $schema=../../../.nrw/schema/nrw-model-1.json
schema: nrw-model/1
name: doomed
sample: Sample1
materials:
  Air: {rho: 0.0}
  Film: {rho: 4.0}
  Si: {rho: 2.07}
stack:
  - {name: Air, material: Air, thickness: 0, roughness: 5}
  - {name: Film, material: Film, thickness: 100, roughness: 5}
  - {name: Si, material: Si}
probe: {resolution: angular_only, dq_is_fwhm: true}
states:
  - name: run100001
    run: 100001
    segments: auto
    thetas: [0.45]
    data_dir: samples/Sample1/data/steady
parameters:
  - {path: Film.thickness, range: [50, 200], per: state}
"""


# --------------------------------------------------------------------------
# The banner, and the hash rule that makes it safe
# --------------------------------------------------------------------------


def test_stripping_a_banner_restores_the_original_bytes() -> None:
    """The whole design rests on this being exact."""
    marked = deprecation.banner("inverted geometry") + SPEC

    assert deprecation.strip(marked) == SPEC


def test_the_identity_hash_ignores_the_banner(tmp_path: Path) -> None:
    """So nothing generated from the spec changes state when it is labelled."""
    path = tmp_path / "doomed.yaml"
    path.write_text(SPEC, encoding="utf-8")
    before = deprecation.identity_hash(path)

    path.write_text(deprecation.banner("inverted geometry") + SPEC, encoding="utf-8")

    assert deprecation.identity_hash(path) == before
    assert hashlib.sha256(path.read_bytes()).hexdigest() != before, (
        "the file really did change; only its identity did not"
    )


def test_a_long_reason_is_wrapped_and_read_back_whole() -> None:
    reason = (
        "the stack was ordered Si-first, inverting the back-reflection geometry, "
        "so the model plateau collapsed to R~0.25 where the data sits at 0.8"
    )
    marked = deprecation.banner(reason)

    assert deprecation.is_deprecated(marked)
    assert deprecation.reason_of(marked) == reason


def test_an_unmarked_spec_reports_nothing() -> None:
    assert not deprecation.is_deprecated(SPEC)
    assert deprecation.reason_of(SPEC) == ""
    assert deprecation.strip(SPEC) == SPEC


# --------------------------------------------------------------------------
# The command
# --------------------------------------------------------------------------


def run(root: Path, monkeypatch: pytest.MonkeyPatch, *args: str):
    monkeypatch.chdir(root)
    return CliRunner().invoke(main, list(args))


@pytest.fixture
def spec(project: Path) -> Path:
    models = project / "samples" / "Sample1" / "models"
    models.mkdir(parents=True, exist_ok=True)
    path = models / "doomed.yaml"
    path.write_text(SPEC, encoding="utf-8")
    return path


def test_deprecating_writes_the_banner_at_the_top(project, spec, monkeypatch) -> None:
    result = run(
        project,
        monkeypatch,
        "model",
        "deprecate",
        str(spec),
        "--reason",
        "inverted geometry",
    )

    assert result.exit_code == 0, result.output
    text = spec.read_text(encoding="utf-8")
    assert text.startswith(deprecation.BEGIN)
    assert "inverted geometry" in text


def test_a_reason_is_required(project, spec, monkeypatch) -> None:
    """Why a model was abandoned is the part nobody can reconstruct later."""
    result = run(project, monkeypatch, "model", "deprecate", str(spec))

    assert result.exit_code != 0
    assert "--reason is required" in result.output
    assert not deprecation.is_deprecated(spec.read_text(encoding="utf-8"))


def test_deprecating_twice_is_refused(project, spec, monkeypatch) -> None:
    run(project, monkeypatch, "model", "deprecate", str(spec), "--reason", "one")

    again = run(
        project, monkeypatch, "model", "deprecate", str(spec), "--reason", "two"
    )

    assert again.exit_code != 0
    assert "already deprecated" in again.output


def test_undo_restores_the_file_exactly(project, spec, monkeypatch) -> None:
    original = spec.read_bytes()
    run(project, monkeypatch, "model", "deprecate", str(spec), "--reason", "wrong call")

    result = run(project, monkeypatch, "model", "deprecate", str(spec), "--undo")

    assert result.exit_code == 0, result.output
    assert spec.read_bytes() == original


def test_undo_on_a_live_spec_is_refused(project, spec, monkeypatch) -> None:
    result = run(project, monkeypatch, "model", "deprecate", str(spec), "--undo")

    assert result.exit_code != 0
    assert "not deprecated" in result.output


def test_the_banner_leaves_the_spec_parseable(project, spec, monkeypatch) -> None:
    """It is a YAML comment, so everything that reads a spec still can."""
    from nr_workbench.spec.models import load_spec

    run(project, monkeypatch, "model", "deprecate", str(spec), "--reason", "inverted")

    assert load_spec(spec).name == "doomed"


# --------------------------------------------------------------------------
# What the rest of the tool does about it
# --------------------------------------------------------------------------


def test_generate_refuses_a_deprecated_spec(project, spec, monkeypatch) -> None:
    """A deprecated spec is not a model to run; the refusal names the reason."""
    run(project, monkeypatch, "model", "deprecate", str(spec), "--reason", "Si-first")

    result = run(project, monkeypatch, "model", "generate", str(spec))

    assert result.exit_code != 0
    assert "deprecated" in result.output
    assert "Si-first" in result.output
    assert not spec.with_suffix(".py").exists()


def test_validate_says_deprecated_without_calling_it_invalid(
    project, spec, monkeypatch
) -> None:
    """It may be a perfectly valid spec. What matters is that it was rejected."""
    run(project, monkeypatch, "model", "deprecate", str(spec), "--reason", "Si-first")

    result = run(project, monkeypatch, "model", "validate", str(spec))

    assert "DEPRECATED" in result.output
    assert "Si-first" in result.output


def test_check_does_not_call_the_generated_script_stale(
    project, spec, monkeypatch
) -> None:
    """The reason the banner is fenced and hash-neutral.

    A bare comment line added to a spec makes `nrw check` report `stale-script`
    for it -- measured, not assumed. Deprecating must not, or labelling the
    abandoned specs would poison the command you run before calling anything
    final.
    """
    (project / "samples/Sample1/data/steady").mkdir(parents=True, exist_ok=True)
    from .test_lifecycle import write_partials

    write_partials(project / "samples/Sample1/data/steady", 100001, segments=1)
    assert run(project, monkeypatch, "model", "generate", str(spec)).exit_code == 0
    assert "stale-script" not in run(project, monkeypatch, "check").output

    run(project, monkeypatch, "model", "deprecate", str(spec), "--reason", "Si-first")

    assert "stale-script" not in run(project, monkeypatch, "check").output


def test_an_agent_session_lists_a_deprecated_spec_as_settled(
    project, spec, monkeypatch
) -> None:
    """Its contradictions are not work to do; the reason is what matters."""
    from nr_workbench.agent import session

    notes = project / "samples/Sample1/sample.md"
    notes.write_text(
        "# Sample1\n\n## Fits to perform\n\nCo-refine the two runs.\n", encoding="utf-8"
    )
    run(project, monkeypatch, "model", "deprecate", str(spec), "--reason", "Si-first")

    composed = session.compose(project, "Sample1")

    assert "already marked abandoned" in composed.prompt
    assert "Si-first" in composed.prompt
