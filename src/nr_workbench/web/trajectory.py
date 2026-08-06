"""Layer parameters as a function of time, for a fit that includes a series.

A tNR co-refinement describes 15 or 130 slices with a handful of numbers, and
the interesting output is not the reflectivity curves -- it is what the layers
*did*. This assembles that: one trace per layer property, against the real
elapsed time, with an uncertainty band where the fit produced one.

Two sources, deliberately different:

* **Values** come from the ``-slabs.dat`` bumps writes for every model. That is
  the layer table the fit actually used for that slice, so nothing is
  recomputed and it works for any fitter.
* **The band** comes from the DREAM posterior in ``point.mc.gz``. A trajectory
  is a deterministic function of the fitted endpoints, so evaluating it once per
  posterior sample gives the correct spread -- including the correlation
  between the endpoints, which is strong for an interpolating form and which no
  per-parameter ``std`` can capture. Propagating ``std`` as if the endpoints
  were independent would overstate the band in the middle of the series, where
  it should be *tighter* than at the ends.
"""

from __future__ import annotations

import gzip
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

#: Columns bumps writes into ``<basename>-<n>-slabs.dat``, in order.
SLAB_COLUMNS = ("thickness", "roughness", "rho", "irho")

#: Percentiles for the band. 68% is one sigma, and matching what `-err.json`
#: reports keeps the two readable together.
BAND_PERCENTILES = (16.0, 84.0)

#: Cap on posterior samples used for the band. 8400 is typical and instant;
#: a long production chain is not, and the percentile is stable well before.
MAX_SAMPLES = 20_000


@dataclass
class Trace:
    """One layer property through time.

    Attributes:
        layer: Layer name.
        attribute: ``thickness``, ``roughness``, ``rho`` or ``irho``.
        times: Elapsed seconds per slice.
        values: The value the fit used at each slice.
        lo: Lower edge of the credible band, when a posterior exists.
        hi: Upper edge.
        median: Posterior median at each slice, when a posterior exists.
        constrained: Whether a constraint governs this path, as opposed to the
            value simply being the same in every slice.
    """

    layer: str
    attribute: str
    times: list[float] = field(default_factory=list)
    values: list[float] = field(default_factory=list)
    lo: list[float] = field(default_factory=list)
    hi: list[float] = field(default_factory=list)
    median: list[float] = field(default_factory=list)
    constrained: bool = False

    @property
    def path(self) -> str:
        """The spec path, e.g. ``CuOx.thickness``."""
        return f"{self.layer}.{self.attribute}"

    @property
    def varies(self) -> bool:
        """Whether the value actually changes across the series."""
        if len(self.values) < 2:
            return False
        spread = max(self.values) - min(self.values)
        scale = max(abs(v) for v in self.values) or 1.0
        return spread / scale > 1e-6

    def as_dict(self) -> dict[str, Any]:
        """Return the JSON form."""
        payload: dict[str, Any] = {
            "path": self.path,
            "layer": self.layer,
            "attribute": self.attribute,
            "times": self.times,
            "values": self.values,
            "constrained": self.constrained,
            "varies": self.varies,
        }
        if self.lo and self.hi:
            payload["lo"] = self.lo
            payload["hi"] = self.hi
        if self.median:
            payload["median"] = self.median
        return payload


def _numbered(directory: Path, suffix: str) -> dict[int, Path]:
    """Map export position to file, for ``<basename>-<n><suffix>``."""
    found: dict[int, Path] = {}
    for path in directory.glob(f"*{suffix}"):
        stem = path.name[: -len(suffix)]
        _, _, tail = stem.rpartition("-")
        try:
            found[int(tail)] = path
        except ValueError:
            continue
    return found


def read_slabs(path: Path) -> list[dict[str, float]]:
    """Read one model's layer table.

    Args:
        path: A ``-slabs.dat`` file.

    Returns:
        One mapping per layer, ambient first, with the columns bumps writes.
    """
    data = np.loadtxt(path, ndmin=2)
    if data.size == 0:
        return []
    return [
        {
            name: float(row[index])
            for index, name in enumerate(SLAB_COLUMNS)
            if index < len(row)
        }
        for row in data
    ]


def build(
    fit_dir: Path,
    *,
    model_names: dict[int, str],
    series: str,
    times: list[float],
    layers: list[str],
    constrained: set[str],
) -> list[Trace]:
    """Assemble every layer property's trajectory through the series.

    Args:
        fit_dir: The fit's ``fit/`` directory.
        model_names: Export position to name, e.g. ``{7: "tnr#0"}``.
        series: The series group name.
        times: Elapsed seconds, one per slice, in slice order.
        layers: Layer names in stack order.
        constrained: Spec paths a constraint governs.

    Returns:
        One trace per (layer, attribute) that has data, unvarying ones included
        so a reader can see what stayed put.
    """
    slabs = _numbered(fit_dir, "-slabs.dat")
    prefix = f"{series}#"

    # Export position -> slice index, from the names recorded at fit time.
    slices: dict[int, int] = {}
    for position, name in model_names.items():
        if name.startswith(prefix):
            try:
                slices[position] = int(name[len(prefix) :])
            except ValueError:
                continue
    if not slices:
        return []

    per_slice: dict[int, list[dict[str, float]]] = {}
    for position, index in slices.items():
        path = slabs.get(position)
        if path is None:
            continue
        try:
            per_slice[index] = read_slabs(path)
        except (OSError, ValueError):
            continue
    if not per_slice:
        return []

    ordered = sorted(per_slice)
    traces: list[Trace] = []
    for depth, layer in enumerate(layers):
        for attribute in SLAB_COLUMNS:
            values: list[float] = []
            stamps: list[float] = []
            for index in ordered:
                table = per_slice[index]
                if depth >= len(table) or attribute not in table[depth]:
                    continue
                value = table[depth][attribute]
                if not math.isfinite(value):
                    continue
                values.append(value)
                stamps.append(times[index] if index < len(times) else float(index))
            if len(values) < 2:
                continue
            traces.append(
                Trace(
                    layer=layer,
                    attribute=attribute,
                    times=stamps,
                    values=values,
                    constrained=f"{layer}.{attribute}" in constrained,
                )
            )
    return traces


# --------------------------------------------------------------------------
# The credible band
# --------------------------------------------------------------------------


def load_posterior(fit_dir: Path) -> tuple[np.ndarray | None, dict[str, int]]:
    """Load the DREAM posterior and the column each parameter occupies.

    ``point.mc.gz`` holds ``logp`` followed by one column per free parameter,
    and ``-err.json`` records each parameter's ``index`` into that block, which
    is the only reliable way to know which column is which.

    Args:
        fit_dir: The fit's ``fit/`` directory.

    Returns:
        ``(samples, {display_name: column})``. Samples is ``None`` when the fit
        was an optimiser run rather than a sampler, which is not an error.
    """
    point = next(iter(sorted(fit_dir.glob("*-point.mc.gz"))), None)
    errors = next(iter(sorted(fit_dir.glob("*-err.json"))), None)
    if point is None or errors is None:
        return (None, {})

    try:
        with gzip.open(point, "rt") as handle:
            samples = np.loadtxt(handle)
    except (OSError, ValueError):
        return (None, {})
    if samples.ndim != 2 or samples.shape[0] < 2:
        return (None, {})
    if samples.shape[0] > MAX_SAMPLES:
        step = samples.shape[0] // MAX_SAMPLES + 1
        samples = samples[::step]

    try:
        payload = json.loads(errors.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return (None, {})

    columns: dict[str, int] = {}
    for name, entry in payload.items():
        if isinstance(entry, dict) and isinstance(entry.get("index"), int):
            # `index` is already the chain column, not an offset into the
            # parameter block: column 0 is logp and the indices start at 1.
            # Adding one shifts every parameter onto its neighbour, which
            # produces bands that look plausible and belong to another
            # quantity -- a Cu roughness with a 500 A band.
            columns[str(name)] = int(entry["index"])
    return (samples, columns)


#: Identifiers a generated constraint expression may reference. The source is
#: emitted by this package's own resolver from a validated spec and lives in an
#: immutable result directory beside the script that was already executed to
#: produce the fit -- but the namespace is still pinned to exactly what the
#: grammar needs, so a hand-edited spec cannot widen it.
_EVAL_GLOBALS: dict[str, Any] = {"__builtins__": {}}


def band_for(
    expressions: dict[str, str],
    key_to_display: dict[str, str],
    samples: np.ndarray,
    columns: dict[str, int],
) -> dict[str, tuple[float, float, float]]:
    """Evaluate constrained slice values across the posterior.

    A trajectory is a deterministic function of the fitted endpoints, so this
    is arithmetic on chain columns -- no reflectivity is recomputed. Working
    from paired samples carries the endpoint correlation, which is what makes
    the band tighten in the middle of an interpolating form rather than widen.

    Args:
        expressions: Slice key to the Python source the generator emitted.
        key_to_display: Free-parameter key to the name ``-err.json`` uses.
        samples: The posterior, ``(n_samples, 1 + n_parameters)``.
        columns: Display name to column index.

    Returns:
        Slice key to ``(lo, median, hi)``. Keys whose expression cannot be
        evaluated are omitted rather than given a made-up band.

        The median is returned alongside because the *difference* between it
        and the reported best-fit value is diagnostic: on a real fit here the
        two sit up to 1.2 sigma apart, which says the posterior is skewed or a
        parameter is railing against a bound. Plotting only one hides that.
    """
    from bumps.parameter import pmath

    bound: dict[str, np.ndarray] = {}
    for key, display in key_to_display.items():
        column = columns.get(display)
        if column is not None and column < samples.shape[1]:
            bound[key] = samples[:, column]
    if not bound:
        return {}

    namespace = dict(_EVAL_GLOBALS)
    namespace["pmath"] = pmath
    namespace["np"] = np

    band: dict[str, tuple[float, float, float]] = {}
    for key, source in expressions.items():
        if not _references_only(source, bound):
            continue
        try:
            drawn = eval(source, namespace, {"P": bound})  # noqa: S307
        except Exception:
            continue
        array = np.asarray(drawn, dtype=float)
        if array.ndim != 1 or array.size < 2 or not np.isfinite(array).all():
            continue
        low, high = np.percentile(array, BAND_PERCENTILES)
        band[key] = (float(low), float(np.median(array)), float(high))
    return band


_P_REFERENCE = re.compile(r"P\[(['\"])(.+?)\1\]")


def _references_only(source: str, bound: dict[str, np.ndarray]) -> bool:
    """Whether every ``P[...]`` in the source has a posterior column."""
    keys = [match.group(2) for match in _P_REFERENCE.finditer(source)]
    return bool(keys) and all(key in bound for key in keys)


# --------------------------------------------------------------------------
# SLD profile alignment
# --------------------------------------------------------------------------

#: refl1d writes the profile with z = 0 at the *top* of the stack, so two
#: models whose total thickness differs are drawn offset from each other -- a
#: 3 A change in a copper layer shifts the substrate and everything below it.
#: Referencing z to the substrate surface instead puts the one interface that
#: cannot move at a fixed place, so the buried layers line up and only the
#: layer that actually changed moves. This is refl1d's own ``align=-1``.
SUBSTRATE_ALIGNED = "substrate"


def substrate_offset(slabs: list[dict[str, float]]) -> float:
    """Distance from refl1d's z = 0 to the substrate surface.

    Args:
        slabs: The layer table, ambient first.

    Returns:
        The offset to subtract from ``z``. Zero for a table too short to have
        a substrate.
    """
    if len(slabs) < 2:
        return 0.0
    return float(sum(layer.get("thickness", 0.0) for layer in slabs[:-1]))
