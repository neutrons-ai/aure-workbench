"""Install bundled skills into a project's ``skills/`` directory.

Copies a skill's whole directory to ``skills/<domain>/<name>/`` and generates a
thin dispatcher agent in the agents directory of every harness the project is
scaffolded for.

Two conventions are load-bearing and deliberately preserved:

* **Skills live at the repo root, not under ``.claude/skills/``.** Copilot
  cannot read the latter, so a repo-root folder is the only tool-neutral home.
  OpenCode *can* read it, which does not change the answer: the point is one
  location every assistant reaches, not the union of their private ones.
  None of them auto-discovers it, which is why dispatchers exist.
* **Thin dispatcher, fat skill.** The stub is a pointer; every substantive
  instruction lives in the ``SKILL.md``. To change a standard, edit the skill.

The dispatchers are byte-identical across harnesses apart from the frontmatter
each one's schema accepts --- see :attr:`Harness.dispatcher_frontmatter`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

import yaml

from nr_workbench.harness import DEFAULT_HARNESSES, Harness, resolve

#: Frontmatter delimiter.
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

#: Skill directory names must be lowercase-hyphen, matching frontmatter `name`.
_NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")

#: Never installed into a user's project. The v2 anatomy lets a skill ship
#: executable helpers under `scripts/`, and because skills live inside the
#: package, pip byte-compiles those on install -- so a stale `.pyc` would
#: otherwise be copied into every project alongside the real file.
_EXCLUDED_DIRS = frozenset({"__pycache__"})
_EXCLUDED_SUFFIXES = frozenset({".pyc", ".pyo"})


class SkillError(Exception):
    """Raised when a skill cannot be read or fails its structural contract."""


@dataclass(frozen=True)
class Skill:
    """A skill on disk, ready to be installed into a project.

    Attributes:
        name: The skill's canonical name; equals its directory name.
        description: The frontmatter description, used for dispatcher matching.
        domain: Subdirectory under ``skills/``, e.g. ``"reflectometry"``.
        directory: Absolute path to the skill's source directory.
    """

    name: str
    description: str
    domain: str
    directory: Path

    @property
    def relative_dir(self) -> Path:
        """Where this skill installs to, relative to the project root."""
        return (
            Path("skills", self.domain, self.name)
            if self.domain
            else Path("skills", self.name)
        )

    @property
    def relative_skill_md(self) -> str:
        """POSIX path of the installed ``SKILL.md``, relative to the root."""
        return (self.relative_dir / "SKILL.md").as_posix()


def bundled_skills_root() -> Path:
    """Return the directory holding the skills shipped inside this package.

    Returns:
        Absolute path to ``nr_workbench/skills``.

    Raises:
        SkillError: If the directory is missing from the installed package,
            which means the wheel was built without its package-data.
    """
    root = Path(str(resources.files("nr_workbench"))) / "skills"
    if not root.is_dir():
        raise SkillError(
            f"No bundled skills directory at {root}. If this is an installed "
            "nr-workbench, the wheel was built without its package-data -- see "
            "[tool.setuptools.package-data] in pyproject.toml."
        )
    return root


def parse_skill(skill_md: Path, *, domain: str) -> Skill:
    """Parse one ``SKILL.md`` into a :class:`Skill`.

    Args:
        skill_md: Path to the skill's ``SKILL.md``.
        domain: The domain subdirectory the skill belongs to.

    Returns:
        The parsed skill.

    Raises:
        SkillError: If frontmatter is missing or malformed, if ``name`` or
            ``description`` is absent, or if ``name`` does not match the
            directory name.
    """
    text = skill_md.read_text(encoding="utf-8")
    match = _FRONTMATTER_RE.match(text)
    if match is None:
        raise SkillError(f"{skill_md} has no YAML frontmatter block")

    try:
        meta = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as exc:
        raise SkillError(f"{skill_md} has malformed frontmatter: {exc}") from exc
    if not isinstance(meta, dict):
        raise SkillError(f"{skill_md} frontmatter is not a mapping")

    name = str(meta.get("name", "")).strip()
    description = str(meta.get("description", "")).strip()
    if not name:
        raise SkillError(f"{skill_md} frontmatter is missing `name`")
    if not description:
        raise SkillError(f"{skill_md} frontmatter is missing `description`")
    if not _NAME_RE.match(name):
        raise SkillError(f"{skill_md}: name '{name}' must be lowercase-hyphen")
    if name != skill_md.parent.name:
        raise SkillError(
            f"{skill_md}: frontmatter name '{name}' does not match its "
            f"directory '{skill_md.parent.name}'"
        )

    return Skill(
        name=name, description=description, domain=domain, directory=skill_md.parent
    )


def discover_skills(root: Path | None = None) -> list[Skill]:
    """Find every skill under a skills root, sorted by name.

    Expects the layout ``<root>/<domain>/<name>/SKILL.md``.

    Args:
        root: The skills root. Defaults to the bundled one.

    Returns:
        Parsed skills, sorted by name.

    Raises:
        SkillError: If any discovered skill fails its structural contract. A
            malformed skill is a packaging bug, not something to skip past.
    """
    root = bundled_skills_root() if root is None else Path(root)
    skills: list[Skill] = []
    for skill_md in sorted(root.glob("*/*/SKILL.md")):
        skills.append(parse_skill(skill_md, domain=skill_md.parent.parent.name))
    return sorted(skills, key=lambda s: s.name)


def dispatcher_markdown(skill: Skill, harness: Harness) -> str:
    """Render the thin dispatcher agent that loads an installed skill.

    The description is emitted as a YAML block scalar so that arbitrary skill
    descriptions -- colons, quotes, several lines -- stay frontmatter-safe
    without needing a YAML serializer.

    Args:
        skill: The skill the dispatcher points at.
        harness: The harness whose agents directory this copy is for. Only its
            :attr:`~nr_workbench.harness.Harness.dispatcher_frontmatter` varies
            the output; the body is the same everywhere.

    Returns:
        The dispatcher file's contents.
    """
    lines = (skill.description or skill.name).strip().splitlines() or [skill.name]
    indented = "\n".join(f"  {line.rstrip()}" for line in lines)
    extra = "".join(f"{line}\n" for line in harness.dispatcher_frontmatter)
    return (
        "---\n"
        f"name: {skill.name}\n"
        "description: >\n"
        f"{indented}\n"
        f"{extra}"
        "---\n\n"
        f"# {skill.name}\n\n"
        f"You apply the `{skill.name}` skill from the nr-workbench library.\n"
        f"Read `{skill.relative_skill_md}` and follow it exactly for the current\n"
        "task. The skill file is the authoritative standard; this dispatcher only\n"
        "loads it. To change the standard, edit the skill -- never this file.\n"
    )


def plan_skill_files(
    skill: Skill, *, harnesses: Iterable[str] = DEFAULT_HARNESSES
) -> list[tuple[str, bytes]]:
    """Enumerate every file installing one skill would write.

    Returns the skill's own files plus one dispatcher stub per harness, so the
    caller can feed them through the same idempotent scaffold engine as
    everything else rather than copying blindly over a scientist's edits.

    Args:
        skill: The skill to plan.
        harnesses: Names of the harnesses to write dispatchers for. Harnesses
            that do not read subagent files contribute none.

    Returns:
        Pairs of (POSIX relpath from the project root, file contents).

    Raises:
        HarnessError: If a name is not a known harness.
    """
    planned: list[tuple[str, bytes]] = []

    for source in sorted(skill.directory.rglob("*")):
        if not source.is_file():
            continue
        if _EXCLUDED_DIRS & set(source.parts) or source.suffix in _EXCLUDED_SUFFIXES:
            continue
        relative = source.relative_to(skill.directory)
        planned.append(
            ((skill.relative_dir / relative).as_posix(), source.read_bytes())
        )

    for harness in resolve(harnesses):
        if harness.agents_dir is None:
            continue
        planned.append(
            (
                f"{harness.agents_dir}/{skill.name}.md",
                dispatcher_markdown(skill, harness).encode("utf-8"),
            )
        )

    return planned
