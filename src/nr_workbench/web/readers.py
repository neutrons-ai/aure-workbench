"""Readers for the file formats the UI plots.

Kept apart from :mod:`nr_workbench.web.project` because knowing the column
layout of a bumps export is a different concern from knowing the shape of a
project, and because these are the functions most likely to need a fix when
refl1d changes its output.

Every reader here returns plain Python lists rather than numpy arrays. The
consumers are ``json.dumps`` and a Jinja template, and converting once at the
boundary is cheaper than remembering to convert at every call site -- a stray
``np.float64`` raises ``TypeError: Object of type float64 is not JSON
serializable`` only when that particular endpoint is hit, which is exactly the
kind of bug that reaches a user.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

#: Columns bumps writes into ``<basename>-<n>-refl.dat``.
REFL_COLUMNS = ("Q", "dQ", "R", "dR", "theory", "fresnel")

#: Columns bumps writes into ``<basename>-<n>-profile.dat``.
PROFILE_COLUMNS = ("z", "rho", "irho")


class DataFormatError(Exception):
    """Raised when a file does not have the columns its name promises."""


def _clean(values: np.ndarray) -> list[float | None]:
    """Convert to a JSON-safe list, mapping non-finite values to ``None``.

    ``NaN`` and ``Infinity`` are not JSON, but Python's ``json`` emits them
    anyway by default and browsers reject the result. Plotly treats ``null`` as
    a gap, which is what a non-finite point means.
    """
    return [None if not math.isfinite(v) else float(v) for v in values]


@dataclass
class Curve:
    """One reflectivity curve, measured and optionally modelled.

    Attributes:
        label: What this curve is, e.g. ``ocv1#0`` or a filename stem.
        q: Momentum transfer in inverse angstroms.
        r: Measured reflectivity.
        dr: Uncertainty on ``r``.
        dq: Resolution, where the file carries it.
        theory: Model reflectivity, for a curve that came from a fit.
        theta: Incident angle in degrees, where known.
        time: Elapsed seconds, for a member of a time series.
        source: Path relative to the project root, for provenance.
    """

    label: str
    q: list[float | None]
    r: list[float | None]
    dr: list[float | None] = field(default_factory=list)
    dq: list[float | None] = field(default_factory=list)
    theory: list[float | None] = field(default_factory=list)
    theta: float | None = None
    time: float | None = None
    source: str | None = None

    @property
    def n_points(self) -> int:
        """How many points the curve holds."""
        return len(self.q)

    def as_dict(self) -> dict[str, Any]:
        """Return the JSON form, omitting empty optional series."""
        payload: dict[str, Any] = {
            "label": self.label,
            "q": self.q,
            "r": self.r,
            "n_points": self.n_points,
        }
        for name, value in (
            ("dr", self.dr),
            ("dq", self.dq),
            ("theory", self.theory),
        ):
            if value:
                payload[name] = value
        for name, scalar in (
            ("theta", self.theta),
            ("time", self.time),
            ("source", self.source),
        ):
            if scalar is not None:
                payload[name] = scalar
        return payload


def read_reduced(path: Path, *, label: str, root: Path | None = None) -> Curve:
    """Read a reduced REF_L ASCII file.

    Handles both the four-column steady-state form (``Q R dR dQ``) and the
    three-column tNR slice form (``Q R dR``). Note the column order: the
    reduced files put ``dQ`` *last*, where bumps' own exports put it second.

    Args:
        path: The file to read.
        label: Curve label to attach.
        root: Project root, for reporting a relative source path.

    Returns:
        The curve.

    Raises:
        DataFormatError: If the file has fewer than three usable columns.
    """
    path = Path(path)
    try:
        data = np.loadtxt(path, ndmin=2)
    except (ValueError, OSError) as exc:
        raise DataFormatError(f"Cannot read {path}: {exc}") from exc
    return _reduced_curve(
        data, label=label, name=str(path), source=_relative(path, root)
    )


def read_reduced_bytes(data: bytes, *, label: str, name: str) -> Curve:
    """Read a reduced file's bytes, as a data source hands them over.

    The experiment page plots runs straight from the data source -- the local
    folder today, Tiled later -- and a source provides bytes, not a path. The
    parsing is :func:`read_reduced`'s, so a quick look and an applied copy are
    read identically.

    Args:
        data: The file's content.
        label: Curve label to attach.
        name: The file's name, for messages and provenance.

    Raises:
        DataFormatError: If the bytes are not a reduced file.
    """
    import io

    try:
        table = np.loadtxt(io.BytesIO(data), ndmin=2)
    except ValueError as exc:
        raise DataFormatError(f"Cannot read {name}: {exc}") from exc
    return _reduced_curve(table, label=label, name=name, source=name)


def _reduced_curve(data: np.ndarray, *, label: str, name: str, source: str) -> Curve:
    if data.size == 0 or data.shape[1] < 3:
        raise DataFormatError(
            f"{name} has {data.shape[1] if data.size else 0} column(s); "
            "a reduced file needs at least Q, R and dR."
        )

    curve = Curve(
        label=label,
        q=_clean(data[:, 0]),
        r=_clean(data[:, 1]),
        dr=_clean(data[:, 2]),
        source=source,
    )
    if data.shape[1] >= 4:
        curve.dq = _clean(data[:, 3])
    return curve


def read_refl_dat(path: Path, *, label: str, root: Path | None = None) -> Curve:
    """Read a bumps ``-refl.dat`` export: data and model on the same grid.

    Args:
        path: The file to read.
        label: Curve label to attach.
        root: Project root, for reporting a relative source path.

    Returns:
        The curve, with ``theory`` populated.

    Raises:
        DataFormatError: If the file does not have the six expected columns.
    """
    path = Path(path)
    try:
        data = np.loadtxt(path, ndmin=2)
    except (ValueError, OSError) as exc:
        raise DataFormatError(f"Cannot read {path}: {exc}") from exc

    if data.size == 0 or data.shape[1] < len(REFL_COLUMNS):
        found = data.shape[1] if data.size else 0
        raise DataFormatError(
            f"{path} has {found} column(s); a bumps -refl.dat has "
            f"{len(REFL_COLUMNS)}: {', '.join(REFL_COLUMNS)}."
        )

    return Curve(
        label=label,
        q=_clean(data[:, 0]),
        dq=_clean(data[:, 1]),
        r=_clean(data[:, 2]),
        dr=_clean(data[:, 3]),
        theory=_clean(data[:, 4]),
        source=_relative(path, root),
    )


#: Coarsest z spacing that is still safe to plot, in angstroms. refl1d samples
#: its profile on the grid its numerical integration needs (0.1 A here), which
#: is one to two orders of magnitude finer than anything physical in these
#: models -- the thinnest oxide layer is tens of angstroms and the smallest
#: interfacial roughness a few. Rendering all of it makes a 21-model fit page
#: slow to pan for no visible gain, so the profile is thinned to this spacing.
#: Well below the roughness, so no interface is softened or moved.
PROFILE_MAX_SPACING = 0.5


@dataclass
class Profile:
    """One scattering-length-density depth profile.

    Attributes:
        label: Which model this profile belongs to.
        z: Depth in angstroms.
        rho: Real SLD in 1e-6 per square angstrom.
        irho: Imaginary SLD, where non-zero.
        source: Path relative to the project root.
        stride: Sampling stride applied; 1 means every point is present.
        n_source: Points in the file before thinning.
    """

    label: str
    z: list[float | None]
    rho: list[float | None]
    irho: list[float | None] = field(default_factory=list)
    source: str | None = None
    stride: int = 1
    n_source: int = 0

    def as_dict(self) -> dict[str, Any]:
        """Return the JSON form, dropping an all-zero imaginary component."""
        payload: dict[str, Any] = {"label": self.label, "z": self.z, "rho": self.rho}
        if any(v for v in self.irho):
            payload["irho"] = self.irho
        if self.source:
            payload["source"] = self.source
        if self.stride > 1:
            payload["stride"] = self.stride
            payload["n_source"] = self.n_source
        return payload


def read_profile_dat(
    path: Path,
    *,
    label: str,
    root: Path | None = None,
    max_spacing: float = PROFILE_MAX_SPACING,
) -> Profile:
    """Read a bumps ``-profile.dat`` export, thinned for display.

    Args:
        path: The file to read.
        label: Profile label to attach.
        root: Project root, for reporting a relative source path.
        max_spacing: Coarsest z spacing to keep, in angstroms. Pass ``0`` to
            read every point, which is what a numerical consumer should do.

    Returns:
        The profile, with ``stride`` recording any thinning applied.

    Raises:
        DataFormatError: If the file lacks the z, rho and irho columns.
    """
    path = Path(path)
    try:
        data = np.loadtxt(path, ndmin=2)
    except (ValueError, OSError) as exc:
        raise DataFormatError(f"Cannot read {path}: {exc}") from exc

    if data.size == 0 or data.shape[1] < len(PROFILE_COLUMNS):
        found = data.shape[1] if data.size else 0
        raise DataFormatError(
            f"{path} has {found} column(s); a bumps -profile.dat has "
            f"{len(PROFILE_COLUMNS)}: {', '.join(PROFILE_COLUMNS)}."
        )

    n_source = int(data.shape[0])
    stride = _stride_for(data[:, 0], max_spacing)
    if stride > 1:
        # Keep the final point regardless of where the stride lands, so the
        # profile still reaches the substrate rather than stopping short.
        kept = np.unique(np.append(np.arange(0, n_source, stride), n_source - 1))
        data = data[kept]

    return Profile(
        label=label,
        z=_clean(data[:, 0]),
        rho=_clean(data[:, 1]),
        irho=_clean(data[:, 2]),
        source=_relative(path, root),
        stride=stride,
        n_source=n_source,
    )


def _stride_for(z: np.ndarray, max_spacing: float) -> int:
    """Return the sampling stride that brings z spacing up to ``max_spacing``."""
    if max_spacing <= 0 or z.size < 3:
        return 1
    spacing = float(np.median(np.diff(z)))
    if not math.isfinite(spacing) or spacing <= 0 or spacing >= max_spacing:
        return 1
    return max(1, int(max_spacing // spacing))


#: Column order written by :func:`nr_workbench.tnr.ascii.write_amplitude_ascii`.
AMPLITUDE_COLUMNS = (
    "time_s",
    "a",
    "sigma_a",
    "significance",
    "chi2_res",
    "n_q",
    "interval_type",
    "label",
)


@dataclass
class AmplitudeSeries:
    """The change-amplitude trajectory a(t) from a tNR assessment.

    Attributes:
        times: Interval start times in seconds.
        a: Change amplitude, 0 at the reference block and 1 at the late block.
        sigma: Uncertainty on ``a``.
        significance: ``a / sigma``.
        chi2_res: Residual chi-squared per interval; ~1 means one template
            describes the change.
        types: Interval type per point, e.g. ``hold`` or ``eis``.
        labels: Interval label per point.
        header: The commented provenance lines from the file.
    """

    times: list[float] = field(default_factory=list)
    a: list[float | None] = field(default_factory=list)
    sigma: list[float | None] = field(default_factory=list)
    significance: list[float | None] = field(default_factory=list)
    chi2_res: list[float | None] = field(default_factory=list)
    types: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    header: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """Return the JSON form."""
        return {
            "times": self.times,
            "a": self.a,
            "sigma": self.sigma,
            "significance": self.significance,
            "chi2_res": self.chi2_res,
            "types": self.types,
            "labels": self.labels,
            "header": self.header,
        }


def read_amplitude_txt(path: Path) -> AmplitudeSeries:
    """Read the a(t) trajectory written by ``nrw tnr assess``.

    ``assessment.json`` records summary statistics only -- the range, the
    trajectory shape, the peak significance -- because those are what a verdict
    needs. The per-interval series lives here, in the same tab-separated file
    the scientist reads, so a plot cannot disagree with the numbers they have
    already looked at.

    Args:
        path: Path to ``amplitude.txt``.

    Returns:
        The series. Commented ``# key: value`` lines become ``header``.

    Raises:
        DataFormatError: If a data line has too few fields.
    """
    path = Path(path)
    series = AmplitudeSeries()

    for number, raw in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            body = line.lstrip("#").strip()
            if ":" in body:
                key, _, value = body.partition(":")
                key = key.strip()
                # The column header line also contains a colon-free tab list;
                # only single-word keys are metadata.
                if key and " " not in key:
                    series.header[key] = value.strip()
            continue

        fields = line.split("\t")
        if len(fields) < len(AMPLITUDE_COLUMNS):
            raise DataFormatError(
                f"{path}:{number} has {len(fields)} field(s); "
                f"expected {len(AMPLITUDE_COLUMNS)}: "
                f"{', '.join(AMPLITUDE_COLUMNS)}."
            )
        series.times.append(_as_float(fields[0]) or 0.0)
        series.a.append(_as_float(fields[1]))
        series.sigma.append(_as_float(fields[2]))
        series.significance.append(_as_float(fields[3]))
        series.chi2_res.append(_as_float(fields[4]))
        series.types.append(fields[6])
        series.labels.append(fields[7])

    return series


def _as_float(text: str) -> float | None:
    """Parse a float, returning ``None`` for anything non-finite or unparsable."""
    try:
        value = float(text)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def _relative(path: Path, root: Path | None) -> str:
    """Render a path relative to the project root where possible."""
    if root is None:
        return path.as_posix()
    try:
        return path.resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return path.as_posix()
