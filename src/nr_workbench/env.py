"""Loading configuration from ``.env``, the way AuRE does.

Settings -- currently the language-model endpoint -- come from environment
variables. Those can be supplied four ways, in decreasing precedence:

1. the real shell environment, which is never overridden;
2. a project-local ``.env``, found by walking up from the working directory;
3. a per-user ``~/.nrw``, in the same ``KEY=value`` format;
4. a per-user ``~/.aure``, so a machine already configured for AuRE works here
   without duplicating the key.

The fourth is a convenience, and it is reported by ``nrw doctor`` rather than
being silent: configuration arriving from a file you did not think you were
using is exactly the kind of thing that makes "it works on my machine" hard to
debug.

**Loaded lazily.** ``python-dotenv`` costs about 40 ms to import, and
``nrw --help`` must stay instant, so nothing here runs until something actually
needs a setting.
"""

from __future__ import annotations

import os
import re
import stat
from collections.abc import Collection
from dataclasses import dataclass, field
from pathlib import Path

#: Per-user defaults, same format as ``.env``.
USER_ENV_PATH = Path.home() / ".nrw"

#: AuRE's per-user file, read last so an existing AuRE setup carries over.
AURE_ENV_PATH = Path.home() / ".aure"

#: Settings this package reads. Used by `nrw doctor` to report what is set,
#: and to redact the ones that are secret.
KNOWN_VARS = (
    "LLM_PROVIDER",
    "LLM_MODEL",
    "LLM_BASE_URL",
    "LLM_API_KEY",
    "LLM_TEMPERATURE",
    "LLM_TIMEOUT",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "ALCF_ACCESS_TOKEN",
    "ALCF_CLUSTER",
    # Only read by AuRE's claude_code provider, and only when the CLI is not
    # on PATH. Reported because an endpoint that resolves to the wrong binary
    # looks exactly like one that is not configured.
    "AURE_CLAUDE_BIN",
    # The ISAAC Portal `nrw isaac push` publishes to, and the key it does so
    # with -- read by nr-isaac-format, which nrw passes its environment.
    "ISAAC_URL",
    "ISAAC_KEY",
    # The Tiled server an experiment's data and catalog may be kept on, and a
    # key for it where one was issued. Read from the person's own settings
    # only; see nr_workbench.tiled.address.
    "TILED_URL",
    "TILED_API_KEY",
)

#: Variables whose value must never be printed.
#: The language model's keys: one of these set is "a key is set" for it.
LLM_KEYS = ("LLM_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "ALCF_ACCESS_TOKEN")

SECRET_VARS = frozenset({*LLM_KEYS, "ISAAC_KEY", "TILED_API_KEY"})

#: The settings only the person makes, by the service they reach: each read
#: from the shell, ``~/.nrw`` or ``~/.aure``, and refused when anything but
#: the person could have chosen it (:func:`person_setting`). A server named
#: here is where the person's key or login is sent.
PERSON_ONLY: dict[str, tuple[str, ...]] = {
    "ISAAC": ("ISAAC_URL", "ISAAC_KEY"),
    "Tiled": ("TILED_URL", "TILED_API_KEY"),
}

#: A project's files that programs other than nrw load into the environment:
#: python-dotenv's and node's, Flask's, and direnv's.
_PROJECT_ENV_FILES = (".env", ".env.local", ".flaskenv", ".envrc")

#: Set once loading has run, so repeated calls are free.
_loaded = False

#: The variables :func:`load_env` put in ``os.environ`` from a file, so that
#: :func:`where_set` can still tell them from the shell's afterwards.
_set_by_files: set[str] = set()

#: How :func:`where_set` names the environment a process started with.
ENVIRONMENT = "the environment"


@dataclass
class EnvSources:
    """Where configuration was read from.

    Attributes:
        files: Files that were loaded, in the order they were applied.
        missing: Files that were looked for and not found.
    """

    files: list[Path] = field(default_factory=list)
    missing: list[Path] = field(default_factory=list)


def load_env(start: Path | None = None, *, force: bool = False) -> EnvSources:
    """Populate ``os.environ`` from the project and per-user files.

    Nothing already in the real environment is overridden, so an explicit
    ``LLM_MODEL=... nrw ...`` always wins.

    Args:
        start: Directory to search upward from. Defaults to the working
            directory.
        force: Re-read even if loading has already happened this process.

    Returns:
        Which files were used.
    """
    global _loaded
    sources = EnvSources()
    if _loaded and not force:
        return sources

    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - python-dotenv is a declared dep
        _loaded = True
        return sources

    before = set(os.environ)
    for path, _ in _candidates(start):
        if path.is_file():
            # override=False everywhere: the first file to set a variable wins,
            # and the shell wins over all of them.
            load_dotenv(path, override=False)
            sources.files.append(path)
        else:
            sources.missing.append(path)
    _set_by_files.update(set(os.environ) - before)

    _loaded = True
    return sources


def loaded_from_files() -> frozenset[str]:
    """The variables :func:`load_env` put in ``os.environ`` from a file."""
    return frozenset(_set_by_files)


@dataclass(frozen=True)
class Setting:
    """One variable, as an ``nrw`` command started now would read it.

    Attributes:
        value: Its value; ``""`` when a file sets it empty.
        source: :data:`ENVIRONMENT`, or the file it comes from.
    """

    value: str
    source: str | Path


def where_set(
    start: Path | None = None,
    *,
    skip_project: bool = False,
    skip_environment: bool = False,
) -> dict[str, Setting]:
    """Each known variable an ``nrw`` command started in *start* would read.

    :func:`load_env` replayed, never run: the web server asks this, and a value
    loaded into its own environment would pass to every command it starts as
    though the shell had set it -- winning over a project ``.env`` written
    afterwards. The replay is python-dotenv's own: the same files in the same
    order, each value interpolated against the environment as it would stand by
    then -- a ``${VAR}`` in ``~/.aure`` sees what ``.env`` set -- and a file
    that sets a variable already set changes nothing.

    Args:
        start: The directory the command would run in; the working directory
            by default.
        skip_project: Leave out the project's ``.env``: what applies when it
            sets nothing.
        skip_environment: Leave out the environment too: what the files alone
            say, as a command started from a clean shell would read them.

    Returns:
        Variable name to its value and source, for the variables set anywhere.
    """
    # What a child starts with: see nr_workbench.web.jobs.child_environment.
    environ = (
        {}
        if skip_environment
        else {k: v for k, v in os.environ.items() if k not in _set_by_files}
    )
    sources: dict[str, str | Path] = dict.fromkeys(environ, ENVIRONMENT)
    for path, is_project in _candidates(start):
        # Checked before each file, as load_dotenv does: one may set it.
        if (skip_project and is_project) or _dotenv_disabled(environ):
            continue
        # A regular file only: the server never waits on a FIFO put there.
        if not path.is_file():
            continue
        try:
            values = _resolved(path, environ)
        except (OSError, UnicodeError):
            continue  # a job fails on it; the Settings page says why
        for name, value in values.items():
            if name not in environ and value is not None:
                environ[name] = value
                sources[name] = path
    return {
        name: Setting(environ[name], sources[name])
        for name in KNOWN_VARS
        if name in environ
    }


def person_setting(
    name: str, start: Path | None = None
) -> tuple[Setting | None, str | None]:
    """A setting only the person makes, as nrw may use it; or why it may not be.

    From the shell, ``~/.nrw`` or ``~/.aure``, never the project's ``.env``
    (:func:`where_set` with ``skip_project``). That cannot leave out what is
    already in the environment, so the setting is refused, with the reason:

    * while the project's own files set any setting of its service, or
      ``HOME``. A program that loads those files (direnv, pipenv, an editor
      running a program) puts them in the environment, where they look like
      the person's own (:func:`set_in_project`);
    * when the file it comes from could be written by someone else, as ssh
      refuses its own configuration;
    * for an agent's command (``NRW_AGENT``), when it is not what the
      person's files say. An agent sets the environment of every command it
      runs, and may have been told to by something it read.

    Args:
        name: A setting in :data:`PERSON_ONLY`.
        start: The project.

    Returns:
        The setting, ``None`` when it is not set or set empty; and why it may
        not be used, ``None`` when it may.
    """
    from nr_workbench.agent.guard import AGENT_ENV

    service = next(s for s, names in PERSON_ONLY.items() if name in names)
    found = where_set(start, skip_project=True).get(name)
    setting = found if found is not None and found.value.strip() else None
    problem = _set_by_project(start, service)
    if problem is None and setting is not None and isinstance(setting.source, Path):
        problem = _writable_by_others(setting.source, name, service)
    if problem is None and os.environ.get(AGENT_ENV):
        own = where_set(start, skip_project=True, skip_environment=True).get(name)
        mine = own.value.strip() if own is not None else ""
        if (setting.value.strip() if setting is not None else "") != mine:
            # Never the values: one may be a key.
            problem = (
                f"{name} for this command is not what ~/.nrw or ~/.aure says, "
                "and an agent's command uses only those. Say in ESCALATIONS.md "
                f"what you needed and why, and stop: a person sets {name} in "
                "~/.nrw."
            )
    return setting, problem


def set_in_project(start: Path | None, names: Collection[str]) -> dict[Path, list[str]]:
    """Which of *names* the project's own files set: its ``.env`` and its neighbours.

    The files programs other than nrw load into the environment
    (:data:`_PROJECT_ENV_FILES`), read as the loosest of them would. A line
    sets a name in any shape a loader takes: after ``export``, quoted, as
    ``KEY: value``, behind a byte-order mark, or inside a quoted value. The
    rest of the file is not looked at, so a byte that is not UTF-8 changes
    nothing. Only the person sets these, so a false alarm costs a line moved
    to ``~/.nrw``, and a miss could cost their login.

    Args:
        start: The project, or a directory in it.
        names: The settings to look for.

    Returns:
        Each file that sets one, with the ones it sets, in the order *names*
        has them.
    """
    directory = next(
        (p.parent for p, is_project in _candidates(start) if is_project), None
    )
    if directory is None or not names:
        return {}
    pattern = re.compile(
        rb"(?m)^[ \t\xef\xbb\xbf]*(?:export[ \t]+)?[\"']?("
        + b"|".join(re.escape(name.encode()) for name in names)
        + rb")[\"']?[ \t]*[=:]"
    )
    found: dict[Path, list[str]] = {}
    for file_name in _PROJECT_ENV_FILES:
        data = _regular_bytes(directory / file_name)
        if data is None:
            continue
        given = {match.group(1).decode() for match in pattern.finditer(data)}
        if hits := [name for name in names if name in given]:
            found[directory / file_name] = hits
    return found


def _set_by_project(start: Path | None, service: str) -> str | None:
    """Why no setting of *service* is used while the project's files set one."""
    found = set_in_project(start, (*PERSON_ONLY[service], "HOME"))
    if not found:
        return None
    path, names = next(iter(found.items()))
    return (
        f"This project's {shown_source(path, start)} sets {' and '.join(names)}. "
        "Any program that loads that file puts them in the environment, where "
        f"they would look like your own settings and choose where your {service} "
        f"login or key is sent; so nrw uses no {service} setting for this project "
        f"until they are removed from it. {service}'s own belong in ~/.nrw."
    )


def _writable_by_others(path: Path, name: str, service: str) -> str | None:
    """Why *name* must not be taken from *path*, or ``None``.

    As ssh treats its own configuration: the file, a link to it, and every
    directory above it must be the person's or root's, and writable by no one
    else -- a sticky directory, as /tmp is, excepted -- or whoever could write
    one could choose where the person's login is sent.
    """
    if not hasattr(os, "getuid"):
        return None  # Windows: no owner and mode to compare
    uid = os.getuid()
    where = shown_source(path)
    why = (
        f"so nrw takes no {name} from it: whoever can write it could choose "
        f"where your {service} login is sent."
    )
    try:
        link = path.lstat()
        real = path.resolve(strict=True)
        info = real.stat()
        above = [(directory, directory.stat()) for directory in real.parents]
    except (OSError, RuntimeError) as exc:
        return f"{where} cannot be checked ({exc}), {why}"
    if stat.S_ISLNK(link.st_mode) and link.st_uid not in (uid, 0):
        return f"{where} is a link another user made, {why}"
    if info.st_uid != uid:
        return f"{where} belongs to another user, {why}"
    if info.st_mode & 0o022:
        return (
            f"{where} can be written by others (mode {info.st_mode & 0o777:03o}), "
            f"{why} Run: chmod go-w {where}"
        )
    for directory, held in above:
        if held.st_uid not in (uid, 0):
            return f"{directory}, which holds {where}, belongs to another user, {why}"
        if held.st_mode & 0o022 and not held.st_mode & stat.S_ISVTX:
            return (
                f"{directory}, which holds {where}, can be written by others "
                f"(mode {held.st_mode & 0o777:03o}), {why}"
            )
    return None


def _regular_bytes(path: Path) -> bytes | None:
    """A regular file's bytes, or ``None``: never waits on a FIFO put in its place."""
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    except OSError:
        return None
    with os.fdopen(descriptor, "rb") as stream:
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                return None
            return stream.read()
        except OSError:
            return None


def _resolved(path: Path, environ: dict[str, str]) -> dict[str, str | None]:
    """A file's values as ``load_dotenv(override=False)`` would have them.

    python-dotenv's ``resolve_variables``, with *environ* standing in for
    ``os.environ``: each value's ``${VAR}`` is looked up in the file's own
    earlier values, and then in the environment, which wins.
    """
    from dotenv.parser import parse_stream
    from dotenv.variables import parse_variables

    values: dict[str, str | None] = {}
    with path.open(encoding="utf-8") as stream:
        for binding in parse_stream(stream):
            if binding.key is None:
                continue
            if binding.value is None:
                values[binding.key] = None
                continue
            env = {**values, **environ}
            values[binding.key] = "".join(
                atom.resolve(env) for atom in parse_variables(binding.value)
            )
    return values


def _dotenv_disabled(environ: dict[str, str]) -> bool:
    """python-dotenv's own switch, which makes ``load_dotenv`` load nothing."""
    value = environ.get("PYTHON_DOTENV_DISABLED", "").casefold()
    return value in {"1", "true", "t", "yes", "y"}


def shown_source(source: str | Path, root: Path | None = None) -> str:
    """A setting's source as a person reads it: ``~/.aure``, not a home path.

    Args:
        source: As :class:`Setting` has it.
        root: The project, for its own ``.env``.

    Returns:
        The environment, a path under the project, or one under ``~``.
    """
    if not isinstance(source, Path):
        return source
    for base, shown in ((root, ""), (Path.home(), "~/")):
        if base is not None:
            try:
                return shown + source.relative_to(base).as_posix()
            except ValueError:
                continue
    return str(source)


def _candidates(start: Path | None) -> list[tuple[Path, bool]]:
    """Files to try, in precedence order, each with whether it is the project's.

    The one list :func:`load_env` loads and :func:`where_set` replays.
    """
    here = (Path(start) if start else Path.cwd()).resolve()

    found: list[tuple[Path, bool]] = []
    # Walk up looking for a project-local .env. Stop at the project root if we
    # reach one -- past that, a .env belongs to something else.
    for directory in [here, *here.parents]:
        candidate = directory / ".env"
        if candidate.is_file():
            found.append((candidate, True))
            break
        if (directory / "nrw.toml").is_file():
            found.append((candidate, True))  # recorded as missing by the caller
            break

    found.append((USER_ENV_PATH, False))
    found.append((AURE_ENV_PATH, False))
    return found


def describe() -> dict[str, str]:
    """Report the settings that are set, with secrets redacted.

    Returns:
        Variable name to value, with anything secret shown only as its length
        and last four characters -- enough to tell two keys apart, not enough
        to use one.
    """
    report: dict[str, str] = {}
    for name in KNOWN_VARS:
        value = os.environ.get(name)
        if not value:
            continue
        report[name] = redact(value) if name in SECRET_VARS else value
    return report


def redact(value: str) -> str:
    """Render a secret as a shape rather than a value.

    Public because the harness-provider variables reported by ``nrw check-llm``
    are a different set from :data:`KNOWN_VARS` but need the same treatment,
    and two redaction functions is one too many.

    Args:
        value: The secret.

    Returns:
        Its length and last four characters -- enough to tell two keys apart,
        not enough to use one.
    """
    if len(value) <= 4:
        return "set (short)"
    return f"set ({len(value)} chars, ...{value[-4:]})"
