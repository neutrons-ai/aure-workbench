"""Which coding harnesses a project is set up for, and what each one needs.

A "harness" here is a tool-using coding assistant --- Claude Code, GitHub
Copilot, OpenCode. Each wants its instructions, its subagent stubs and its
limits in a different place, and before this package existed those paths were
spelled out literally in six modules. Adding a third assistant meant finding
every one of them.

The registry is the single place that knows. Everything else asks it.

**Not every harness does every job.** Copilot has no configuration file, no
pre-tool hook, and cannot be driven unattended; OpenCode has all three but
spells them differently. The optional fields on :class:`Harness` are what makes
that honest --- a harness that cannot enforce a limit says so, rather than
having a plausible-looking path that nothing reads.

**Selection is a project decision**, recorded in ``nrw.toml`` and overridable
with ``nrw init --harness``. A project that says nothing gets
:data:`DEFAULT_HARNESSES`, which is what every project got before this existed.
"""

from __future__ import annotations

from nr_workbench.harness.registry import (
    DEFAULT_HARNESSES,
    HARNESSES,
    Harness,
    HarnessError,
    agent_dirs,
    known_names,
    resolve,
    session_harnesses,
)

__all__ = [
    "DEFAULT_HARNESSES",
    "HARNESSES",
    "Harness",
    "HarnessError",
    "agent_dirs",
    "known_names",
    "resolve",
    "session_harnesses",
]
