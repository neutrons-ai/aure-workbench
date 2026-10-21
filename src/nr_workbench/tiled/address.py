"""Which Tiled server nrw talks to, and what an address there may be.

**Credentials go to one server: the person's ``TILED_URL``.** ``nrw.toml`` is
committed with the project, so its addresses may be written by anyone who can
commit there; one naming another host would have the person's login sent to
it -- the lesson of the ISAAC key (``docs/ground_truths.md``, 2026-09-30). So
``TILED_URL`` is a setting only the person makes
(:func:`nr_workbench.env.person_setting`), and an address on any other server
is refused (:func:`address_problem`).

**An address is checked as written, whole.** ``urlsplit`` drops a tab or a
line break anywhere in a URL, and older Pythons take the host of
``evil.example[tiled.ornl.gov]`` from inside the brackets; so an address is
held to printable ASCII, and its host and port to a pattern of their own,
before anything ``urlsplit`` makes of it is believed.

**An address names its experiment as ``{ipts}``.** Being on the person's
server is not enough: one naming another experiment, or a container above
every experiment, would read or write it with the person's permissions.

**No address is printed whole.** A user name, password, query or fragment may
be a secret -- Tiled takes a key as ``?api_key=`` -- and a reason is shown on
pages anyone logged in to the machine can read. :func:`shown` is how an
address is printed.

Nothing here imports an HTTP client: ``nrw --help`` must stay instant, and
nearly every command reads the settings that use this.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import SplitResult, urlsplit

from nr_workbench.project.config import DEFAULT_TILED_URL, IPTS_PLACEHOLDER

#: The server nrw talks to unless the person's ``TILED_URL`` names another.
DEFAULT_URL = DEFAULT_TILED_URL

#: One label of a host name.
_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"

#: The host and port, whole: a name or a bracketed IPv6 address, and a port.
_NETLOC = re.compile(
    rf"(?:{_LABEL}(?:\.{_LABEL})*|\[[0-9a-f:.]+\])(?::[0-9]{{1,5}})?",
    re.ASCII | re.IGNORECASE,
)

#: One segment of an address's path: the characters a node's key holds there.
_SEGMENT = re.compile(r"[A-Za-z0-9._-]+", re.ASCII)

#: An IPTS written into an address, which only ``{ipts}`` may stand for.
_IPTS_NAMED = re.compile(r"IPTS-[0-9]+", re.ASCII | re.IGNORECASE)

#: The port each scheme reaches when an address gives none.
_DEFAULT_PORTS = {"https": 443, "http": 80}

#: What :func:`shown` prints in place of a part it does not show.
_HIDDEN = "\N{HORIZONTAL ELLIPSIS}"

#: The line breaks a reason names as such: CR, LF and Unicode's others.
_LINE_BREAKS = "".join(map(chr, (0x0A, 0x0D, 0x85, 0x2028, 0x2029)))


@dataclass(frozen=True)
class Server:
    """The person's Tiled server, as nrw found it.

    Attributes:
        url: ``TILED_URL`` without a final ``/``, or :data:`DEFAULT_URL`.
        source: Where that came from, as a person reads it.
        problem: Why nothing may be sent to it -- its address, or where it
            was set -- or ``None``.
    """

    url: str
    source: str
    problem: str | None = None


def server(start: Path | None = None) -> Server:
    """The person's Tiled server: ``TILED_URL``, as only the person sets it.

    The shell, ``~/.nrw`` or ``~/.aure`` -- never the project's files -- and
    refused for any reason :func:`nr_workbench.env.person_setting` gives, or
    when it is no server nrw logs in to (:func:`server_problem`).

    Args:
        start: The project, whose files are left out.
    """
    from nr_workbench.env import person_setting, shown_source

    setting, problem = person_setting("TILED_URL", start)
    url = setting.value.strip() if setting is not None else DEFAULT_URL
    source = shown_source(setting.source) if setting is not None else "nrw's default"
    problem = problem or server_problem(url)
    if problem is None and url.endswith("/"):
        url = url[:-1]
    return Server(url=url, source=source, problem=problem)


def server_problem(url: str) -> str | None:
    """Why *url* cannot be the Tiled server nrw logs in to, or ``None``.

    The rules of every address, and no path: Tiled's API is at the server's
    root. A final ``/`` alone is allowed, and dropped by :func:`server`.
    """
    problem = _address_problem(url, "TILED_URL")
    if problem is None and urlsplit(url).path not in ("", "/"):
        problem = (
            f"TILED_URL {shown(url)} has a path; give the server alone, such as "
            f"{DEFAULT_URL}."
        )
    return problem


def address_problem(url: str, server: Server, name: str) -> str | None:
    """Why *url* cannot be a place nrw reads or writes on Tiled, or ``None``.

    The first reason there is, in this order: the address's own rules (an
    ``https://`` address, or plain ``http://`` on this machine's loopback for
    tests; no user name, query or fragment; a path of segments of letters,
    digits, ``.``, ``_`` and ``-``); the person's server; that the address is
    on it; and that it names its experiment as ``{ipts}``, and no other.
    Never raises.

    Args:
        url: As written, ``{ipts}`` unfilled.
        server: The person's server, from :func:`server`.
        name: What to call the address in the reason.
    """
    return (
        _location_problem(url, name)
        or server.problem
        or _host_problem(url, server, name)
        or _ipts_problem(url, name)
    )


def resolved(url: str, ipts: str | None) -> str:
    """*url* with the project's IPTS in place of ``{ipts}``.

    Raises:
        ValueError: The address needs an IPTS, and *ipts* is not one as
            ``normalize_ipts`` makes it. The rules were checked on the address
            as written, and only an IPTS of that shape keeps it within them.
    """
    from nr_workbench.project.experiment_schema import normalize_ipts

    if IPTS_PLACEHOLDER not in url:
        return url
    if ipts is None or normalize_ipts(ipts) != ipts:
        raise ValueError(f"{ipts!r} is not an IPTS nrw fills into an address")
    return url.replace(IPTS_PLACEHOLDER, ipts)


def shown(url: str) -> str:
    """An address as nrw prints it: never a user name, password, query or fragment.

    Its host and port only when they are a host and a port, and its path when
    it is plain segments, as a valid one is; any other part is shown as
    ``…``. An address nrw cannot read is not shown at all.
    """
    if not _printable(url):
        return "(not shown)"
    try:
        parts = urlsplit(url)
    except ValueError:
        return "(not shown)"
    if parts.scheme not in _DEFAULT_PORTS or not parts.netloc:
        return "(not shown)"
    login = f"{_HIDDEN}@" if "@" in parts.netloc else ""
    host = parts.netloc.rpartition("@")[2]
    if not _NETLOC.fullmatch(host):
        host = _HIDDEN
    plain = parts.path in ("", "/") or _plain(parts.path, final_slash=True)
    path = parts.path if plain else f"/{_HIDDEN}"
    return f"{parts.scheme}://{login}{host}{path}"


def lazily(start: Path | None) -> Callable[[], Server]:
    """:func:`server` for *start*, looked up the first time it is called.

    Only an address on Tiled needs the server, and looking it up reads the
    person's files.
    """
    found: list[Server] = []

    def lookup() -> Server:
        if not found:
            found.append(server(start))
        return found[0]

    return lookup


def _address_problem(url: str, name: str) -> str | None:
    """Why *url* cannot be an address on any Tiled server, or ``None``."""
    bad = next((i for i, c in enumerate(url) if not "!" <= c <= "~"), None)
    if bad is not None:
        return (
            f"{name} holds {_character(url[bad])} at position {bad + 1}; an "
            "address is printable ASCII, with no spaces."
        )
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return f"{name} is not an address nrw can read; check its host and port."
    if parts.scheme not in _DEFAULT_PORTS or not parts.netloc:
        return f"{name} is not an https:// address."
    if "@" in parts.netloc:
        return (
            f"{name} {shown(url)} holds a user name or a password (not shown). A "
            "Tiled login is made with `nrw tiled login`, never written into an "
            "address."
        )
    if "?" in url or "#" in url:
        return (
            f"{name} {shown(url)} has a query or a fragment (not shown); give "
            "the address alone."
        )
    if not _NETLOC.fullmatch(parts.netloc) or (
        parts.netloc.startswith("[") and _ip(parts.hostname or "") is None
    ):
        return (
            f"{name} {shown(url)} does not name a host: give a name such as "
            "tiled.ornl.gov, or an IP address, and a port if it needs one."
        )
    if port == 0:
        return f"{name} {shown(url)} gives port 0, which no server listens on."
    if parts.scheme == "http" and not _loopback(parts.hostname or ""):
        return (
            f"{name} {shown(url)} is plain http:// to another machine, which "
            "would send a login across the network unencrypted. Use https://."
        )
    return None


def _location_problem(url: str, name: str) -> str | None:
    """Why *url* cannot be a place on a Tiled server, or ``None``."""
    problem = _address_problem(url, name)
    if problem is not None:
        return problem
    path = urlsplit(url).path
    if _plain(path, final_slash=True) and path.endswith("/") and path != "/":
        return f"{name} {shown(url)} ends with '/'; give it without."
    if not _plain(path):
        return (
            f"{name} {shown(url)}: its path must be one or more segments of "
            "letters, digits, '.', '_' and '-', such as "
            f"/projects/isaac/{IPTS_PLACEHOLDER}/context."
        )
    return None


def _host_problem(url: str, server: Server, name: str) -> str | None:
    """Why *url* must not be sent the login for *server*, or ``None``."""
    try:
        here = _origin(urlsplit(url))
        same = here == _origin(urlsplit(server.url))
        on_default = here == _origin(urlsplit(DEFAULT_URL))
    except ValueError:  # both were checked already; never a crash regardless
        same = on_default = False
    if same:
        return None
    if on_default:
        # nrw's own default address, or one like it: nobody chose another host.
        return (
            f"{name} {shown(url)} is on {DEFAULT_URL}, and your TILED_URL is "
            f"{shown(server.url)} ({server.source}). Give the address on that "
            f"server, or remove TILED_URL to use {DEFAULT_URL}."
        )
    return (
        f"{name} {shown(url)} is not on your Tiled server, {shown(server.url)} "
        f"({server.source}), and nrw sends your Tiled login there only. nrw.toml "
        "is committed with the project, so whoever can commit to it may have "
        "chosen this address: change TILED_URL only for a server you chose "
        "yourself."
    )


def _ipts_problem(url: str, name: str) -> str | None:
    """Why *url* does not name the project's experiment, and only it, or ``None``.

    The experiment is ``{ipts}``, a segment of its own, filled in from
    ``[beamtime] ipts`` -- whose change is asked about first -- and never
    written in: an IPTS written in would stay behind when the project's
    changed, and could be another's.
    """
    path = urlsplit(url).path
    named = _IPTS_NAMED.search(path)
    if named is not None:
        return (
            f"{name} {shown(url)} names {named.group(0)} itself. Write "
            f"{IPTS_PLACEHOLDER} where the IPTS goes: the address then follows "
            "[beamtime] ipts, and names no other experiment."
        )
    if IPTS_PLACEHOLDER not in path.split("/"):
        return (
            f"{name} {shown(url)} does not name the project's experiment: give "
            f"{IPTS_PLACEHOLDER} as a segment of its own, where the IPTS goes, "
            f"such as /projects/isaac/{IPTS_PLACEHOLDER}/context."
        )
    return None


def _origin(parts: SplitResult) -> tuple[str, str, int | None]:
    """Scheme, host and port: what decides which server a request reaches."""
    port = parts.port
    if port is None:
        port = _DEFAULT_PORTS.get(parts.scheme)
    return (parts.scheme, (parts.hostname or "").casefold(), port)


def _plain(path: str, *, final_slash: bool = False) -> bool:
    """Whether *path* is ``/`` and one or more plain segments, ``{ipts}`` allowed."""
    segments = path.replace(IPTS_PLACEHOLDER, "IPTS-0").split("/")
    if final_slash and len(segments) > 2 and segments[-1] == "":
        segments = segments[:-1]
    return (
        len(segments) >= 2
        and segments[0] == ""
        and all(_SEGMENT.fullmatch(s) and s not in (".", "..") for s in segments[1:])
    )


def _printable(text: str) -> bool:
    return all("!" <= c <= "~" for c in text)


def _character(char: str) -> str:
    """A character as a reason names it, without printing it."""
    if char == " ":
        return "a space"
    if char == "\t":
        return "a tab"
    if char in _LINE_BREAKS:
        return "a line break"
    return f"the character U+{ord(char):04X}"


def _ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def _loopback(host: str) -> bool:
    """Whether *host* is this machine, as a test server would be."""
    address = _ip(host)
    return host.casefold() == "localhost" or bool(address and address.is_loopback)
