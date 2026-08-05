"""Embed a fit identifier inside the files a fit produces.

A figure's provenance normally lives in its directory. But figures get dragged
into slide decks, emailed, and pasted into papers -- at which point the
directory is gone and the filename is ``Screenshot 2026-08-05.png``.

Stamping writes the fit id into the file itself, so ``nrw whence`` can still
answer for a copy that has left the project. Both formats used here carry
metadata natively:

* **SVG** -- a ``<desc>`` element, valid and ignored by every renderer.
* **PNG** -- a ``tEXt`` chunk, which is what matplotlib's ``metadata=`` writes.
"""

from __future__ import annotations

import re
import struct
import zlib
from pathlib import Path

#: The marker written into stamped files.
STAMP_PREFIX = "nrw-fit:"

#: Key used for the PNG tEXt chunk and the SVG desc line.
STAMP_KEY = "nrw-provenance"

_SVG_DESC_RE = re.compile(
    rf"<desc[^>]*>\s*{re.escape(STAMP_PREFIX)}([A-Za-z0-9\-]+)\s*</desc>", re.IGNORECASE
)
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def matplotlib_metadata(
    fit_id: str, *, nrw_version: str | None = None
) -> dict[str, str]:
    """Build the ``metadata=`` mapping for ``matplotlib.savefig``.

    Passing this at save time is cheaper and more reliable than rewriting the
    file afterwards, so it is the preferred path for figures we generate.

    Args:
        fit_id: The fit that produced the figure.
        nrw_version: Version string to record as the producing software.

    Returns:
        Mapping suitable for ``savefig(..., metadata=...)``. Keys work for both
        the PNG (``tEXt``) and SVG backends.
    """
    from nr_workbench import __version__

    return {
        "Title": f"{STAMP_PREFIX}{fit_id}",
        "Software": f"nr-workbench {nrw_version or __version__}",
        "Description": f"{STAMP_PREFIX}{fit_id}",
    }


def stamp_file(path: Path, fit_id: str) -> bool:
    """Stamp an existing file in place, if its format supports it.

    Args:
        path: The file to stamp.
        fit_id: The fit identifier to embed.

    Returns:
        True if a stamp was written, False for an unsupported format.
    """
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".svg":
        return _stamp_svg(path, fit_id)
    if suffix == ".png":
        return _stamp_png(path, fit_id)
    return False


def read_stamp(path: Path) -> str | None:
    """Read the fit identifier embedded in a file.

    Args:
        path: The file to inspect.

    Returns:
        The fit id, or ``None`` if the file is unstamped or unreadable.
    """
    path = Path(path)
    suffix = path.suffix.lower()
    try:
        if suffix == ".svg":
            return _read_svg_stamp(path)
        if suffix == ".png":
            return _read_png_stamp(path)
    except OSError:
        return None
    return None


def _stamp_svg(path: Path, fit_id: str) -> bool:
    """Insert a ``<desc>`` element carrying the stamp."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False

    if _SVG_DESC_RE.search(text):
        text = _SVG_DESC_RE.sub(f"<desc>{STAMP_PREFIX}{fit_id}</desc>", text, count=1)
        path.write_text(text, encoding="utf-8")
        return True

    match = re.search(r"<svg\b[^>]*>", text, re.IGNORECASE)
    if match is None:
        return False

    insertion = f"<desc>{STAMP_PREFIX}{fit_id}</desc>"
    stamped = text[: match.end()] + insertion + text[match.end() :]
    path.write_text(stamped, encoding="utf-8")
    return True


def _read_svg_stamp(path: Path) -> str | None:
    """Read a stamp from an SVG's ``<desc>`` element."""
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None
    match = _SVG_DESC_RE.search(text)
    if match:
        return match.group(1)
    loose = re.search(rf"{re.escape(STAMP_PREFIX)}([A-Za-z0-9\-]+)", text)
    return loose.group(1) if loose else None


def _stamp_png(path: Path, fit_id: str) -> bool:
    """Insert a ``tEXt`` chunk carrying the stamp.

    Written by hand rather than via Pillow: this package does not depend on
    Pillow, and a ``tEXt`` chunk is a length, a type, a payload and a CRC.
    """
    data = path.read_bytes()
    if not data.startswith(_PNG_MAGIC):
        return False

    payload = (
        STAMP_KEY.encode("latin-1")
        + b"\x00"
        + f"{STAMP_PREFIX}{fit_id}".encode("latin-1")
    )
    chunk = (
        struct.pack(">I", len(payload))
        + b"tEXt"
        + payload
        + struct.pack(">I", zlib.crc32(b"tEXt" + payload) & 0xFFFFFFFF)
    )

    # The chunk must follow IHDR, which is always the first chunk after the
    # signature and is always 13 bytes of data.
    ihdr_end = len(_PNG_MAGIC) + 8 + 13 + 4
    if len(data) < ihdr_end:
        return False

    path.write_bytes(data[:ihdr_end] + chunk + data[ihdr_end:])
    return True


def _read_png_stamp(path: Path) -> str | None:
    """Read a stamp from a PNG's ``tEXt`` chunks."""
    data = path.read_bytes()
    if not data.startswith(_PNG_MAGIC):
        return None

    offset = len(_PNG_MAGIC)
    while offset + 8 <= len(data):
        (length,) = struct.unpack(">I", data[offset : offset + 4])
        chunk_type = data[offset + 4 : offset + 8]
        body = data[offset + 8 : offset + 8 + length]
        if chunk_type == b"tEXt" and STAMP_PREFIX.encode("latin-1") in body:
            text = body.split(b"\x00", 1)[-1].decode("latin-1", errors="replace")
            marker = text.find(STAMP_PREFIX)
            if marker != -1:
                return text[marker + len(STAMP_PREFIX) :].strip() or None
        if chunk_type == b"IEND":
            break
        offset += 8 + length + 4
    return None


def stamp_directory(directory: Path, fit_id: str) -> list[Path]:
    """Stamp every stampable file in a directory tree.

    Args:
        directory: Directory to walk.
        fit_id: The fit identifier to embed.

    Returns:
        The files that were stamped.
    """
    stamped: list[Path] = []
    directory = Path(directory)
    if not directory.is_dir():
        return stamped
    for path in sorted(directory.rglob("*")):
        if path.is_file() and stamp_file(path, fit_id):
            stamped.append(path)
    return stamped
