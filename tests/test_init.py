"""End-to-end tests for `nrw init` and `nrw sample new`."""

from __future__ import annotations

import json
import shutil
import tomllib
from pathlib import Path

import pytest
from click.testing import CliRunner

from nr_workbench.cli import main
from nr_workbench.commands.init_cmd import plan_project_files
from nr_workbench.commands.sample import plan_sample_files, validate_sample_id
from nr_workbench.project.layout import SAMPLE_SUBDIRS, ProjectLayout
from nr_workbench.project.render import RenderContext

#: The scaffold's own files. A golden list rather than a loose assertion,
#: because the classic packaging failure is a file silently vanishing from the
#: wheel -- and a test that only checks "some files appeared" misses it.
#:
#: The skill files are derived below rather than listed: which skills ship is
#: data that changes as the library grows, whereas this shape should not.
EXPECTED_SCAFFOLD_FILES = {
    ".env.example",
    ".github/copilot-instructions.md",
    ".gitignore",
    ".vscode/extensions.json",
    ".vscode/settings.json",
    # The limits an assistant works under. Two independent mechanisms: this
    # deny list plus a PreToolUse hook, because an instruction to a model is
    # a request and only a hook is a limit.
    ".claude/settings.json",
    # The tool-neutral instruction body. Read directly by OpenCode and Copilot,
    # imported by CLAUDE.md, so there is one copy rather than one per assistant.
    "AGENTS.md",
    "CLAUDE.md",
    "README.md",
    "docs/ground_truths.md",
    "nrw.toml",
    "samples/.gitkeep",
}


def expected_project_files() -> set[str]:
    """The full file set `nrw init` should produce, skills included."""
    from nr_workbench.commands.init_cmd import SEED_SKILLS
    from nr_workbench.skills_install import discover_skills, plan_skill_files

    files = set(EXPECTED_SCAFFOLD_FILES)
    bundled = {s.name: s for s in discover_skills()}
    for name in SEED_SKILLS:
        files.update(relpath for relpath, _ in plan_skill_files(bundled[name]))
    return files


def installed_files(root: Path) -> set[str]:
    """Every file under root, as POSIX relpaths, excluding machine state."""
    return {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and ".nrw/" not in path.relative_to(root).as_posix()
    }


# --------------------------------------------------------------------------
# The scaffold contents
# --------------------------------------------------------------------------


def test_init_produces_the_expected_file_set(
    tmp_path: Path, context: RenderContext
) -> None:
    from nr_workbench.project.scaffold import apply_scaffold

    apply_scaffold(tmp_path, plan_project_files(context))

    assert installed_files(tmp_path) == expected_project_files()


def test_init_writes_skills_to_repo_root_not_dot_claude(project: Path) -> None:
    """Recorded decision: Copilot cannot read `.claude/skills/`, so skills live
    at the repo root and both assistants reach them by path."""
    assert (project / "skills" / "reflectometry").is_dir()
    assert not (project / ".claude" / "skills").exists()


def test_init_writes_matching_dispatcher_pairs(project: Path) -> None:
    claude = sorted(p.name for p in (project / ".claude" / "agents").glob("*.md"))
    copilot = sorted(p.name for p in (project / ".github" / "agents").glob("*.md"))

    assert claude == copilot
    assert claude, "expected dispatcher agents to be installed"


# --------------------------------------------------------------------------
# Harness selection
# --------------------------------------------------------------------------


def test_init_scaffolds_for_claude_and_copilot_by_default(project: Path) -> None:
    """The default set is what every project got before harnesses existed.

    Pinned, because the whole point of making harnesses selectable was that a
    project which says nothing must be unaffected by it.
    """
    config = tomllib.loads((project / "nrw.toml").read_text(encoding="utf-8"))

    assert config["harness"]["kinds"] == ["claude", "copilot"]
    assert (project / "CLAUDE.md").is_file()
    assert (project / ".claude" / "settings.json").is_file()
    assert (project / ".github" / "agents").is_dir()


def test_init_for_one_harness_omits_the_others_files(
    tmp_path: Path, context: RenderContext
) -> None:
    """A project that does not use an assistant must not carry its files.

    Before this, every project got a `.github/agents/` tree of dispatchers
    whether or not anyone read them.
    """
    from dataclasses import replace

    from nr_workbench.project.scaffold import apply_scaffold

    apply_scaffold(
        tmp_path, plan_project_files(replace(context, harnesses=("claude",)))
    )
    installed = installed_files(tmp_path)

    assert "CLAUDE.md" in installed
    assert ".claude/settings.json" in installed
    assert not any(path.startswith(".github/agents/") for path in installed)
    assert ".github/copilot-instructions.md" in installed, (
        "the shared instruction body belongs to the project, not to Copilot -- "
        "CLAUDE.md @-imports it and it must survive deselecting Copilot"
    )


def test_init_for_copilot_alone_writes_no_claude_files(
    tmp_path: Path, context: RenderContext
) -> None:
    from dataclasses import replace

    from nr_workbench.project.scaffold import apply_scaffold

    apply_scaffold(
        tmp_path, plan_project_files(replace(context, harnesses=("copilot",)))
    )
    installed = installed_files(tmp_path)

    assert not any(path.startswith(".claude/") for path in installed)
    assert "CLAUDE.md" not in installed
    assert ".github/agents/neutron-reflectometry.md" in installed
    assert "skills/reflectometry/neutron-reflectometry/SKILL.md" in installed, (
        "skills are tool-neutral and install for any harness"
    )


def test_init_keeps_a_narrowed_harness_set_on_a_later_run(tmp_path: Path) -> None:
    """Re-running without --harness must not silently re-add what was dropped."""
    runner = CliRunner()
    first = runner.invoke(main, ["init", str(tmp_path), "--harness", "claude"])
    assert first.exit_code == 0, first.output

    second = runner.invoke(main, ["init", str(tmp_path), "--check"])

    assert second.exit_code == 0, (
        f"a re-run should be clean, not pending: {second.output}"
    )
    assert not (tmp_path / ".github" / "agents").exists()


def test_init_adds_a_harness_without_disturbing_the_existing_one(
    tmp_path: Path,
) -> None:
    runner = CliRunner()
    runner.invoke(main, ["init", str(tmp_path), "--harness", "claude"])
    before = (tmp_path / "CLAUDE.md").read_bytes()

    result = runner.invoke(
        main, ["init", str(tmp_path), "--harness", "claude", "--harness", "copilot"]
    )

    assert result.exit_code == 0, result.output
    assert (tmp_path / ".github" / "agents").is_dir()
    assert (tmp_path / "CLAUDE.md").read_bytes() == before


def test_init_never_deletes_a_deselected_harnesss_files(tmp_path: Path) -> None:
    """Narrowing the set leaves what is already on disk alone.

    `nrw init` never removes a file. A scientist who has edited a dispatcher
    would otherwise lose it to a config change.
    """
    runner = CliRunner()
    runner.invoke(main, ["init", str(tmp_path)])
    stub = tmp_path / ".github" / "agents" / "neutron-reflectometry.md"
    assert stub.is_file()

    result = runner.invoke(main, ["init", str(tmp_path), "--harness", "claude"])

    assert result.exit_code == 0, result.output
    assert stub.is_file(), "deselecting a harness must not delete its files"


def test_the_instruction_body_lives_in_exactly_one_file(project: Path) -> None:
    """CLAUDE.md points at AGENTS.md rather than repeating it.

    Two copies of the same instructions is the drift this project avoids for
    skills; it would be no better one level up. The marker is a sentence from
    the body that must appear once and be reachable from CLAUDE.md.
    """
    marker = "Never hand-edit a generated fit script"
    claude = (project / "CLAUDE.md").read_text(encoding="utf-8")
    agents = (project / "AGENTS.md").read_text(encoding="utf-8")

    assert marker in agents
    assert marker not in claude, "the body must not be duplicated into CLAUDE.md"
    assert "@AGENTS.md" in claude, "CLAUDE.md must import the shared body"
    assert "@.github/copilot-instructions.md" in claude


def test_agents_md_names_only_the_directories_this_project_has(
    tmp_path: Path, context: RenderContext
) -> None:
    """Telling an assistant to look in a directory that was never written is
    the kind of wrong that costs tool calls before it is noticed."""
    from dataclasses import replace

    from nr_workbench.project.scaffold import apply_scaffold

    apply_scaffold(
        tmp_path, plan_project_files(replace(context, harnesses=("claude",)))
    )
    agents = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")

    assert "`.claude/agents/`" in agents
    assert ".github/agents" not in agents
    assert ".opencode/agents" not in agents


def test_every_scaffolded_text_file_ends_with_a_newline(project: Path) -> None:
    """Jinja drops the trailing newline unless told not to.

    Every `.j2`-rendered file shipped without one for months: git reports
    "\\ No newline at end of file" on each, and a scaffolded project running
    its own pre-commit would rewrite them on the first commit. Invisible until
    a file that previously had one became a template.
    """
    offenders = [
        path.relative_to(project).as_posix()
        for path in project.rglob("*")
        if path.is_file()
        and path.suffix in {".md", ".toml", ".json", ".yaml", ".py"}
        and ".nrw/" not in path.relative_to(project).as_posix()
        and path.stat().st_size
        and not path.read_bytes().endswith(b"\n")
    ]

    assert not offenders, f"no trailing newline: {offenders}"


def test_init_rejects_an_unknown_harness(tmp_path: Path) -> None:
    result = CliRunner().invoke(main, ["init", str(tmp_path), "--harness", "emacs"])

    assert result.exit_code != 0
    assert "emacs" in result.output
    assert "claude" in result.output, "the error must say what is available"


def test_init_renders_config_with_the_given_identity(project: Path) -> None:
    config = tomllib.loads((project / "nrw.toml").read_text(encoding="utf-8"))

    assert config["project"]["name"] == "test-project"
    assert config["project"]["instrument"] == "REF_L"
    assert config["beamtime"]["label"] == "june2026"
    # Descriptive, and no longer one value: REF_L's two live reductions
    # disagree about the 4th column, so it is read per file from the header.
    assert config["conventions"]["dq_convention"] == "per-file, read from the header"


def test_init_leaves_no_unrendered_jinja_markers(project: Path) -> None:
    """A stray `{{ ... }}` means a template variable was never substituted."""
    offenders = [
        path.relative_to(project).as_posix()
        for path in project.rglob("*")
        if path.is_file() and path.suffix in {".toml", ".md", ".yaml", ".json"}
        for text in [path.read_text(encoding="utf-8", errors="ignore")]
        if "{{" in text or "{%" in text
    ]

    assert offenders == []


def test_init_writes_no_j2_suffixed_files(project: Path) -> None:
    assert list(project.rglob("*.j2")) == []


def test_init_never_installs_byte_compiled_files(
    tmp_path: Path, context: RenderContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stale .pyc files must never leak into a scaffolded project.

    Both the template tree and the skills tree live inside the package, so pip
    byte-compiles any `.py` they carry on install: a skill's `scripts/*.py`
    gains a sibling `__pycache__/*.cpython-3xx.pyc` in site-packages. Copying
    that into a user's project is wrong and confusing. An editable install
    never shows it -- nothing compiles the source tree -- so the condition is
    simulated here for both trees.
    """
    from nr_workbench import skills_install
    from nr_workbench.project import render
    from nr_workbench.project.scaffold import apply_scaffold

    fake_templates = tmp_path / "pkg" / "templates"
    shutil.copytree(render.templates_root(), fake_templates)
    template_cache = fake_templates / "project" / "__pycache__"
    template_cache.mkdir(parents=True, exist_ok=True)
    (template_cache / "anything.cpython-314.pyc").write_bytes(b"\x00compiled")
    monkeypatch.setattr(render, "templates_root", lambda: fake_templates)

    fake_skills = tmp_path / "pkg" / "skills"
    shutil.copytree(skills_install.bundled_skills_root(), fake_skills)
    scripts = next(fake_skills.rglob("scripts"), None)
    assert scripts is not None, "expected at least one bundled skill with scripts/"
    skill_cache = scripts / "__pycache__"
    skill_cache.mkdir(parents=True, exist_ok=True)
    (skill_cache / "summarize.cpython-314.pyc").write_bytes(b"\x00compiled")
    monkeypatch.setattr(skills_install, "bundled_skills_root", lambda: fake_skills)

    target = tmp_path / "proj"
    apply_scaffold(target, plan_project_files(context))

    assert list(target.rglob("*.pyc")) == []
    assert list(target.rglob("__pycache__")) == []


def test_scaffold_lock_records_every_installed_file(project: Path) -> None:
    lock = json.loads(
        (project / ".nrw" / "scaffold.lock.json").read_text(encoding="utf-8")
    )

    assert lock["schema"] == "nrw-scaffold-lock/1"
    assert set(lock["files"]) >= expected_project_files()


def test_init_writes_the_spec_schema_the_editor_points_at(project: Path) -> None:
    """`.vscode/settings.json` names this file; init has to actually write it.

    It did not, for a while. Projects scaffolded in that period showed
    "Unable to load schema ... No content" on every spec and got no editor
    validation at all, which stays invisible until a typo survives to fit time.
    """
    from nr_workbench.spec.schema import schema_bytes

    schema = project / ".nrw" / "schema" / "nrw-model-1.json"

    assert schema.is_file(), "nrw init did not write the project's JSON Schema"
    assert schema.read_bytes() == schema_bytes()

    settings = json.loads(
        (project / ".vscode" / "settings.json").read_text(encoding="utf-8")
    )
    assert "./.nrw/schema/nrw-model-1.json" in set(settings["yaml.schemas"])


def test_doctor_reports_a_missing_spec_schema(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A project scaffolded before the fix must not stay silently broken."""
    from nr_workbench.commands.doctor import collect_checks

    (project / ".nrw" / "schema" / "nrw-model-1.json").unlink()
    monkeypatch.chdir(project)

    by_name = {check.name: check for check in collect_checks()}

    assert by_name["spec schema"].status == "missing"


# --------------------------------------------------------------------------
# Samples
# --------------------------------------------------------------------------


def test_sample_plan_creates_the_standard_subdirs(context: RenderContext) -> None:
    paths = {
        relpath
        for _, relpath in ((p, p.relpath) for p in plan_sample_files(context, "S1"))
    }

    assert "samples/S1/sample.md" in paths
    assert "samples/S1/sample.yaml" in paths
    for subdir in SAMPLE_SUBDIRS:
        assert f"samples/S1/{subdir}/.gitkeep" in paths


def test_layout_lists_samples_holding_a_sample_md(project: Path) -> None:
    """A stray directory under samples/ must not be mistaken for a sample."""
    (project / "samples" / "not-a-sample").mkdir()

    assert ProjectLayout(project).list_samples() == ["Sample1"]


@pytest.mark.parametrize("sample_id", ["Sample1", "S-4", "cu_thf_2"])
def test_validate_sample_id_accepts_safe_ids(sample_id: str) -> None:
    assert validate_sample_id(sample_id) == sample_id


@pytest.mark.parametrize(
    "sample_id", ["", "with space", "../escape", "a/b", "sample:1"]
)
def test_validate_sample_id_rejects_unsafe_ids(sample_id: str) -> None:
    """IDs become directory names, so path separators must never get through."""
    with pytest.raises(ValueError):
        validate_sample_id(sample_id)


# --------------------------------------------------------------------------
# The CLI
# --------------------------------------------------------------------------


def test_init_then_init_again_reports_no_changes(tmp_path: Path) -> None:
    """Re-running must be a true no-op, including the `created` timestamp."""
    runner = CliRunner()
    target = str(tmp_path / "proj")

    first = runner.invoke(main, ["init", target, "--beamtime", "bt-1"])
    assert first.exit_code == 0, first.output
    created_before = (tmp_path / "proj" / "nrw.toml").read_text(encoding="utf-8")

    second = runner.invoke(main, ["init", target])

    assert second.exit_code == 0, second.output
    assert "upgrade" not in second.output
    assert (tmp_path / "proj" / "nrw.toml").read_text(
        encoding="utf-8"
    ) == created_before


def test_init_check_exits_zero_when_up_to_date(tmp_path: Path) -> None:
    runner = CliRunner()
    target = str(tmp_path / "proj")
    runner.invoke(main, ["init", target])

    result = runner.invoke(main, ["init", target, "--check"])

    assert result.exit_code == 0
    assert "up to date" in result.output


def test_init_check_exits_two_when_a_file_has_drifted(tmp_path: Path) -> None:
    """The CI signal: pending scaffold changes must fail a check run."""
    runner = CliRunner()
    target = tmp_path / "proj"
    runner.invoke(main, ["init", str(target)])
    (target / "CLAUDE.md").write_text("edited\n", encoding="utf-8")

    result = runner.invoke(main, ["init", str(target), "--check"])

    assert result.exit_code == 2
    assert "CLAUDE.md" in result.output
    assert (target / "CLAUDE.md").read_text(encoding="utf-8") == "edited\n"


def test_init_on_a_non_empty_directory_preserves_existing_files(tmp_path: Path) -> None:
    """`init` must be safe to run on top of a live beamtime folder."""
    target = tmp_path / "beamtime"
    target.mkdir()
    (target / "my_old_fit.py").write_text("problem = None\n", encoding="utf-8")

    result = CliRunner().invoke(main, ["init", str(target)])

    assert result.exit_code == 0, result.output
    assert (target / "my_old_fit.py").read_text(encoding="utf-8") == "problem = None\n"
    assert (target / "nrw.toml").is_file()


def test_init_refuses_to_nest_inside_an_existing_project(tmp_path: Path) -> None:
    """`cd` into a sample's data directory out of habit and run `init` there,
    and it must not quietly grow a second project underneath the first."""
    runner = CliRunner()
    outer = tmp_path / "proj"
    runner.invoke(main, ["init", str(outer)])
    inner = outer / "samples" / "S1" / "data" / "steady"
    inner.mkdir(parents=True)

    result = runner.invoke(main, ["init", str(inner)])

    assert result.exit_code != 0
    assert str(outer) in result.output
    assert "nrw sample new" in result.output
    assert "--nested" in result.output
    assert not (inner / "nrw.toml").is_file()


def test_init_nested_flag_overrides_the_refusal(tmp_path: Path) -> None:
    """The escape hatch exists for the rare case it really is intentional."""
    runner = CliRunner()
    outer = tmp_path / "proj"
    runner.invoke(main, ["init", str(outer)])
    inner = outer / "samples" / "S1"
    inner.mkdir(parents=True)

    result = runner.invoke(main, ["init", str(inner), "--nested"])

    assert result.exit_code == 0, result.output
    assert (inner / "nrw.toml").is_file()


def test_init_in_place_upgrade_is_not_refused_as_nested(tmp_path: Path) -> None:
    """A project re-initializing itself must never trip the nested guard --
    only an ancestor's `nrw.toml` should, not the project's own."""
    runner = CliRunner()
    target = tmp_path / "proj"
    runner.invoke(main, ["init", str(target)])

    result = runner.invoke(main, ["init", str(target)])

    assert result.exit_code == 0, result.output
    assert "nested" not in result.output.lower()


def test_sample_new_from_inside_a_sample_anchors_at_the_project_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`sample new` walks up to find `nrw.toml`, so running it from deep
    inside another sample's directory must still create the new sample under
    the one project root -- never nested under wherever the cwd happened to
    be."""
    runner = CliRunner()
    target = tmp_path / "proj"
    runner.invoke(main, ["init", str(target)])
    monkeypatch.chdir(target)
    runner.invoke(main, ["sample", "new", "S1"], catch_exceptions=False)
    cwd = target / "samples" / "S1" / "data" / "steady"
    cwd.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(cwd)

    result = runner.invoke(main, ["sample", "new", "S2"])

    assert result.exit_code == 0, result.output
    assert (target / "samples" / "S2" / "sample.md").is_file()
    assert not (cwd / "samples").exists()


def test_sample_new_creates_the_sample(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = CliRunner()
    target = tmp_path / "proj"
    runner.invoke(main, ["init", str(target)])
    monkeypatch.chdir(target)

    result = runner.invoke(
        main, ["sample", "new", "Sample4", "--title", "ionomer on copper"]
    )

    assert result.exit_code == 0, result.output
    assert (target / "samples" / "Sample4" / "sample.md").is_file()
    assert (
        "ionomer on copper"
        in (target / "samples" / "Sample4" / "sample.md").read_text()
    )


def test_sample_new_without_a_project_fails_with_guidance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(main, ["sample", "new", "Sample1"])

    assert result.exit_code != 0
    assert "nrw init" in result.output


def test_doctor_runs_outside_a_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`doctor` exists to run when things are wrong, including "no project"."""
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(main, ["doctor"])

    assert result.exit_code == 0, result.output
    assert "nrw init" in result.output


def test_doctor_json_is_parseable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(main, ["doctor", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert {"name", "status", "detail"} <= set(payload[0])
