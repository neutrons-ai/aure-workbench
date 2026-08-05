#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["neutron-skills>=0.1.0"]
# ///
"""Install neutron-skills into this project's shared ``skills/`` folder.

`nrw init` already installed the reflectometry skills that ship with
nr-workbench. Use this script to pull *additional* domain skills from the
shared neutron-skills library -- SANS, diffraction, spectroscopy, or newer
reflectometry skills published since your nr-workbench version.

Each retrieved skill is copied verbatim (full ``SKILL.md`` plus any
``assets/``/``scripts/``/``references/``) to ``skills/<domain>/<name>/``, the
single source of truth read by both Claude Code and GitHub Copilot. A thin
dispatcher agent is generated for each in ``.claude/agents/`` and
``.github/agents/`` so it is immediately invocable.

Usage (run without installing anything):
    uv run scripts/install_skills.py --query "reduce EQSANS SANS data"
    uv run scripts/install_skills.py --list
    uv run scripts/install_skills.py --query "diffraction reduction" --dry-run
"""

import argparse
import shutil
import sys
from pathlib import Path, PurePosixPath

AGENT_DIRS = (Path(".claude") / "agents", Path(".github") / "agents")

# Claude Code understands this read-only guardrail; Copilot's agent schema
# flags the tool names as unknown, so it is omitted from the .github copy.
CLAUDE_TOOLS_LINE = "tools: Read, Grep, Glob, Bash"


def find_project_root(start: Path) -> Path:
    """Walk up from start looking for nrw.toml, a .git directory, or pyproject.toml."""
    current = start.resolve()
    for candidate in [current, *current.parents]:
        if (
            (candidate / "nrw.toml").exists()
            or (candidate / ".git").exists()
            or (candidate / "pyproject.toml").exists()
        ):
            return candidate
    raise RuntimeError(
        f"Could not find project root from {start}. "
        "Run this script from within the project directory."
    )


def dispatcher_markdown(skill, rel_skill_md: str, *, for_claude: bool) -> str:
    """Return a thin dispatcher agent that loads the installed skill.

    The description is emitted as a YAML block scalar so arbitrary skill
    descriptions (colons, quotes, multiple lines) are frontmatter-safe without
    needing a YAML serializer.
    """
    desc_lines = (skill.description or skill.name).strip().splitlines() or [skill.name]
    indented = "\n".join(f"  {line.rstrip()}" for line in desc_lines)
    tools = f"{CLAUDE_TOOLS_LINE}\n" if for_claude else ""
    return (
        "---\n"
        f"name: {skill.name}\n"
        "description: >\n"
        f"{indented}\n"
        f"{tools}"
        "---\n\n"
        f"# {skill.name}\n\n"
        f"You apply the `{skill.name}` domain skill from the neutron-skills library.\n"
        f"Read `{rel_skill_md}` and follow it exactly for the current task. The\n"
        "skill file is the authoritative standard; this dispatcher only loads it.\n"
    )


def install_skill(
    skill, project_root: Path, dry_run: bool, dispatchers: bool
) -> list[Path]:
    """Copy a skill into skills/<domain>/<name>/ and (optionally) emit dispatchers."""
    domain = skill.domain or ""
    rel_dir = (
        Path("skills", domain, skill.name) if domain else Path("skills", skill.name)
    )
    target_dir = project_root / rel_dir
    rel_skill_md = PurePosixPath(*rel_dir.parts, "SKILL.md").as_posix()

    written: list[Path] = [target_dir]
    if dry_run:
        print(f"  [dry-run] would copy skill -> {rel_skill_md}")
    else:
        target_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(skill.directory, target_dir, dirs_exist_ok=True)
        print(f"  installed skill -> {rel_skill_md}")

    if dispatchers:
        for agent_dir in AGENT_DIRS:
            for_claude = agent_dir.parts[0] == ".claude"
            stub = dispatcher_markdown(skill, rel_skill_md, for_claude=for_claude)
            stub_path = project_root / agent_dir / f"{skill.name}.md"
            rel_stub = stub_path.relative_to(project_root).as_posix()
            if dry_run:
                print(f"  [dry-run] would write dispatcher -> {rel_stub}")
            else:
                stub_path.parent.mkdir(parents=True, exist_ok=True)
                stub_path.write_text(stub, encoding="utf-8")
                print(f"  dispatcher -> {rel_stub}")
            written.append(stub_path)

    return written


def cmd_list() -> None:
    """Print all available skills with their descriptions."""
    try:
        from neutron_skills import SkillRegistry
    except ImportError:
        print(
            "Error: neutron-skills is not installed. Run: uv add neutron-skills",
            file=sys.stderr,
        )
        sys.exit(1)

    skills = sorted(SkillRegistry().all(), key=lambda s: s.name)
    if not skills:
        print("No skills found.")
        return

    width = max(len(s.name) for s in skills)
    print(f"{'NAME':<{width}}  DESCRIPTION")
    print("-" * (width + 2) + "-" * 40)
    for skill in skills:
        desc = " ".join((skill.description or "").split())
        print(f"{skill.name:<{width}}  {desc[:72] + '…' if len(desc) > 72 else desc}")


def cmd_install(
    query: str, top_k: int, project_root: Path, dry_run: bool, dispatchers: bool
) -> None:
    """Retrieve relevant skills and install them into skills/."""
    try:
        from neutron_skills import retrieve
    except ImportError:
        print(
            "Error: neutron-skills is not installed. Run: uv add neutron-skills",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f'Retrieving up to {top_k} skill(s) for query: "{query}"')
    skills = retrieve(query, top_k=top_k, method="auto")
    if not skills:
        print("No matching skills found.")
        return

    print(f"Installing {len(skills)} skill(s) into skills/:\n")
    for skill in skills:
        print(f"• {skill.name}: {skill.description}")
        install_skill(skill, project_root, dry_run, dispatchers)
        print()

    if dry_run:
        print("(dry-run: no files were written)")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Install neutron-skills into this project's shared skills/ folder.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--query", "-q", metavar="TEXT", help="Task description to find skills for"
    )
    parser.add_argument(
        "--top-k", type=int, default=3, metavar="N", help="Max skills (default: 3)"
    )
    parser.add_argument("--project-dir", type=Path, default=None, metavar="PATH")
    parser.add_argument(
        "--no-dispatchers", action="store_true", help="Skip dispatcher generation"
    )
    parser.add_argument(
        "--list", "-l", action="store_true", help="List available skills and exit"
    )
    parser.add_argument("--dry-run", action="store_true", help="Print without writing")

    args = parser.parse_args()

    if args.list:
        cmd_list()
        return
    if not args.query:
        parser.error("--query is required unless --list is used")

    cmd_install(
        query=args.query,
        top_k=args.top_k,
        project_root=args.project_dir or find_project_root(Path.cwd()),
        dry_run=args.dry_run,
        dispatchers=not args.no_dispatchers,
    )


if __name__ == "__main__":
    main()
