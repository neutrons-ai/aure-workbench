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
import unicodedata
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

#: What the page may ask a fitter for. Generous, so they never decide an
#: analysis; finite, so a typo is not a week of CPU on a shared node.
FIT_LIMITS = {
    "steps": (1, 1_000_000),
    "samples": (1, 100_000_000),
    "burn": (0, 1_000_000),
}

#: Settings only DREAM takes: the others optimize, and draw no samples.
DREAM_ONLY = ("samples", "burn")

#: A fit's note, one line: it is stored in the record, and heads its notes.
MAX_FIT_NOTE = 500


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
        from nr_workbench.web.jobs import JobRunner

        self.root = Path(root)
        self.writable = writable
        self.why_read_only = why_read_only
        self.jobs = JobRunner(self.root, on_finished=self._recorded)

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
        from nr_workbench.aure_adapter import is_available

        return {
            "sample": sample_id,
            "exists": directory.is_dir(),
            "has_data": _has_data(directory),
            "runs": _steady_runs(directory),
            "models": models,
            "writable": self.writable,
            # Whether it is installed, found without importing it: AuRE brings
            # the whole language-model stack, seconds of it, into the server.
            "aure": is_available(),
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
        self._refuse_unless_writable()
        directory = self._sample_dir(sample_id)
        name = _model_name(name)
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

    def fit(self, sample_id: str, name: Any, request: dict[str, Any]) -> dict[str, Any]:
        """Start fitting a spec: ``nrw model generate``, then ``nrw fit run``.

        The script is generated from the spec every time, and generate refuses
        a script edited by hand -- so what runs is the spec's, never code
        written into ``models/`` some other way.

        Args:
            sample_id: The sample.
            name: The model, ``models/<name>.yaml``.
            request: ``method`` (amoeba, de or dream), and optionally
                ``steps``, DREAM's ``samples`` and ``burn``, a one-line
                ``note``, and ``force`` to run again what already ran.

        Returns:
            ``{"job": ...}``, the job started.

        Raises:
            WritesDisabledError: The server was started read-only.
            RequestError: A setting is not usable.
            ModelRefused: There is no such spec.
            JobBusy: A job is running already.
        """
        from nr_workbench.fitters import FITTERS

        self._refuse_unless_writable()
        directory = self._sample_dir(sample_id)
        name = _model_name(name)
        spec = directory / MODELS_DIR / f"{name}.yaml"
        if not spec.is_file():
            raise ModelRefused(
                f"samples/{sample_id}/{MODELS_DIR}/{name}.yaml does not exist."
            )
        method = request.get("method", "amoeba")
        if method not in FITTERS:
            raise RequestError(
                f"method must be one of {', '.join(FITTERS)}, not {method!r}."
            )
        # `--option=value`, one argument each: a value is never read as an option.
        options: list[str] = []
        for key, (low, high) in FIT_LIMITS.items():
            value = request.get(key)
            if value is None or value == "":
                continue
            if key in DREAM_ONLY and method != "dream":
                raise RequestError(
                    f"{key} is a DREAM setting, and {method} draws no samples."
                )
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not low <= value <= high
            ):
                raise RequestError(
                    f"{key} must be a whole number from {low} to {high}."
                )
            options.append(f"--{key}={value}")
        note = _fit_note(request.get("note", ""))
        if note:
            options.append(f"--note={note}")
        if request.get("force") is True:
            options.append("--force")
        relative = spec.relative_to(self.root)
        job = self.jobs.start(
            label=f"{method} fit of {name}",
            sample=sample_id,
            model=name,
            steps=[
                ["model", "generate", relative.as_posix()],
                # --verbose: the fitter's progress is what the page shows.
                [
                    "fit",
                    "run",
                    relative.with_suffix(".py").as_posix(),
                    f"--method={method}",
                    "--verbose",
                    *options,
                ],
            ],
        )
        return {"job": job.as_dict()}

    def job(self, offset: int = 0) -> dict[str, Any]:
        """The job running, or the last one; and what it printed from *offset*."""
        job = self.jobs.current()
        if job is None:
            return {"job": None, "log": "", "offset": 0}
        from nr_workbench.web.jobs import JobNotFound

        try:
            text, offset = self.jobs.log(job.id, offset)
        except JobNotFound:
            text, offset = "", 0
        return {"job": job.as_dict(), "log": text, "offset": offset}

    def cancel(self, job_id: Any) -> dict[str, Any]:
        """Stop the running job.

        Raises:
            WritesDisabledError: The server was started read-only.
            JobNotFound: *job_id* is not the current job.
        """
        from nr_workbench.web.jobs import JobNotFound

        self._refuse_unless_writable()
        if not isinstance(job_id, str):
            raise JobNotFound("No such job.")
        return {"job": self.jobs.cancel(job_id).as_dict()}

    def _recorded(self, job: Any) -> None:
        """Find the fit a finished job recorded: its model's, since it started."""
        from nr_workbench.project.layout import ProjectLayout
        from nr_workbench.provenance.index import FitIndex

        index = FitIndex(ProjectLayout(root=self.root).index_file)
        for entry in index.fits(sample=job.sample):
            if (
                entry.get("model") == job.model
                and str(entry.get("started_at") or "") >= job.started_at
            ):
                job.fit_id = entry.get("fit_id")
                return

    def quick_fit(self, sample_id: str, name: Any, run: Any = None) -> dict[str, Any]:
        """Start a quick fit with AuRE: a stack proposed from sample.md, then fitted.

        AuRE's own run is reconnaissance (see :mod:`nr_workbench.commands.
        aure_cmd`); the fit that counts is the one ``nrw fit run`` records. So
        the job goes on from AuRE's quick budget to import the spec it proposes,
        generate its script, and fit that with amoeba from AuRE's values -- and
        that fit is the one in Fits.

        Args:
            sample_id: The sample.
            name: The model to write: ``models/<name>.yaml``, and AuRE's run
                in ``aure/<name>/``.
            run: Which steady run to fit; needed when the sample has several.

        Returns:
            ``{"job": ...}``, the job started.

        Raises:
            WritesDisabledError: The server was started read-only.
            RequestError: The name or the run is not usable.
            ModelRefused: The name is taken, or AuRE is not installed.
            JobBusy: A job is running already.
        """
        from nr_workbench.aure_adapter import is_available
        from nr_workbench.aure_setup import AURE_DIR

        self._refuse_unless_writable()
        directory = self._sample_dir(sample_id)
        name = _model_name(name)
        if run is not None and (
            isinstance(run, bool) or not isinstance(run, int) or run <= 0
        ):
            raise RequestError(f"run must be a run number, not {run!r}.")
        for taken in (
            directory / MODELS_DIR / f"{name}.yaml",
            directory / AURE_DIR / name,
        ):
            if taken.exists():
                raise ModelRefused(
                    f"{taken.relative_to(self.root).as_posix()} already exists. "
                    "Choose another name."
                )
        if not is_available():
            raise ModelRefused(
                "AuRE is not installed where nrw serve runs, so there is nothing "
                "to run a quick fit with. `nrw doctor` lists what is installed."
            )
        base = f"samples/{sample_id}"
        chosen = [f"--run={run}"] if run is not None else []
        job = self.jobs.start(
            label=f"quick fit of {name} with AuRE",
            sample=sample_id,
            model=name,
            steps=[
                ["aure", "new", f"--name={name}", *chosen, "--", sample_id],
                # Needs a language-model endpoint; without one it says how to
                # set one, and the job stops here.
                [
                    "aure",
                    "run",
                    f"{base}/{AURE_DIR}/{name}/setup.yaml",
                    "--budget=quick",
                ],
                [
                    "aure",
                    "import",
                    f"{base}/{AURE_DIR}/{name}/output",
                    f"--sample={sample_id}",
                    f"--name={name}",
                    *chosen,
                ],
                ["model", "generate", f"{base}/{MODELS_DIR}/{name}.yaml"],
                [
                    "fit",
                    "run",
                    f"{base}/{MODELS_DIR}/{name}.py",
                    "--method=amoeba",
                    "--verbose",
                    "--note=From AuRE's quick fit, refined by amoeba.",
                ],
            ],
        )
        return {"job": job.as_dict()}

    def _refuse_unless_writable(self) -> None:
        if not self.writable:
            raise WritesDisabledError(self.why_read_only or "This server is read-only.")

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


def _model_name(name: Any) -> str:
    try:
        return validate_model_name(name)
    except ValueError as exc:
        raise RequestError(str(exc)) from exc


def _steady_runs(directory: Path) -> list[int]:
    """The runs in ``data/steady/``, from the file names, as apply names them."""
    from nr_workbench.instrument.reduced import parse_combined_name, parse_segment_name

    folder = directory / "data" / "steady"
    runs: set[int] = set()
    if folder.is_dir():
        for path in folder.iterdir():
            segment = parse_segment_name(path.name)
            run = segment.run if segment else parse_combined_name(path.name)
            if run is not None:
                runs.add(run)
    return sorted(runs)


def _fit_note(value: Any) -> str:
    """A fit's note: one line of text, or nothing."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise RequestError(f"note must be text, not {value!r}.")
    text = value.strip()
    if len(text) > MAX_FIT_NOTE:
        raise RequestError(
            f"note is {len(text)} characters; the limit is {MAX_FIT_NOTE}."
        )
    if any(unicodedata.category(char) in {"Cc", "Zl", "Zp"} for char in text):
        raise RequestError("note must be one line of text.")
    return text


def _has_data(directory: Path) -> bool:
    """Whether the sample holds any data a spec could be built from."""
    for sub in ("steady", "tnr"):
        folder = directory / "data" / sub
        if folder.is_dir() and any(folder.iterdir()):
            return True
    return False
