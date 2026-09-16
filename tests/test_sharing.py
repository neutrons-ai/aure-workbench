"""Sharing one analysis project between two people.

The failure these tests pin down was found in the field, not in review. A
project scaffolded by one analyst was pushed to GitHub and cloned by a second,
and it carried ``.nrw/bin/nrw`` and ``.claude/settings.local.json`` with it --
both of which hold the absolute path of the ``nrw`` executable on the first
analyst's machine.

The chain, each link of which is separately reasonable:

1. The repository was created on GitHub with a Python ``.gitignore`` in it.
2. `nrw init` therefore classified its own ``.gitignore`` as ``UNTRACKED`` and
   skipped it silently, reporting only "left alone 1 pre-existing file(s)".
3. The rules it withheld are the ones that ignore the two machine-local files.
4. Both were committed. Nothing complained, because both work perfectly on the
   machine that wrote them.
5. The second analyst's `nrw init` refreshed ``NRW_BIN`` but deliberately left
   the committed ``PATH`` alone, so their assistant sessions ran with the first
   analyst's virtualenv first on ``PATH`` -- resolving rather than failing,
   because the two accounts shared a filesystem.

So the tests here are mostly integration tests against a real git repository:
the bug lived in the gaps between the scaffold engine, the toolpath installer
and git, and none of the three was wrong on its own.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from nr_workbench.cli import main
from nr_workbench.commands.init_cmd import plan_project_files
from nr_workbench.project import ignore, toolpath
from nr_workbench.project.render import RenderContext
from nr_workbench.project.scaffold import Outcome, apply_scaffold, classify

#: A `.gitignore` of the kind GitHub offers when you create a repository. It is
#: the presence of *any* such file that triggered the bug, not its contents.
GITHUB_PYTHON_GITIGNORE = """\
# Byte-compiled / optimized / DLL files
__pycache__/
*.py[cod]

# Environments
.venv
venv/
"""


def git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run a git command in ``root``, failing the test if it errors."""
    return subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """An empty git repository with identity configured.

    Identity is set locally rather than relying on the developer's global
    config, so the tests pass in CI where no global config exists.
    """
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "test@example.invalid")
    git(root, "config", "user.name", "Test")
    return root


def scaffold(root: Path, context: RenderContext) -> None:
    """Install the project templates into ``root``, skills excluded for speed."""
    apply_scaffold(root, plan_project_files(context, include_skills=False))


def gitignore_block(context: RenderContext) -> str:
    """The managed block exactly as `nrw init` would write it."""
    planned = next(
        file
        for file in plan_project_files(context, include_skills=False)
        if file.relpath == ".gitignore"
    )
    return planned.content.decode("utf-8")


# --------------------------------------------------------------------------
# The managed block itself
# --------------------------------------------------------------------------


def test_wrap_puts_the_body_between_the_markers() -> None:
    block = ignore.wrap("*.tmp\n")

    assert block.startswith(ignore.BEGIN_MARKER + "\n")
    assert block.rstrip("\n").endswith(ignore.END_MARKER)
    assert "*.tmp\n" in block


def test_merge_into_an_empty_file_is_just_the_block() -> None:
    block = ignore.wrap("*.tmp\n")

    assert ignore.merge("", block) == block


def test_merge_appends_and_keeps_every_user_rule() -> None:
    block = ignore.wrap("*.tmp\n")

    merged = ignore.merge(GITHUB_PYTHON_GITIGNORE, block)

    assert merged.startswith(GITHUB_PYTHON_GITIGNORE)
    assert merged.endswith(block)


def test_merge_is_idempotent() -> None:
    """The second run must be a byte-for-byte no-op, or `init` diffs forever."""
    block = ignore.wrap("*.tmp\n")

    once = ignore.merge(GITHUB_PYTHON_GITIGNORE, block)
    twice = ignore.merge(once, block)

    assert twice == once


def test_merge_refreshes_a_stale_block_in_place() -> None:
    """An upgrade replaces the block without disturbing what surrounds it."""
    old = ignore.wrap("*.old\n")
    new = ignore.wrap("*.new\n")
    existing = f"# mine\nkeep-me/\n\n{old}# after the block\ntrailer/\n"

    merged = ignore.merge(existing, new)

    assert "*.old" not in merged
    assert "*.new" in merged
    assert "keep-me/" in merged
    assert "trailer/" in merged, "content below the block must survive"


def test_merge_does_not_accumulate_blank_lines() -> None:
    block = ignore.wrap("*.tmp\n")

    merged = ignore.merge("mine/", block)

    assert "\n\n\n" not in merged


def test_a_file_with_no_trailing_newline_still_merges_cleanly() -> None:
    block = ignore.wrap("*.tmp\n")

    merged = ignore.merge("mine/", block)

    assert "mine/\n" in merged
    assert merged.endswith(block)


def test_an_unterminated_block_is_refused_rather_than_guessed() -> None:
    """Half a merge conflict. Either repair would destroy something."""
    damaged = f"{ignore.BEGIN_MARKER}\n*.tmp\nmine-added-later/\n"

    with pytest.raises(ignore.DamagedBlockError):
        ignore.merge(damaged, ignore.wrap("*.tmp\n"))


def test_find_block_returns_none_when_there_is_no_block() -> None:
    assert ignore.find_block(GITHUB_PYTHON_GITIGNORE) is None


# --------------------------------------------------------------------------
# The scaffold engine's merge outcome
# --------------------------------------------------------------------------


def test_a_preexisting_gitignore_is_merged_not_skipped(
    tmp_path: Path, context: RenderContext
) -> None:
    """The regression. This reported UNTRACKED and wrote nothing at all."""
    (tmp_path / ".gitignore").write_text(GITHUB_PYTHON_GITIGNORE, encoding="utf-8")

    report = apply_scaffold(tmp_path, plan_project_files(context, include_skills=False))

    outcomes = {f.relpath: f.outcome for f in report.files}
    assert outcomes[".gitignore"] is Outcome.MERGE

    text = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert "__pycache__/" in text, "the user's own rules must survive"
    assert ".claude/settings.local.json" in text
    assert ".nrw/bin/" in text


def test_scaffolding_into_an_empty_directory_creates_the_gitignore(
    tmp_path: Path, context: RenderContext
) -> None:
    report = apply_scaffold(tmp_path, plan_project_files(context, include_skills=False))

    outcomes = {f.relpath: f.outcome for f in report.files}
    assert outcomes[".gitignore"] is Outcome.CREATE
    assert ignore.BEGIN_MARKER in (tmp_path / ".gitignore").read_text(encoding="utf-8")


def test_merging_twice_reports_unchanged(
    tmp_path: Path, context: RenderContext
) -> None:
    (tmp_path / ".gitignore").write_text(GITHUB_PYTHON_GITIGNORE, encoding="utf-8")
    planned = plan_project_files(context, include_skills=False)

    apply_scaffold(tmp_path, planned)
    report = apply_scaffold(tmp_path, planned)

    outcomes = {f.relpath: f.outcome for f in report.files}
    assert outcomes[".gitignore"] is Outcome.UNCHANGED


def test_the_block_is_upgraded_after_the_template_moves_on(
    tmp_path: Path, context: RenderContext
) -> None:
    """A lock entry is not what keeps a merged file current -- the markers are.

    Written as a lock *loss*, because that is the case a whole-file hash cannot
    survive: a fresh clone has the user's `.gitignore` and no history of what
    we installed into it.
    """
    from dataclasses import replace

    planned = plan_project_files(context, include_skills=False)
    apply_scaffold(tmp_path, planned)
    (tmp_path / ".nrw" / "scaffold.lock.json").unlink()

    moved_on = [
        replace(file, content=ignore.wrap("*.brand-new\n").encode("utf-8"))
        if file.relpath == ".gitignore"
        else file
        for file in planned
    ]
    report = apply_scaffold(tmp_path, moved_on)

    outcomes = {f.relpath: f.outcome for f in report.files}
    assert outcomes[".gitignore"] is Outcome.MERGE
    assert "*.brand-new" in (tmp_path / ".gitignore").read_text(encoding="utf-8")


def test_a_damaged_block_is_reported_and_the_file_left_alone(
    tmp_path: Path, context: RenderContext
) -> None:
    damaged = f"mine/\n{ignore.BEGIN_MARKER}\n*.tmp\n"
    (tmp_path / ".gitignore").write_text(damaged, encoding="utf-8")

    report = apply_scaffold(tmp_path, plan_project_files(context, include_skills=False))

    outcomes = {f.relpath: f.outcome for f in report.files}
    assert outcomes[".gitignore"] is Outcome.DRIFTED
    assert (tmp_path / ".gitignore").read_text(encoding="utf-8") == damaged
    assert (tmp_path / ".gitignore.nrw-new").is_file(), "hand the block over"


def test_classify_ignores_the_lock_for_a_merge_file(
    tmp_path: Path, context: RenderContext
) -> None:
    """A stale or absent lock entry must not make a merged file look drifted."""
    planned = next(
        file
        for file in plan_project_files(context, include_skills=False)
        if file.relpath == ".gitignore"
    )
    target = tmp_path / ".gitignore"
    target.write_text(
        ignore.merge(GITHUB_PYTHON_GITIGNORE, planned.content.decode("utf-8")),
        encoding="utf-8",
    )

    outcome = classify(planned, target, {"sha256_at_install": "0" * 64})

    assert outcome is Outcome.UNCHANGED


# --------------------------------------------------------------------------
# The lock is not rewritten by a no-op run
# --------------------------------------------------------------------------


def test_a_noop_init_leaves_the_lock_byte_identical(
    tmp_path: Path, context: RenderContext
) -> None:
    """The lock is tracked, so an unconditional write is a diff every run.

    For one person that is noise. For two sharing a project it is a merge
    conflict on a file neither of them edited, arriving whenever either runs
    `nrw init`.
    """
    planned = plan_project_files(context, include_skills=False)
    apply_scaffold(tmp_path, planned)
    lock = tmp_path / ".nrw" / "scaffold.lock.json"
    before = lock.read_bytes()

    apply_scaffold(tmp_path, planned)

    assert lock.read_bytes() == before


def test_the_lock_is_still_written_when_something_changes(
    tmp_path: Path, context: RenderContext
) -> None:
    planned = plan_project_files(context, include_skills=False)
    apply_scaffold(tmp_path, planned[:2])
    lock = tmp_path / ".nrw" / "scaffold.lock.json"
    before = json.loads(lock.read_text(encoding="utf-8"))["files"]

    apply_scaffold(tmp_path, planned)
    after = json.loads(lock.read_text(encoding="utf-8"))["files"]

    assert len(after) > len(before)


# --------------------------------------------------------------------------
# What init writes cannot be committed
# --------------------------------------------------------------------------


def test_init_leaves_the_machine_local_files_ignored(
    repo: Path, context: RenderContext
) -> None:
    """The point of the whole change, asked of git rather than of our own code."""
    (repo / ".gitignore").write_text(GITHUB_PYTHON_GITIGNORE, encoding="utf-8")
    scaffold(repo, context)
    toolpath.install(repo, executable=Path("/opt/nrw/.venv/bin/nrw"))

    assert toolpath.unignored_paths(repo, toolpath.MACHINE_LOCAL) == ()


def test_init_warns_when_a_machine_local_file_is_already_tracked(
    repo: Path, context: RenderContext
) -> None:
    """The state the field failure was in, and the one the ignore rule cannot fix.

    Git applies no ignore rule to a path already in the index, so merging the
    block into `.gitignore` -- which this run also does -- leaves the committed
    file exactly as committable as before. The advice has to say `git rm
    --cached`, because telling someone to add an ignore rule they already have
    is how a warning gets ignored.
    """
    scaffold(repo, context)
    toolpath.install(repo, executable=Path("/opt/nrw/.venv/bin/nrw"))
    git(repo, "add", "-f", toolpath.LOCAL_SETTINGS)
    git(repo, "commit", "-qm", "oops")

    result = CliRunner().invoke(main, ["init", str(repo)])

    assert result.exit_code == 0
    assert toolpath.LOCAL_SETTINGS in result.output
    assert "already tracked" in result.output
    assert "git rm --cached" in result.output


def test_init_is_quiet_when_nothing_is_committable(
    repo: Path, context: RenderContext
) -> None:
    """The warning must not fire on the healthy path, or it trains people to skip it."""
    (repo / ".gitignore").write_text(GITHUB_PYTHON_GITIGNORE, encoding="utf-8")

    result = CliRunner().invoke(main, ["init", str(repo)])

    assert result.exit_code == 0
    assert "would let them be committed" not in result.output


def test_a_path_from_another_machine_is_reported(
    repo: Path, context: RenderContext
) -> None:
    """The second analyst's symptom: NRW_BIN is refreshed, PATH is not."""
    scaffold(repo, context)
    settings = repo / toolpath.LOCAL_SETTINGS
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(
        json.dumps({"env": {"PATH": "/home/someone-else/.venv/bin:/usr/bin"}}),
        encoding="utf-8",
    )

    report = toolpath.install(repo, executable=Path("/opt/nrw/.venv/bin/nrw"))

    assert report.stale_path == "/home/someone-else/.venv/bin"


def test_a_path_that_already_leads_here_is_not_reported(
    repo: Path, context: RenderContext
) -> None:
    scaffold(repo, context)
    settings = repo / toolpath.LOCAL_SETTINGS
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(
        json.dumps({"env": {"PATH": "/opt/nrw/.venv/bin:/usr/bin"}}),
        encoding="utf-8",
    )

    report = toolpath.install(repo, executable=Path("/opt/nrw/.venv/bin/nrw"))

    assert report.stale_path == ""


def test_install_still_refreshes_nrw_bin_over_a_foreign_path(
    repo: Path, context: RenderContext
) -> None:
    """Reporting the stale PATH must not stop the additive fix from landing."""
    scaffold(repo, context)
    settings = repo / toolpath.LOCAL_SETTINGS
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(
        json.dumps({"env": {"PATH": "/home/someone-else/.venv/bin"}}),
        encoding="utf-8",
    )

    toolpath.install(repo, executable=Path("/opt/nrw/.venv/bin/nrw"))

    document = json.loads(settings.read_text(encoding="utf-8"))
    assert document["env"]["NRW_BIN"] == "/opt/nrw/.venv/bin/nrw"
    assert document["env"]["PATH"] == "/home/someone-else/.venv/bin", (
        "a PATH the user may have set deliberately is still not rewritten"
    )


def test_unignored_paths_is_silent_outside_a_repository(
    tmp_path: Path, context: RenderContext
) -> None:
    """Not a git repo means nothing can be committed, so there is no finding."""
    scaffold(tmp_path, context)
    toolpath.install(tmp_path, executable=Path("/opt/nrw/.venv/bin/nrw"))

    assert toolpath.unignored_paths(tmp_path, toolpath.MACHINE_LOCAL) == ()


# --------------------------------------------------------------------------
# `nrw check` enforces provenance rule 3
# --------------------------------------------------------------------------


def kinds(problems: list[dict[str, str]]) -> set[str]:
    """The distinct problem kinds in a check result."""
    return {problem["kind"] for problem in problems}


def check(root: Path) -> list[dict[str, str]]:
    """Run the committed-path check against ``root``."""
    from nr_workbench.commands.provenance_cmd import check_committed_paths
    from nr_workbench.project.layout import ProjectLayout

    return check_committed_paths(ProjectLayout(root=root))


def test_check_flags_a_tracked_machine_local_file(
    repo: Path, context: RenderContext
) -> None:
    scaffold(repo, context)
    toolpath.install(repo, executable=Path("/opt/nrw/.venv/bin/nrw"))
    git(repo, "add", "-f", toolpath.LOCAL_SETTINGS)

    problems = check(repo)

    assert "machine-local-tracked" in kinds(problems)
    assert any(toolpath.LOCAL_SETTINGS in p["detail"] for p in problems)


def test_check_passes_when_the_machine_local_files_are_ignored(
    repo: Path, context: RenderContext
) -> None:
    scaffold(repo, context)
    toolpath.install(repo, executable=Path("/opt/nrw/.venv/bin/nrw"))
    git(repo, "add", "-A")

    assert "machine-local-tracked" not in kinds(check(repo))


def test_check_flags_a_home_directory_in_a_tracked_file(
    repo: Path, context: RenderContext
) -> None:
    scaffold(repo, context)
    (repo / "analysis.py").write_text(
        'DATA = "/home/jenni/experiments/run1.txt"\n', encoding="utf-8"
    )
    git(repo, "add", "-A")

    problems = check(repo)

    assert "absolute-path" in kinds(problems)
    assert any("analysis.py" in p["detail"] for p in problems)


@pytest.mark.parametrize(
    "line",
    [
        'D = "/SNS/users/abc/data/run1.txt"\n',
        'D = "/Users/jenni/OneDrive/run1.txt"\n',
        'D = "/home/abc/run1.txt"\n',
    ],
)
def test_check_flags_every_home_directory_shape(
    repo: Path, context: RenderContext, line: str
) -> None:
    scaffold(repo, context)
    (repo / "analysis.py").write_text(line, encoding="utf-8")
    git(repo, "add", "-A")

    assert "absolute-path" in kinds(check(repo))


def test_check_allows_a_facility_path_that_is_true_for_everyone(
    repo: Path, context: RenderContext
) -> None:
    """`/SNS/REF_L/IPTS-1234/...` is where the data was. That is worth writing.

    Flagging it would make the check noisy enough to be ignored, which costs
    more than the one rule it would enforce.
    """
    scaffold(repo, context)
    (repo / "notes.md").write_text(
        "Reduced from /SNS/REF_L/IPTS-31745/nexus/REF_L_213456.nxs.h5\n",
        encoding="utf-8",
    )
    git(repo, "add", "-A")

    assert "absolute-path" not in kinds(check(repo))


def test_check_skips_files_nrw_installed(repo: Path, context: RenderContext) -> None:
    """One bundled skill quotes a home path deliberately, as a bad example.

    A path inside a file we installed is this package's bug, not the project's,
    and reporting it in every project would be a permanent false positive.
    """
    scaffold(repo, context)
    tracked_by_us = repo / "docs" / "ground_truths.md"
    tracked_by_us.write_text(
        'DATA = "/Users/jenni/OneDrive/experiments/..."  # someone else\n',
        encoding="utf-8",
    )
    git(repo, "add", "-A")

    assert "absolute-path" not in kinds(check(repo))


def test_check_flags_an_untracked_working_copy_never(
    repo: Path, context: RenderContext
) -> None:
    """Rule 3 is about *committed* files. An uncommitted scratch file is fine."""
    scaffold(repo, context)
    (repo / "scratch.py").write_text('D = "/home/abc/x.txt"\n', encoding="utf-8")

    assert "absolute-path" not in kinds(check(repo))


def test_check_is_silent_outside_a_git_repository(
    tmp_path: Path, context: RenderContext
) -> None:
    scaffold(tmp_path, context)
    (tmp_path / "analysis.py").write_text('D = "/home/abc/x.txt"\n', encoding="utf-8")

    assert check(tmp_path) == []


# --------------------------------------------------------------------------
# The whole story
# --------------------------------------------------------------------------


def test_a_second_analyst_cloning_the_repo_gets_their_own_paths(
    repo: Path, tmp_path: Path, context: RenderContext
) -> None:
    """End to end, against real git: the failure that started all of this.

    Analyst one scaffolds a repo that already had a GitHub `.gitignore`, sets
    up their toolpath, and commits everything they can. Analyst two clones and
    runs `nrw init`. Nothing of analyst one's machine may survive the clone.
    """
    (repo / ".gitignore").write_text(GITHUB_PYTHON_GITIGNORE, encoding="utf-8")
    scaffold(repo, context)
    toolpath.install(repo, executable=Path("/home/analyst-one/.venv/bin/nrw"))
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "setup nrw")

    clone = tmp_path / "clone"
    git(tmp_path, "clone", "-q", str(repo), str(clone))

    assert not (clone / ".nrw" / "bin" / "nrw").exists()
    assert not (clone / toolpath.LOCAL_SETTINGS).exists()
    tracked = subprocess.run(
        ["git", "grep", "-l", "analyst-one"],
        cwd=clone,
        capture_output=True,
        text=True,
        check=False,
    )
    assert tracked.stdout == "", f"analyst one leaked into {tracked.stdout!r}"

    # Analyst two sets themselves up.
    report = toolpath.install(clone, executable=Path("/home/analyst-two/.venv/bin/nrw"))

    assert report.stale_path == "", "no PATH came along with the clone"
    shim = clone / ".nrw" / "bin" / "nrw"
    assert "analyst-two" in shim.read_text(encoding="utf-8")
    assert toolpath.unignored_paths(clone, toolpath.MACHINE_LOCAL) == ()
    assert check(clone) == []


def test_force_on_a_damaged_block_keeps_the_user_rules_above_it(
    tmp_path: Path, context: RenderContext
) -> None:
    """`--force` must resolve the block, not replace the file.

    Everywhere else in the scaffold `--force` means "overwrite with the
    template". Taken literally on a merged file that would drop every rule the
    user wrote -- recoverable from `.nrw/backups/`, but only by someone who
    realises they need to look.
    """
    damaged = f"# mine\nkeep-me/\n{ignore.BEGIN_MARKER}\n*.half-written\n"
    (tmp_path / ".gitignore").write_text(damaged, encoding="utf-8")

    apply_scaffold(
        tmp_path, plan_project_files(context, include_skills=False), force=True
    )

    text = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert "keep-me/" in text, "rules above the marker are unambiguously the user's"
    assert ".claude/settings.local.json" in text
    assert text.count(ignore.BEGIN_MARKER) == 1, "no duplicate marker"
    assert ignore.find_block(text) is not None, "the block is well-formed again"


def test_force_merge_on_a_healthy_file_is_an_ordinary_merge() -> None:
    block = ignore.wrap("*.tmp\n")

    assert ignore.force_merge(GITHUB_PYTHON_GITIGNORE, block) == ignore.merge(
        GITHUB_PYTHON_GITIGNORE, block
    )


# --------------------------------------------------------------------------
# The git helpers, when git has no answer
# --------------------------------------------------------------------------

# Every branch below is reached with a real directory rather than a mock,
# because "git cannot answer" is a state a project is genuinely in -- not a
# repository yet, or `nrw` running somewhere git is not installed -- and the
# contract is that none of it is reported as a finding.


def test_helpers_return_nothing_outside_a_repository(tmp_path: Path) -> None:
    from nr_workbench.project import vcs

    assert vcs.is_repository(tmp_path) is False
    assert vcs.tracked_files(tmp_path) == ()
    assert vcs.grep(tmp_path, "anything") == ()
    assert vcs.ignored(tmp_path, ["a.txt"]) == frozenset()


def test_ignored_short_circuits_on_an_empty_list(repo: Path) -> None:
    from nr_workbench.project import vcs

    assert vcs.ignored(repo, []) == frozenset()


def test_grep_returns_line_numbers_and_text(repo: Path) -> None:
    from nr_workbench.project import vcs

    (repo / "a.txt").write_text("one\nneedle: a:b:c\n", encoding="utf-8")
    git(repo, "add", "-A")

    matches = vcs.grep(repo, "needle")

    assert matches == (("a.txt", 2, "needle: a:b:c"),), (
        "the text may itself contain colons, so only the first two split"
    )


def test_wrap_tolerates_a_body_without_a_trailing_newline() -> None:
    block = ignore.wrap("*.tmp")

    assert block == ignore.wrap("*.tmp\n")
