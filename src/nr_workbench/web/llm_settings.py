"""The language model the Experiment page's jobs use, for the Settings page.

New model and AuRE's quick fit ask a language model; this is where a person
sees which one, and chooses Claude through the Claude Code CLI. The choice is
saved in the project's ``.env`` (:mod:`nr_workbench.project.envfile`), never in
``nrw.toml``: that is committed and shared, and a language model is one
person's, on one machine.

Nothing here loads a ``.env`` into this process. Each job reads the files as
it starts, so a choice saved here reaches the next one; loaded here, a value
would pass to every job as the environment's and win over the file (see
:func:`nr_workbench.env.where_set` and
:func:`nr_workbench.web.jobs.child_environment`).

Its own class, beside :class:`~nr_workbench.web.settings.SettingsData`: it
writes another file, with other conflicts, and shares nothing with the
``nrw.toml`` settings but the page. Nothing here imports Flask;
:mod:`nr_workbench.web.experiment_api` maps the errors to status codes.
"""

from __future__ import annotations

import io
import json
import threading
from pathlib import Path
from typing import Any

from nr_workbench.bounded import Busy, TimedOut
from nr_workbench.web.experiment import RequestError, require_writable
from nr_workbench.web.settings import WriteFailedError

#: The variables the page sets, and removes, in the project's ``.env``.
LLM_VARIABLES = ("LLM_PROVIDER", "LLM_MODEL")

#: Seconds a check may take beyond AuRE's own limit on the call: the CLI's
#: start, and nrw's.
CHECK_MARGIN = 60.0

#: The longest a check is waited for, whatever ``LLM_TIMEOUT`` says: a request
#: is held open for it.
MAX_CHECK = 600.0

#: What a request that says nothing about the provider is taken to mean:
#: nothing -- it is refused rather than read as "remove it".
MISSING = object()


class LlmSettingsData:
    """The Settings page's Language model section, for one project.

    Args:
        root: Project root.
        writable: Whether writes are allowed at all.
        why_read_only: The reason, when not writable, for the page to show.
    """

    def __init__(self, root: Path, *, writable: bool, why_read_only: str = "") -> None:
        self.root = Path(root).resolve()
        self.writable = writable
        self.why_read_only = why_read_only
        #: Two saves from two tabs are made one after the other.
        self._saving = threading.Lock()
        #: One check at a time: each is a billed call.
        self._checking = threading.Lock()

    def llm(self) -> dict[str, Any]:
        """What the page's jobs use, where each part is set, and what can be chosen.

        Read from the files, never loaded: see the module's docstring. No key
        is shown, not even redacted -- reading this needs no link -- only
        whether one is set. Never fails on a ``.env`` it cannot use: that is
        said, as ``problem``, and saving is refused.

        Returns:
            What the project's ``.env`` chooses (``choice``: the Claude Code
            provider's id, ``outside`` when it sets no provider, ``other`` when
            it sets one by hand, ``None`` when it cannot be read), what applies
            with it and without it, which variables ``nrw serve``'s own
            environment sets -- and so wins with -- and whether the Claude Code
            provider can be used here.
        """
        from dotenv import dotenv_values

        from nr_workbench.aure_adapter import (
            CLAUDE_CODE,
            claude_cli,
            claude_code_supported,
            is_available,
        )
        from nr_workbench.env import ENVIRONMENT, shown_source, where_set
        from nr_workbench.project.envfile import (
            ENV_FILE,
            MAX_VALUE,
            EnvFileError,
            read_env_file,
        )

        problem = None
        try:
            current = read_env_file(self.root)
        except EnvFileError as exc:
            current, problem = None, str(exc)
        text = current.text if current is not None else None
        project = {
            name: value
            for name, value in dotenv_values(stream=io.StringIO(text or "")).items()
            if name in LLM_VARIABLES and value is not None
        }
        provider = project.get("LLM_PROVIDER")
        if current is None:
            choice = None
        elif provider is None:
            choice = "outside"
        elif provider == CLAUDE_CODE:
            choice = CLAUDE_CODE
        else:
            choice = "other"  # written by hand: shown, replaced only when asked
        effective = where_set(self.root)
        binary = effective.get("AURE_CLAUDE_BIN")
        return {
            "revision": current.revision if current is not None else None,
            "problem": problem,
            "file": ENV_FILE,
            "choice": choice,
            "project": project,
            "effective": self._described(effective),
            "outside": self._described(where_set(self.root, skip_project=True)),
            "environment": [
                name
                for name in LLM_VARIABLES
                if name in effective and effective[name].source == ENVIRONMENT
            ],
            "aure": is_available(),
            "claude_code": {
                "id": CLAUDE_CODE,
                "supported": claude_code_supported(),
                # On this server's PATH, which is the one its jobs get.
                "cli": claude_cli(binary.value if binary else None) is not None,
                # Where AURE_CLAUDE_BIN is set, when it is: which binary a job
                # runs is decided there. The path itself stays off this page.
                "binary_from": (
                    shown_source(binary.source, self.root) if binary else None
                ),
            },
            "model_max": MAX_VALUE,
            "writable": self.writable,
            "read_only_reason": self.why_read_only,
        }

    def save_llm(
        self, revision: Any, provider: Any = MISSING, model: Any = ""
    ) -> dict[str, Any]:
        """Choose the language model, in the project's ``.env``.

        Args:
            revision: The ``.env`` revision the page was shown.
            provider: The Claude Code provider's id -- Claude, through the
                Claude Code CLI -- or ``None`` for what is set outside the
                project: its lines are removed. Required.
            model: For Claude, the model the CLI is asked for; empty for the
                CLI's default. Written even when empty, so a model ``~/.aure``
                names for another provider is not passed to ``claude``.

        Returns:
            ``{"changes": [...], "llm": ...}``: what changed in ``.env``, and
            the section as it is now.

        Raises:
            WritesDisabledError: The server was started read-only.
            RequestError: The request is malformed.
            EnvValueError: The model is not one word.
            EnvFileConflict: ``.env`` changed since the page read it.
            EnvFileError: ``.env`` cannot be read or changed safely.
            WriteFailedError: It could not be written (a full disk, say).
        """
        from nr_workbench.aure_adapter import CLAUDE_CODE
        from nr_workbench.project.envfile import ENV_FILE, set_values

        require_writable(self.writable, self.why_read_only)
        if not isinstance(revision, str) or not revision:
            raise RequestError("revision is required: load the settings before saving")
        if provider is MISSING or provider not in (CLAUDE_CODE, None):
            raise RequestError(
                f"provider must be {CLAUDE_CODE!r}, or null for what is set "
                "outside this project"
                + ("" if provider is MISSING else f", not {provider!r}")
            )
        if not isinstance(model, str):
            raise RequestError(f"model must be text, not {model!r}")
        values: dict[str, str | None] = (
            {"LLM_PROVIDER": CLAUDE_CODE, "LLM_MODEL": model.strip()}
            if provider == CLAUDE_CODE
            else dict.fromkeys(LLM_VARIABLES)
        )
        with self._saving:
            try:
                changes = set_values(self.root, values, base_revision=revision)
            except OSError as exc:
                raise WriteFailedError(
                    f"{ENV_FILE} could not be written: {exc.strerror or exc}"
                ) from exc
        return {"changes": changes, "llm": self.llm()}

    def check_llm(self) -> dict[str, Any]:
        """Make one real call to the language model the page's jobs would use.

        ``nrw check-llm --endpoint``, run in the project as a job's step is, so
        it reads the files as a job does. Exactly one call: AuRE's retries are
        off for it. Behind the write gate, because the call is billed.

        Returns:
            ``{"probe": ...}``: its status (``ok``, ``warn``, ``error``), what
            it said, the model that answered, and how long it took.

        Raises:
            WritesDisabledError: The server was started read-only.
            Busy: A check is running already.
            TimedOut: No answer in time; the check was stopped, whole.
        """
        from nr_workbench.web import jobs

        require_writable(self.writable, self.why_read_only)
        if not self._checking.acquire(blocking=False):
            raise Busy("A check of the language model is running already.")
        deadline = self._deadline()
        try:
            completed = jobs.run_nrw(
                self.root,
                "check-llm",
                "--endpoint",
                "--json",
                timeout=deadline,
                env={"LLM_MAX_RETRIES": "0"},
            )
        except TimedOut as exc:
            raise TimedOut(
                f"The language model did not answer in {deadline:.0f} seconds; the "
                "check was stopped."
            ) from exc
        finally:
            self._checking.release()
        try:
            (probe,) = json.loads(completed.stdout)["probes"]
        except (ValueError, KeyError, TypeError):
            last = (completed.stderr or completed.stdout).strip().splitlines()
            probe = {
                "status": "error",
                "detail": "nrw check-llm gave no report"
                + (f": {last[-1]}" if last else "."),
            }
        return {"probe": probe}

    def _deadline(self) -> float:
        """How long a check is waited for: AuRE's limit on the call, and more."""
        from nr_workbench.aure_adapter import DEFAULT_LLM_TIMEOUT
        from nr_workbench.env import where_set

        setting = where_set(self.root).get("LLM_TIMEOUT")
        try:
            seconds = float(setting.value) if setting else DEFAULT_LLM_TIMEOUT
        except ValueError:
            seconds = DEFAULT_LLM_TIMEOUT  # AuRE refuses it; the check says why
        return min(max(seconds, 1.0) + CHECK_MARGIN, MAX_CHECK)

    def _described(self, settings: dict[str, Any]) -> dict[str, Any]:
        """A provider and model, and where each is set, for the page."""
        from nr_workbench.env import SECRET_VARS, shown_source

        described: dict[str, Any] = {}
        for key, name in (("provider", "LLM_PROVIDER"), ("model", "LLM_MODEL")):
            setting = settings.get(name)
            described[key] = setting.value if setting else None
            described[f"{key}_from"] = (
                shown_source(setting.source, self.root) if setting else None
            )
        # Set, and not empty: AuRE takes an empty key as none.
        described["key"] = any(
            settings[name].value for name in SECRET_VARS if name in settings
        )
        return described
