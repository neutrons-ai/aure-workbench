"""A sample's models, for the Experiment page: its specs, and a new one written.

A spec is written by ``nrw model new``, run as a child process in the project:
the command a person types, so the page and the terminal cannot disagree about
what a new spec holds. The model code is never imported into the server, which
would load it, and whatever it imports, into the process serving every page.

Nothing here imports Flask; :mod:`nr_workbench.web.experiment_api` maps the
errors to status codes.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

from nr_workbench.bounded import TimedOut
from nr_workbench.experiment.model import CatalogValidationError, validate_sample_id
from nr_workbench.project.samples import validate_model_name
from nr_workbench.web.experiment import RequestError, WritesDisabledError

#: Seconds ``nrw model new`` may take. It reads each data file's header, so a
#: few seconds on a local disk; longer means something is wrong.
MODEL_NEW_TIMEOUT = 120.0

#: Where a sample's specs live, and where ``nrw model new`` writes them.
MODELS_DIR = "models"


class ModelRefused(Exception):
    """The command declined, or cannot run yet; the message says why."""


def nrw_command(*args: str) -> list[str]:
    """The command line that runs ``nrw`` with *args*, as this server does.

    ``sys.executable -m nr_workbench`` is the interpreter serving the page, so
    the child is the same nrw, whatever is first on ``PATH``.
    """
    return [sys.executable, "-m", "nr_workbench", *args]


class ModelsData:
    """A project's models, as the Experiment page lists and creates them.

    Args:
        root: Project root.
        writable: Whether writes are allowed at all.
        why_read_only: What a refused write says.
    """

    def __init__(
        self, root: Path, *, writable: bool = True, why_read_only: str = ""
    ) -> None:
        self.root = Path(root)
        self.writable = writable
        self.why_read_only = why_read_only

    def models(self, sample_id: str) -> dict[str, Any]:
        """One sample's specs, and whether a new one can be written.

        Raises:
            RequestError: If the sample id is not usable.
        """
        directory = self._sample_dir(sample_id)
        models = []
        for spec in sorted((directory / MODELS_DIR).glob("*.yaml")):
            models.append(
                {
                    "name": spec.stem,
                    "spec": spec.relative_to(self.root).as_posix(),
                    "script": spec.with_suffix(".py").is_file(),
                }
            )
        return {
            "sample": sample_id,
            "exists": directory.is_dir(),
            "has_data": _has_data(directory),
            "models": models,
            "writable": self.writable,
        }

    def create(self, sample_id: str, name: Any) -> dict[str, Any]:
        """Write a new spec with ``nrw model new``, from the data on disk.

        Returns:
            The sample's models, as :meth:`models` gives them, with ``output``:
            what the command printed, notes on angles and series included.

        Raises:
            WritesDisabledError: The server was started read-only.
            RequestError: The sample id or the name is not usable.
            ModelRefused: The sample has no data yet, the spec exists, or the
                command failed; the message is the command's own.
            TimedOut: The command did not finish in time.
        """
        if not self.writable:
            raise WritesDisabledError(self.why_read_only or "This server is read-only.")
        directory = self._sample_dir(sample_id)
        try:
            validate_model_name(name)
        except ValueError as exc:
            raise RequestError(str(exc)) from exc
        if not directory.is_dir():
            raise ModelRefused(
                f"samples/{sample_id}/ does not exist yet. Apply creates it, "
                "with the data a spec is built from."
            )
        spec = directory / MODELS_DIR / f"{name}.yaml"
        if spec.exists():
            # Checked here for the message; `nrw model new` refuses too, without
            # --force, so two requests racing for one name cannot overwrite.
            raise ModelRefused(
                f"{spec.relative_to(self.root).as_posix()} already exists. "
                "Choose another name, or edit that spec."
            )
        # `--` ends the options: the sample id is never read as one.
        output = run_nrw(
            self.root,
            "model",
            "new",
            "--name",
            name,
            "--",
            sample_id,
            timeout=MODEL_NEW_TIMEOUT,
        )
        return {**self.models(sample_id), "output": output}

    def _sample_dir(self, sample_id: str) -> Path:
        try:
            validate_sample_id(sample_id)
        except CatalogValidationError as exc:
            raise RequestError(str(exc)) from exc
        return self.root / "samples" / sample_id


def run_nrw(root: Path, *args: str, timeout: float) -> str:
    """Run one ``nrw`` command in the project, and return what it printed.

    Raises:
        ModelRefused: The command exited non-zero; its message is the error.
        TimedOut: It did not finish within *timeout* seconds, and was stopped.
    """
    try:
        result = subprocess.run(
            nrw_command(*args),
            cwd=root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise TimedOut(
            f"`nrw {' '.join(args[:2])}` did not finish within {timeout:.0f} s "
            "and was stopped."
        ) from exc
    output = result.stdout.strip()
    if result.returncode != 0:
        # Click prints a refusal as "Error: <message>"; the message is the part
        # a person needs, and the rest is kept for the log.
        message = output.rsplit("Error: ", 1)[-1] if "Error: " in output else output
        raise ModelRefused(message or f"`nrw {args[0]}` exited {result.returncode}.")
    return output


def _has_data(directory: Path) -> bool:
    """Whether the sample holds any data a spec could be built from."""
    for sub in ("steady", "tnr"):
        folder = directory / "data" / sub
        if folder.is_dir() and any(folder.iterdir()):
            return True
    return False
