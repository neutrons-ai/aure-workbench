"""Everything the Settings page knows, with no Flask anywhere.

The Settings page sets what the Experiment page watches: the IPTS, where the
data is, and how new runs are noticed, all saved into ``nrw.toml`` through
:mod:`nr_workbench.project.settings`. It is its own class, beside
:class:`~nr_workbench.web.experiment.ExperimentData` rather than inside it,
and speaks to it through three callables -- how many runs the catalog holds,
which folder the server reads now, and "the file changed" -- so neither needs
the other's internals.

It also sets the language model the page's jobs use -- New model and AuRE's
quick fit -- in the project's ``.env`` (:mod:`nr_workbench.project.envfile`),
never in ``nrw.toml``: that is committed and shared, and a language model is
one person's, on one machine. Nothing here loads a ``.env`` into this process:
each job reads the files as it starts, so a choice saved here reaches the next
job (see :func:`nr_workbench.env.where_set`).
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from nr_workbench.bounded import Bounded, Busy, TimedOut
from nr_workbench.experiment.sources import SOURCE_TIMEOUT
from nr_workbench.experiment.views import settings_view
from nr_workbench.web.experiment import RequestError, require_writable

#: The provider the page can set: AuRE's Claude Code CLI provider, which needs
#: no key -- whatever ``claude`` is logged in as answers.
CLAUDE_CODE = "claude_code"

#: The variables the page sets, and removes, in the project's ``.env``.
LLM_VARIABLES = ("LLM_PROVIDER", "LLM_MODEL")

#: Seconds a check of the language model may take: AuRE's own limit on one
#: call (``LLM_TIMEOUT``, 120 by default), and the CLI's start.
LLM_CHECK_TIMEOUT = 180.0


class WriteFailedError(Exception):
    """``nrw.toml`` could not be written -- a read-only project, a full disk."""


class SettingsData:
    """The Settings page of one project.

    Args:
        root: Project root.
        writable: Whether writes are allowed at all.
        why_read_only: The reason, when not writable, for the page to show.
        count_runs: How many runs the catalog holds, ``None`` if unreadable.
        watching: The folder the running server reads now.
        on_saved: Called once a save has written ``nrw.toml``, so the server
            follows it at once, whatever the file's timestamps say.
    """

    def __init__(
        self,
        root: Path,
        *,
        writable: bool,
        why_read_only: str = "",
        count_runs: Callable[[], int | None],
        watching: Callable[[], str | None],
        on_saved: Callable[[], None],
    ) -> None:
        self.root = Path(root).resolve()
        self.writable = writable
        self.why_read_only = why_read_only
        self._count_runs = count_runs
        self._watching = watching
        self._on_saved = on_saved
        #: Folder checks: two at most, each with the same deadline as a read.
        self.checks = Bounded(
            slots=2,
            timeout=SOURCE_TIMEOUT,
            name="nrw-folder-check",
            what="The folder",
            busy=(
                "Folder checks are already waiting for an answer -- the data "
                "mount may be unavailable. Try again once they finish."
            ),
        )
        #: One check of the language model at a time: each is a billed call.
        self._llm_check = threading.Lock()

    def needs_setup(self) -> bool:
        """Whether nothing can be watched until someone sets the experiment up.

        From ``nrw.toml`` alone, never the data mount; a file that cannot be
        read counts as needing setup.
        """
        from nr_workbench.experiment.config import experiment_config_for

        return experiment_config_for(self.root).needs_setup

    def settings(self) -> dict[str, Any]:
        """What the page shows: values, defaults, choices and problems.

        Read from ``nrw.toml`` itself, never from the poller, which would start
        listing the folder the page is there to change.
        """
        view = settings_view(self.root, catalogued_runs=self._count_runs())
        view["writable"] = self.writable and view["editable"]
        view["read_only_reason"] = self.why_read_only
        view["watching"] = self._watching()
        return view

    def save_settings(
        self, revision: Any, changes: Any, confirmed: Any = None
    ) -> dict[str, Any]:
        """Save settings into ``nrw.toml``; the server follows the new file at once.

        Raises:
            RequestError: A malformed request (400).
            SettingsError: A value is not allowed (400).
            NeedsConfirmation: The change needs confirming first (409).
            TomlConflictError: ``nrw.toml`` changed since the page loaded it (409).
            TomlEditError: ``nrw.toml`` cannot be edited safely (409).
            WriteFailedError: It could not be written (500, with the reason).
        """
        from nr_workbench.project.settings import save

        self._require_writable()
        if not isinstance(revision, str) or not revision:
            raise RequestError("revision is required: load the settings before saving")
        if not isinstance(changes, dict) or not changes:
            raise RequestError("changes must be a non-empty object")
        confirmed = [] if confirmed is None else confirmed
        if not isinstance(confirmed, list) or not all(
            isinstance(c, str) for c in confirmed
        ):
            raise RequestError("confirmed must be a list of names")
        try:
            result = save(
                self.root,
                changes,
                base_revision=revision,
                confirmed=set(confirmed),
                catalogued_runs=self._count_runs(),
            )
        except OSError as exc:
            raise WriteFailedError(f"nrw.toml could not be written: {exc}") from exc
        if result.changed:
            self._on_saved()
        return {"result": result.as_dict(), "settings": self.settings()}

    def check_folder(
        self, location: Any, ipts: Any = None, kind: Any = None
    ) -> dict[str, Any]:
        """What a folder holds, before it is chosen as the data source.

        Behind the write gate, although it writes nothing: it lists whatever
        path it is given. Built as the watcher would build it
        (:func:`~nr_workbench.experiment.workspace.check_source`), so it reads
        exactly the folder that would be watched.

        Args:
            location: The location to try, placeholders allowed; ``None`` for
                nrw's default.
            ipts: The IPTS to fill in, when the page is changing it too.
            kind: The source kind, when the page is changing it too: a check
                reads what the save would watch, and no other.

        Raises:
            SettingsError: The location is not one that could be saved (400).
            Busy: Checks are already waiting (409).
            TimedOut: The folder did not answer in time (504).
        """
        from nr_workbench.experiment.workspace import check_source

        self._require_writable()
        changes: dict[str, Any] = {"source.location": location}
        if ipts is not None:
            changes["ipts"] = ipts
        if kind is not None:
            changes["source.kind"] = kind
        return check_source(self.root, changes, run=self.checks.run)

    def llm(self) -> dict[str, Any]:
        """The language model the page's jobs use, and where that is set.

        Read from the files, never loaded: see the module's docstring. No key
        is shown, not even redacted -- reading this needs no link -- only
        whether one is set.

        Returns:
            What the project's ``.env`` chooses (``choice``: ``claude_code``,
            ``outside`` when it sets no provider, ``other`` when it sets one by
            hand), what applies with it and without it, which variables
            ``nrw serve``'s own environment sets (and so wins with), and
            whether the Claude Code provider can be used here.
        """
        from dotenv import dotenv_values

        from nr_workbench.aure_adapter import claude_code_supported, is_available
        from nr_workbench.env import ENVIRONMENT, where_set
        from nr_workbench.project.envfile import ENV_FILE, read_env_file

        current = read_env_file(self.root)
        project = {
            name: value
            for name, value in dotenv_values(
                stream=io.StringIO(current.text or "")
            ).items()
            if name in LLM_VARIABLES and value is not None
        }
        provider = project.get("LLM_PROVIDER")
        if provider is None:
            choice = "outside"
        elif provider == CLAUDE_CODE:
            choice = CLAUDE_CODE
        else:
            choice = "other"  # written by hand: shown, never overwritten unasked
        effective = where_set(self.root)
        binary = effective.get("AURE_CLAUDE_BIN")
        return {
            "revision": current.revision,
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
                "supported": claude_code_supported(),
                # On this server's PATH, which is the one its jobs get; the
                # setting may name the binary, or a path to it.
                "cli": bool(shutil.which(binary.value if binary else "claude")),
            },
            "writable": self.writable,
            "read_only_reason": self.why_read_only,
        }

    def save_llm(self, revision: Any, provider: Any, model: Any = "") -> dict[str, Any]:
        """Choose the language model, in the project's ``.env``.

        Args:
            revision: The ``.env`` revision the page was shown.
            provider: ``claude_code`` -- Claude, through the Claude Code CLI --
                or ``None`` for what is set outside the project: its lines are
                removed.
            model: For ``claude_code``, the model the CLI is asked for; empty
                for the CLI's default. Written even when empty, so a model
                ``~/.aure`` names for another provider is not passed to
                ``claude``.

        Returns:
            ``{"changes": [...], "llm": ...}``: what changed in ``.env``, and
            the section as it is now.

        Raises:
            WritesDisabledError: The server was started read-only.
            RequestError: The request is malformed, or the model is not one
                word.
            EnvFileConflict: ``.env`` changed since the page read it.
            EnvFileError: ``.env`` cannot be written safely.
        """
        from nr_workbench.project.envfile import EnvFileError, check_value, set_values

        self._require_writable()
        if not isinstance(revision, str) or not revision:
            raise RequestError("revision is required: load the settings before saving")
        if provider not in (CLAUDE_CODE, None):
            raise RequestError(
                f"provider must be {CLAUDE_CODE!r}, or null for what is set "
                f"outside this project, not {provider!r}"
            )
        if not isinstance(model, str):
            raise RequestError(f"model must be text, not {model!r}")
        try:
            model = check_value("LLM_MODEL", model.strip())
        except EnvFileError as exc:
            raise RequestError(str(exc)) from exc
        values: dict[str, str | None] = (
            {"LLM_PROVIDER": CLAUDE_CODE, "LLM_MODEL": model}
            if provider == CLAUDE_CODE
            else dict.fromkeys(LLM_VARIABLES)
        )
        changes = set_values(self.root, values, base_revision=revision)
        return {"changes": changes, "llm": self.llm()}

    def check_llm(self) -> dict[str, Any]:
        """Make one real call to the language model the page's jobs would use.

        ``nrw check-llm --endpoint``, run as a job step is, so it reads the
        files as a job does. Behind the write gate: the call is billed.

        Returns:
            ``{"probe": ...}``: its status (``ok``, ``warn``, ``error``), what
            it said, the model that answered, and how long it took.

        Raises:
            WritesDisabledError: The server was started read-only.
            Busy: A check is running already.
            TimedOut: No answer in :data:`LLM_CHECK_TIMEOUT` seconds.
        """
        from nr_workbench.web import jobs

        self._require_writable()
        if not self._llm_check.acquire(blocking=False):
            raise Busy("A check of the language model is running already.")
        try:
            completed = subprocess.run(  # noqa: S603 - nrw's own command line
                jobs.nrw_command("check-llm", "--endpoint", "--json"),
                cwd=self.root,
                env=jobs.child_environment(),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=LLM_CHECK_TIMEOUT,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise TimedOut(
                f"The language model did not answer in {LLM_CHECK_TIMEOUT:.0f} seconds."
            ) from exc
        finally:
            self._llm_check.release()
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
        described["key"] = any(name in settings for name in SECRET_VARS)
        return described

    def _require_writable(self) -> None:
        require_writable(self.writable, self.why_read_only)
