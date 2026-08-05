"""Execute a refl1d script and capture its output.

Adapted from ``neutrons-ai/nr-analyzer`` ``analyzer_tools/analysis/run_fit.py``
@ b09e2e4 (BSD-3-Clause). See ``upstream.toml``.

The load-bearing detail carried over is the bumps 1.0.x export workaround
documented in :func:`run_fit` -- getting that wrong silently discards every
uncertainty output, which is exactly the kind of failure this package exists to
prevent.

refl1d and bumps are imported inside the functions that use them: importing
them at module scope would add seconds to ``nrw --help``.
"""

from __future__ import annotations

import runpy
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Settings that identify a fit run. Anything absent falls back to the fitter's
#: own default, which is recorded as ``None`` rather than guessed at.
FIT_SETTING_KEYS = ("method", "steps", "samples", "burn", "pop", "seed", "alpha")


class FitError(Exception):
    """Raised when a script cannot be loaded or a fit cannot be run."""


# --------------------------------------------------------------------------
# Observing which files a script actually reads
# --------------------------------------------------------------------------
#
# A hand-written script does not declare its inputs, and guessing them from
# string literals fails on the pattern every real script here uses:
#
#     os.path.join(os.path.dirname(__file__), "..", "data", "steady", fname)
#
# So we observe instead of guess. An audit hook sees every genuine file open,
# however the path was assembled -- through numpy.loadtxt, refl1d's loaders, or
# a path built at runtime from a run number. That is exact rather than
# heuristic, and it cannot be fooled by clever path construction.

#: Collector for the currently-tracked execution, or None. Guarded by the lock
#: because an audit hook can fire from any thread.
_tracked: set[str] | None = None
_tracked_lock = threading.Lock()
_hook_installed = False


def _audit_hook(event: str, args: tuple[Any, ...]) -> None:
    """Record file opens while tracking is active.

    Must stay cheap and must never raise: this runs on every open in the
    process, including the interpreter's own.
    """
    if event != "open" or _tracked is None:
        return
    path = args[0] if args else None
    if not isinstance(path, str | bytes):
        return
    try:
        text = path.decode() if isinstance(path, bytes) else path
    except UnicodeDecodeError:
        return
    with _tracked_lock:
        if _tracked is not None:
            _tracked.add(text)


@contextmanager
def track_opened_files() -> Iterator[set[str]]:
    """Collect every file path opened inside the block.

    Audit hooks cannot be removed once added, so one hook is installed lazily
    and gated on a module-level collector that this manager sets and clears.

    Yields:
        The set of opened paths, populated as the block runs.
    """
    global _tracked, _hook_installed

    if not _hook_installed:
        sys.addaudithook(_audit_hook)
        _hook_installed = True

    collected: set[str] = set()
    with _tracked_lock:
        previous, _tracked = _tracked, collected
    try:
        yield collected
    finally:
        with _tracked_lock:
            _tracked = previous


@dataclass
class FitOutcome:
    """What a fit produced.

    Attributes:
        chisq: Reduced chi-squared, if obtainable.
        n_free: Number of free parameters.
        n_points: Number of data points.
        artifacts: Named output files relative to the fit directory.
        models: Export position to model name; see :func:`describe_models`.
        export_ok: Whether the bumps export completed.
        export_error: Why the export failed, if it did.
    """

    chisq: float | None = None
    n_free: int | None = None
    n_points: int | None = None
    artifacts: dict[str, str] = field(default_factory=dict)
    models: list[dict[str, Any]] = field(default_factory=list)
    export_ok: bool = True
    export_error: str | None = None


@dataclass
class LoadedProblem:
    """A problem loaded from a script, plus the files loading it read.

    Attributes:
        problem: The bumps ``FitProblem``.
        opened: Absolute paths the script opened while building it.
    """

    problem: Any
    opened: list[Path] = field(default_factory=list)


def load_problem(script: Path, *, root: Path | None = None) -> LoadedProblem:
    """Execute a script and return its ``problem`` plus the files it read.

    Args:
        script: Path to a refl1d script defining ``problem = FitProblem(...)``.
        root: Project root. When given, only files inside it are reported as
            inputs -- otherwise every stdlib and site-packages file the
            interpreter touched would be listed.

    Returns:
        The problem and the data files it consumed.

    Raises:
        FitError: If the script fails to execute or defines no ``problem``.
    """
    script = Path(script).resolve()
    if not script.is_file():
        raise FitError(f"Script not found: {script}")

    with track_opened_files() as opened:
        try:
            namespace = runpy.run_path(str(script), run_name="__nrw_fit_run__")
        except Exception as exc:
            raise FitError(f"Script {script} raised while loading: {exc}") from exc

    if "problem" not in namespace:
        raise FitError(
            f"Script {script} defines no module-level `problem`. "
            "A fit script must end with `problem = FitProblem(...)`."
        )

    return LoadedProblem(
        problem=namespace["problem"],
        opened=_filter_inputs(opened, script=script, root=root),
    )


def _filter_inputs(opened: set[str], *, script: Path, root: Path | None) -> list[Path]:
    """Reduce observed opens to the data files that count as fit inputs.

    Drops the script itself (recorded separately), anything outside the project,
    and the interpreter's own reads -- imported modules, caches, and the
    site-packages tree.
    """
    root_resolved = Path(root).resolve() if root else None
    interpreter_prefixes = tuple(Path(p).resolve() for p in sys.path if p)

    keep: list[Path] = []
    for raw in sorted(opened):
        try:
            path = Path(raw).resolve()
        except (OSError, ValueError):
            continue
        if not path.is_file() or path == script:
            continue
        if path.suffix in {".py", ".pyc", ".pyi", ".so", ".pth", ".dylib"}:
            continue
        if root_resolved is not None:
            try:
                path.relative_to(root_resolved)
            except ValueError:
                continue
        elif any(_is_within(path, prefix) for prefix in interpreter_prefixes):
            continue
        keep.append(path)
    return keep


def _is_within(path: Path, parent: Path) -> bool:
    """Return whether ``path`` sits under ``parent``."""
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def describe_problem(problem: Any) -> tuple[int | None, int | None]:
    """Report a problem's free-parameter and data-point counts.

    Both are best-effort: the bumps API for these has moved between versions,
    and a missing count is not worth failing a fit over.

    Args:
        problem: A bumps ``FitProblem``.

    Returns:
        ``(n_free, n_points)``, either of which may be ``None``.
    """
    n_free: int | None = None
    n_points: int | None = None

    with suppress(Exception):
        n_free = len(problem.getp())

    for attr in ("dof", "numpoints"):
        try:
            value = getattr(problem, attr)
            n_points = int(value() if callable(value) else value)
            break
        except Exception:
            continue

    return n_free, n_points


def describe_models(problem: Any) -> list[dict[str, Any]]:
    """Map each model's export position to its name and size.

    bumps writes its per-model exports as ``<basename>-<i+1>-refl.dat``,
    numbered by position in ``problem.models`` and nothing else -- the name is
    not used in the filename. Anything reading those files afterwards therefore
    has to know the ordering, and reconstructing it by re-parsing the script is
    guesswork that goes wrong quietly: a plot labelled ``ocv2`` showing
    ``ocv1``'s data looks perfectly reasonable.

    So the ordering is recorded here, once, from the object that was actually
    fitted. Generated scripts name their Experiments after the spec slot
    (``ocv1#0``); a hand-written script may not name them at all, in which case
    only the position is recorded and consumers can say so honestly rather than
    invent a label.

    Args:
        problem: A bumps ``FitProblem``.

    Returns:
        One entry per model, in export order, each with its 1-based ``index``
        (matching the export filename), ``name`` if the script set one, and
        ``n_points`` where obtainable. Empty if the problem exposes no models.
    """
    try:
        models = list(problem.models)
    except Exception:
        return []

    described: list[dict[str, Any]] = []
    for position, model in enumerate(models, start=1):
        entry: dict[str, Any] = {"index": position}
        name = getattr(model, "name", None)
        if name:
            entry["name"] = str(name)
        with suppress(Exception):
            entry["n_points"] = int(model.numpoints())
        described.append(entry)
    return described


def run_fit(
    problem: Any,
    output_dir: Path,
    *,
    method: str = "amoeba",
    steps: int | None = None,
    samples: int | None = None,
    burn: int | None = None,
    pop: int | None = None,
    seed: int | None = None,
    alpha: float | None = None,
    quiet: bool = False,
) -> FitOutcome:
    """Run a fit and write bumps' full export into ``output_dir``.

    Takes an already-loaded problem rather than a script path, so the caller
    can observe the script's inputs (see :func:`load_problem`) without
    executing it twice.

    Args:
        problem: A bumps ``FitProblem``.
        output_dir: Directory to write the bumps export into.
        method: Bumps fitter name (``amoeba``, ``dream``, ``lm``, ``de``, ...).
        steps: Maximum optimizer steps.
        samples: DREAM sample count.
        burn: DREAM burn-in.
        pop: Population size.
        seed: Random seed, for a reproducible run.
        alpha: Bumps convergence parameter.
        quiet: Suppress the fitter's own progress output.

    Returns:
        What the fit produced.

    Raises:
        FitError: If the fit itself fails.
    """
    from bumps.fitters import fit as bumps_fit

    # bumps names its export files after ``problem.name``; unset, that becomes
    # the literal string "None" in every filename.
    if not getattr(problem, "name", None):
        problem.name = "problem"

    kwargs: dict[str, Any] = {"method": method, "verbose": 0 if quiet else 1}
    for key, value in (
        ("steps", steps),
        ("samples", samples),
        ("burn", burn),
        ("pop", pop),
        ("seed", seed),
        ("alpha", alpha),
    ):
        if value is not None:
            kwargs[key] = value

    try:
        result = bumps_fit(problem, **kwargs)
    except Exception as exc:
        raise FitError(f"Fit failed: {exc}") from exc

    outcome = FitOutcome()
    with suppress(Exception):
        outcome.chisq = float(problem.chisq())
    outcome.n_free, outcome.n_points = describe_problem(problem)
    outcome.models = describe_models(problem)

    _export(problem, result, Path(output_dir), outcome)
    return outcome


def _export(problem: Any, result: Any, output_dir: Path, outcome: FitOutcome) -> None:
    """Write bumps' export files, recording failure rather than raising.

    **Do not pass ``export=`` to ``bumps.fitters.fit``.** In bumps 1.0.x that
    path calls ``export_fit(export, problem, result.state, ...)``, forwarding a
    bare ``MCMCDraw`` as the ``fit`` argument. ``export_fit`` then does
    ``getattr(fit, "fit_state", getattr(fit, "state", None))``, which an
    ``MCMCDraw`` does not satisfy, so it resolves to ``None`` and the whole
    uncertainty block is **silently skipped**: no ``*-err.json`` parameter
    uncertainties, no ``*-chain.mc.gz``, no DREAM diagnostics. Calling
    ``export_fit`` ourselves with the ``OptimizeResult`` -- the documented
    usage -- lets it recover ``result.state`` and write everything.

    A failed export is recorded and does not fail the fit: the parameters are
    already in hand, and losing them because a plot could not be written would
    be worse than an incomplete record that says so.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        from bumps.webview.server.api import export_fit

        export_fit(str(output_dir), problem, result, basename=problem.name)
    except Exception as exc:
        outcome.export_ok = False
        outcome.export_error = f"{type(exc).__name__}: {exc}"

    outcome.artifacts = collect_artifacts(output_dir)


#: Suffix to artifact name. Order matters: the first match wins, so the more
#: specific patterns come first.
_ARTIFACT_PATTERNS: tuple[tuple[str, str], ...] = (
    ("-err.json", "uncertainties"),
    (".par", "parameters"),
    ("-chain.mc.gz", "chain"),
    ("-refl.dat", "reflectivity"),
    ("-profile.dat", "profile"),
    ("-expt.json", "experiment"),
    ("problem.json", "problem_json"),
)


def collect_artifacts(output_dir: Path) -> dict[str, str]:
    """Name the interesting files bumps wrote.

    Args:
        output_dir: The directory bumps exported into.

    Returns:
        Mapping of artifact name to path relative to ``output_dir``'s parent,
        so the paths read naturally inside a fit record.
    """
    artifacts: dict[str, str] = {}
    if not output_dir.is_dir():
        return artifacts

    parent = output_dir.parent
    for path in sorted(output_dir.rglob("*")):
        if not path.is_file():
            continue
        name = path.name
        for suffix, label in _ARTIFACT_PATTERNS:
            if name.endswith(suffix) and label not in artifacts:
                artifacts[label] = path.relative_to(parent).as_posix()
                break
    return artifacts
