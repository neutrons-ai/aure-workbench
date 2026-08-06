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
)

#: Variables whose value must never be printed.
SECRET_VARS = frozenset(
    {"LLM_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "ALCF_ACCESS_TOKEN"}
)

#: Set once loading has run, so repeated calls are free.
_loaded = False


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

    for path in _candidates(start):
        if path.is_file():
            # override=False everywhere: the first file to set a variable wins,
            # and the shell wins over all of them.
            load_dotenv(path, override=False)
            sources.files.append(path)
        else:
            sources.missing.append(path)

    _loaded = True
    return sources


def _candidates(start: Path | None) -> list[Path]:
    """Files to try, in precedence order."""
    here = (Path(start) if start else Path.cwd()).resolve()

    found: list[Path] = []
    # Walk up looking for a project-local .env. Stop at the project root if we
    # reach one -- past that, a .env belongs to something else.
    for directory in [here, *here.parents]:
        candidate = directory / ".env"
        if candidate.is_file():
            found.append(candidate)
            break
        if (directory / "nrw.toml").is_file():
            found.append(candidate)  # recorded as missing by the caller
            break

    found.append(USER_ENV_PATH)
    found.append(AURE_ENV_PATH)
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
        report[name] = _redact(value) if name in SECRET_VARS else value
    return report


def _redact(value: str) -> str:
    """Render a secret as a shape rather than a value."""
    if len(value) <= 4:
        return "set (short)"
    return f"set ({len(value)} chars, ...{value[-4:]})"
