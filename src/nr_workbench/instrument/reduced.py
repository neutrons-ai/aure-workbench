"""What a REF_L reduced file is called, in one place.

Filename conventions used to be spelled out in five modules -- ``project/scan``,
``spec/resolve``, ``commands/model``, ``instrument/header`` and AuRE's own
instrument registry -- none of which knew the others existed. When
``_autoreduction.dat`` arrived, ``scan`` was taught about it first, which made
the files visible and let them flow straight into a ``nrw model new`` that
wrote ``dq_is_fwhm: true`` for a file whose header says ``sigma``. **Making one
layer smarter made the failure less visible, not more.** This module is the
remedy: one place that knows what these files are called, and one place that
decides what they are.

Two questions, answered differently on purpose:

**What is this file called?** Local, from the patterns below. The run, segment
and subrun come out of the name, and nothing else on disk carries the segment
number for the ``new_reduction`` dialect -- its header describes the whole run
and is byte-identical in every one of that run's files.

**What is this file?** :func:`role` asks **AuRE**, which is the tool that will
actually build the fit. If AuRE says a state's files are one complete curve
and nrw says they are angle segments, AuRE's view is the one the fit obeys, so
a setup written on nrw's view is wrong whatever nrw believes. That is not
hypothetical: before AuRE learned this dialect, nrw called
``_autoreduction.dat`` files partials and AuRE called them combined, and the
mismatch cost a beamtime. :func:`disagreement` exists to surface exactly that,
rather than let one side win quietly.

AuRE is a hard dependency, but every import of it in this package is guarded
(see ``aure_adapter``) because a broken or absent install must degrade rather
than crash. The fallback here is the local patterns, which is what nrw used
before it could ask.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

#: The complete curve: every segment spliced into one file.
COMBINED_RE = re.compile(r"^REFL_(?P<run>\d+)_combined_data_auto\.txt$")

#: Per-segment files, in both reduction dialects.
#:
#: ``_partial.txt`` is the established reduction (REF_L v1 upstream);
#: ``_autoreduction.dat`` comes from the ``new_reduction`` pipeline (REF_L v2).
#: Same ``run / seg / subrun`` scheme, different suffix *and* extension -- and
#: the two disagree about what the fourth column means (FWHM vs sigma), which
#: is why that is read from each file's header and never inferred from a name.
PARTIAL_RE = re.compile(
    r"^REFL_(?P<run>\d+)_(?P<seg>\d+)_(?P<subrun>\d+)"
    r"_(?P<dialect>partial\.txt|autoreduction\.dat)$"
)

#: Per-dialect suffixes, in the order a glob should try them.
_SEGMENT_SUFFIXES: tuple[str, ...] = ("_partial.txt", "_autoreduction.dat")

#: Extensions a reduced steady-state file may have.
#:
#: Checked before the patterns, so anything else is skipped without reaching
#: them and without being reported as unreadable. That is how a directory of
#: three valid ``.dat`` files came to look like an empty one: the extension
#: filter dropped them silently and the scan said "no data found", naming
#: neither the files nor the extension as the reason.
STEADY_SUFFIXES = frozenset({".txt", ".dat"})

#: Dialect names, matching the instrument names AuRE registers.
PARTIAL_DIALECT = "partial"
AUTOREDUCTION_DIALECT = "autoreduction"


@dataclass(frozen=True)
class ReducedName:
    """What a per-segment filename says about itself.

    Attributes:
        run: The measurement the segment belongs to.
        segment: 1-based segment index, and the only place it is recorded for
            the ``new_reduction`` dialect.
        subrun: The run that produced this particular segment. Usually but not
            always ``run + segment - 1``.
        dialect: :data:`PARTIAL_DIALECT` or :data:`AUTOREDUCTION_DIALECT`.
    """

    run: int
    segment: int
    subrun: int
    dialect: str


def parse_segment_name(name: str) -> ReducedName | None:
    """Read a per-segment filename, or ``None`` if it is not one."""
    match = PARTIAL_RE.match(Path(name).name)
    if match is None:
        return None
    return ReducedName(
        run=int(match.group("run")),
        segment=int(match.group("seg")),
        subrun=int(match.group("subrun")),
        dialect=(
            AUTOREDUCTION_DIALECT
            if match.group("dialect").endswith(".dat")
            else PARTIAL_DIALECT
        ),
    )


def parse_combined_name(name: str) -> int | None:
    """The run a combined-curve filename names, or ``None``."""
    match = COMBINED_RE.match(Path(name).name)
    return int(match.group("run")) if match else None


# ---------------------------------------------------------------------------
# Names about to be *written* -- stricter than names being read
# ---------------------------------------------------------------------------

#: The longest filename most filesystems accept, in bytes.
MAX_NAME_BYTES = 255


def segment_filename(run: int, segment: int, subrun: int, dialect: str) -> str:
    """The exact filename the reduction writes for one segment.

    Args:
        run: The measurement.
        segment: 1-based segment index.
        subrun: The run that produced this segment.
        dialect: :data:`PARTIAL_DIALECT` or :data:`AUTOREDUCTION_DIALECT`.

    Returns:
        The canonical name, e.g. ``REFL_218386_2_218387_partial.txt``.

    Raises:
        ValueError: If the dialect is not one this module knows.
    """
    suffixes = {
        PARTIAL_DIALECT: _SEGMENT_SUFFIXES[0],
        AUTOREDUCTION_DIALECT: _SEGMENT_SUFFIXES[1],
    }
    if dialect not in suffixes:
        raise ValueError(f"unknown reduction dialect {dialect!r}")
    return f"REFL_{run}_{segment}_{subrun}{suffixes[dialect]}"


def combined_filename(run: int) -> str:
    """The exact filename the reduction writes for a combined curve."""
    return f"REFL_{run}_combined_data_auto.txt"


def canonical_name(name: str) -> ReducedName | int | None:
    """Parse *name* only if it is exactly a name the reduction would write.

    The patterns above are for *reading* and are deliberately forgiving: `$`
    also matches before a trailing newline, and ``\\d`` accepts any Unicode
    digit, which ``int()`` then quietly converts. That is harmless when
    classifying files already on disk. It is not harmless for a name that is
    about to become a path inside a project -- copied from a shared facility
    folder that anyone on the team can write to, or later from a remote
    source. So a name is accepted here only if rebuilding it from the parsed
    integers reproduces it byte for byte, which rules out trailing newlines,
    non-ASCII digits, leading zeros, separators, and anything else a pattern
    match would let through.

    Args:
        name: A bare filename. A path is rejected rather than reduced to its
            final component, because the caller is about to write it.

    Returns:
        The parsed segment, the run of a combined curve, or ``None`` when the
        name is not exactly canonical.
    """
    if not name or "\x00" in name or len(name.encode("utf-8")) > MAX_NAME_BYTES:
        return None
    if Path(name).name != name or "/" in name or "\\" in name:
        return None

    segment = parse_segment_name(name)
    if segment is not None:
        rebuilt = segment_filename(
            segment.run, segment.segment, segment.subrun, segment.dialect
        )
        return segment if rebuilt == name else None

    run = parse_combined_name(name)
    if run is not None:
        return run if combined_filename(run) == name else None
    return None


def segment_globs(run: int | str, segment: int | str = "*") -> list[str]:
    """Glob patterns matching one run's segment files, in both dialects.

    Enumeration is the one thing AuRE cannot be asked for -- it classifies a
    path it is given, it does not search a directory -- so the patterns live
    here and every caller that searches uses this rather than writing its own.

    Args:
        run: The measurement.
        segment: A specific segment, or ``"*"`` for all of them.

    Returns:
        Patterns to try, in order. Callers should report **all** of them when
        nothing matches: naming only one sends someone looking for a file
        under a name their reduction never writes.
    """
    return [f"REFL_{run}_{segment}_*{suffix}" for suffix in _SEGMENT_SUFFIXES]


def find_segments(
    directory: Path, run: int | str, segment: int | str = "*"
) -> list[Path]:
    """Every segment file for *run* in *directory*, both dialects, sorted."""
    return sorted(
        match
        for pattern in segment_globs(run, segment)
        for match in Path(directory).glob(pattern)
    )


# ---------------------------------------------------------------------------
# What a file *is* -- asked of AuRE, which is what builds the fit
# ---------------------------------------------------------------------------


def _aure_roles() -> tuple | None:
    """AuRE's ``(instruments, PARTIAL, COMBINED)``, or ``None`` if unavailable."""
    try:
        from aure import instruments

        return (instruments, instruments.PARTIAL, instruments.COMBINED)
    except Exception:
        return None


def role(path: str | Path) -> str:
    """``"partial"``, ``"combined"`` or ``"unknown"`` for *path*.

    Asks AuRE, falling back to the local patterns when it cannot be imported.
    The names match AuRE's own role constants so the two can be compared
    directly.
    """
    resolved = _aure_roles()
    if resolved is not None:
        instruments, partial, combined = resolved
        try:
            answer = instruments.file_role(str(path))
            if answer in (partial, combined):
                return answer
        except Exception:
            pass
    return _local_role(path)


def _local_role(path: str | Path) -> str:
    """The answer from the patterns in this module alone."""
    name = Path(path).name
    if parse_segment_name(name) is not None:
        return "partial"
    if parse_combined_name(name) is not None:
        return "combined"
    return "unknown"


def disagreement(path: str | Path) -> str | None:
    """How AuRE and nrw differ about *path*, or ``None`` if they agree.

    Worth checking wherever a setup is about to be written. Two tools reading
    the same directory and reaching different conclusions about what is in it
    is the failure this module exists to prevent, and it is invisible from
    either side alone: each is internally consistent and simply wrong about
    what the other will do.

    Returns ``None`` when AuRE is unavailable -- an absent dependency is not a
    disagreement, and reporting one would send the reader after a phantom.
    """
    resolved = _aure_roles()
    if resolved is None:
        return None
    instruments, _, _ = resolved
    try:
        theirs = instruments.file_role(str(path))
        instrument = instruments.resolve_by_name(str(path)).name
    except Exception:
        return None
    ours = _local_role(path)
    if theirs == ours:
        return None
    return (
        f"{Path(path).name}: nrw reads this as {ours!r}, AuRE as {theirs!r} "
        f"(instrument {instrument!r}). AuRE's view is the one a fit obeys, so a "
        f"setup written on nrw's view would be wrong. Check that the installed "
        f"AuRE knows this format -- `aure formats` lists what it reads."
    )


def disagreements(paths: Iterable[str | Path]) -> list[str]:
    """:func:`disagreement` over several paths, empty when all agree."""
    return [msg for msg in (disagreement(p) for p in paths) if msg]
