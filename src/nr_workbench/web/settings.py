"""Everything the Settings page knows, with no Flask anywhere.

The Settings page sets what the Experiment page watches: the IPTS, where the
data is, and how new runs are noticed, all saved into ``nrw.toml`` through
:mod:`nr_workbench.project.settings`. It is its own class, beside
:class:`~nr_workbench.web.experiment.ExperimentData` rather than inside it,
and speaks to it through three callables -- how many runs the catalog holds,
which folder the server reads now, and "the file changed" -- so neither needs
the other's internals.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from nr_workbench.bounded import Bounded
from nr_workbench.experiment.sources import SOURCE_TIMEOUT
from nr_workbench.experiment.views import settings_view
from nr_workbench.web.experiment import RequestError, require_writable


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

    def _require_writable(self) -> None:
        require_writable(self.writable, self.why_read_only)
