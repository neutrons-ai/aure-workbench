#!/usr/bin/env python3
"""Report drift between vendored files and the upstream they came from.

Nothing here writes to the source tree. Drift is a thing to look at and decide
about, not to resolve automatically -- the whole reason `upstream.toml`
distinguishes *verbatim* from *adapted* is that the right response differs:

* **verbatim** is a shared contract. If it changed upstream, we take the change
  or we consciously fork. If it changed *locally*, that is a bug --
  ``tests/test_upstream.py::test_the_repos_own_manifest_is_clean`` already
  fails on it, offline.
* **adapted** is code we deliberately changed. Upstream moving is information,
  not an instruction: the adaptation notes in ``upstream.toml`` say what we did
  and why, and that reasoning is what decides whether to follow.

Run with no network access and it still checks local hashes, which is the half
that catches accidental edits.

Usage:
    python tools/sync_upstream.py             # local check only
    python tools/sync_upstream.py --remote    # also ask GitHub what changed
    python tools/sync_upstream.py --json      # machine-readable
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "upstream.toml"

#: GitHub's API. Unauthenticated is rate-limited to 60/hour, which is ample for
#: a handful of files on a nightly job.
API = "https://api.github.com/repos/{repo}/commits?path={path}&per_page=1"

#: Seconds to wait on the network before giving up on one file.
TIMEOUT = 15


@dataclass
class Finding:
    """One thing worth a human's attention.

    Attributes:
        dest: The vendored path, relative to the repo root.
        kind: ``verbatim`` or ``adapted``.
        problem: What is wrong, as a short slug.
        detail: The explanation.
    """

    dest: str
    kind: str
    problem: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        """Return the JSON form."""
        return {
            "dest": self.dest,
            "kind": self.kind,
            "problem": self.problem,
            "detail": self.detail,
        }


@dataclass
class Report:
    """Everything the check found.

    Attributes:
        checked: How many entries were examined.
        findings: Problems found, worst first by category.
        skipped: Entries that could not be checked, and why.
    """

    checked: int = 0
    #: Entries whose bytes were compared against a recorded sha256. An entry
    #: without one is visited and not verified, and saying "no drift" over those
    #: reads as a guarantee nobody made -- five of six skills were unregistered
    #: here while the report said it had checked everything.
    hash_verified: int = 0
    #: Entries whose upstream commit was compared against the recorded one.
    #: Zero unless --remote, which is the only check an adapted file can have:
    #: its content is deliberately different, so a hash would assert the wrong
    #: thing.
    commit_verified: int = 0
    findings: list[Finding] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Whether nothing needs attention."""
        return not self.findings

    def as_dict(self) -> dict[str, Any]:
        """Return the JSON form."""
        return {
            "schema": "nrw-upstream-drift/1",
            "checked": self.checked,
            "hash_verified": self.hash_verified,
            "commit_verified": self.commit_verified,
            "ok": self.ok,
            "findings": [f.as_dict() for f in self.findings],
            "skipped": self.skipped,
        }


def sha256(path: Path) -> str:
    """Hash a file's bytes."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def load_manifest(path: Path | None = None) -> dict[str, Any]:
    """Read ``upstream.toml``.

    Args:
        path: The manifest to read. Defaults to the repo's own, resolved at
            call time rather than bound as a default argument -- a default
            captured at import cannot be overridden, which makes the module
            untestable and silently ignores any override a caller passes.

    Returns:
        The parsed document.

    Raises:
        FileNotFoundError: If the manifest is missing.
    """
    resolved = Path(path) if path is not None else MANIFEST
    if not resolved.is_file():
        raise FileNotFoundError(f"No manifest at {resolved}")
    return tomllib.loads(resolved.read_text(encoding="utf-8"))


def latest_commit(repo: str, path: str) -> str | None:
    """Ask GitHub for the most recent commit touching a path.

    Args:
        repo: ``owner/name``.
        path: Path within that repository.

    Returns:
        The commit SHA, or ``None`` if it could not be determined.
    """
    url = API.format(repo=repo, path=urllib.parse.quote(path))
    request = urllib.request.Request(  # noqa: S310 - fixed https API host
        url, headers={"Accept": "application/vnd.github+json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None
    if not isinstance(payload, list) or not payload:
        return None
    sha = payload[0].get("sha")
    return str(sha) if sha else None


def _sources(entry: dict[str, Any]) -> list[tuple[str, str, str]]:
    """Enumerate an entry's upstream sources as ``(repo, path, commit)``.

    Most entries name one source. A few came from more than one -- the
    reflectometry overview skill was assembled from an AuRE skill and a
    experiments-2025 note -- and those use the plural ``repos``/``paths``/
    ``commits`` keys. Both spellings are flattened here so callers see a list.
    """
    if "repos" in entry or "paths" in entry:
        repos = [str(r) for r in entry.get("repos", [])]
        paths = [str(p) for p in entry.get("paths", [])]
        commits = [str(c) for c in entry.get("commits", [])]
        # The keys mix: one entry has several `paths` from a single `repo`,
        # another has parallel `repos` and `paths`. Pair by position, falling
        # back to the singular key when the plural one is short or absent.
        single_repo = str(entry.get("repo", ""))
        single_commit = str(entry.get("commit", ""))
        pairs = []
        for index, path in enumerate(paths):
            if index < len(repos):
                repo = repos[index]
            elif len(repos) == 1:
                repo = repos[0]
            else:
                repo = single_repo
            if index < len(commits):
                commit = commits[index]
            elif len(commits) == 1:
                commit = commits[0]
            else:
                commit = single_commit
            pairs.append((repo, path, commit))
        return pairs

    return [
        (
            str(entry.get("repo", "")),
            str(entry.get("path", "")),
            str(entry.get("commit", "")),
        )
    ]


def check(*, remote: bool = False, manifest_path: Path | None = None) -> Report:
    """Check every vendored file against its record.

    Args:
        remote: Also query GitHub for upstream commits.
        manifest_path: The manifest to check against. Defaults to the repo's
            own, resolved at call time.

    Returns:
        What was found.
    """
    resolved = Path(manifest_path) if manifest_path is not None else MANIFEST
    document = load_manifest(resolved)
    report = Report()
    root = resolved.parent

    for kind in ("verbatim", "adapted"):
        for entry in document.get(kind, []):
            dest = str(entry.get("dest", ""))
            report.checked += 1
            target = root / dest

            # Two destinations are legitimately not single files: a directory,
            # when the adaptation was a decomposition (tnr_chi2.py -> tnr/),
            # and a glob, when one upstream source became several files
            # (docs/tnr-*.md -> one skill each). Both are still worth checking
            # for existence -- a fan-out that vendored nothing is a real bug.
            if "*" in dest or "?" in dest:
                matches = sorted(root.glob(dest))
                if not matches:
                    report.findings.append(
                        Finding(dest, kind, "missing", "pattern matches nothing")
                    )
                    continue
                report.skipped.append(
                    f"{dest} ({len(matches)} match(es), no single hash)"
                )
            elif dest.endswith("/") or target.is_dir():
                if not target.is_dir():
                    report.findings.append(
                        Finding(dest, kind, "missing", "directory not on disk")
                    )
                    continue
                report.skipped.append(f"{dest} (a directory, no single hash)")
            elif not target.exists():
                report.findings.append(
                    Finding(dest, kind, "missing", "recorded here but not on disk")
                )
                continue
            elif recorded := entry.get("sha256"):
                report.hash_verified += 1
                actual = sha256(target)
                if actual != recorded:
                    report.findings.append(
                        Finding(
                            dest,
                            kind,
                            "local-edit",
                            f"sha256 {actual[:12]} != recorded {str(recorded)[:12]}"
                            + (
                                " -- a verbatim copy must never be edited here"
                                if kind == "verbatim"
                                else " -- update sha256 in upstream.toml"
                            ),
                        )
                    )
            elif kind == "verbatim":
                report.findings.append(
                    Finding(dest, kind, "no-hash", "verbatim entries need a sha256")
                )

            if not remote:
                continue

            for repo, path, recorded_commit in _sources(entry):
                if not repo or not path:
                    report.skipped.append(f"{dest} (no repo/path to query)")
                    continue
                if not recorded_commit:
                    report.skipped.append(
                        f"{dest} <- {repo}:{path} (no commit recorded)"
                    )
                    continue

                upstream = latest_commit(repo, path)
                if upstream is None:
                    report.skipped.append(f"{dest} <- {repo} (unreachable)")
                    continue
                report.commit_verified += 1
                if upstream != recorded_commit:
                    report.findings.append(
                        Finding(
                            dest,
                            kind,
                            "upstream-moved",
                            f"{repo}:{path} is now at {upstream[:12]}, "
                            f"vendored from {recorded_commit[:12]}",
                        )
                    )

    order = {"missing": 0, "local-edit": 1, "no-hash": 2, "upstream-moved": 3}
    report.findings.sort(key=lambda f: (order.get(f.problem, 9), f.dest))
    return report


def main(argv: list[str] | None = None) -> int:
    """Run the check from the command line.

    Args:
        argv: Arguments, defaulting to ``sys.argv``.

    Returns:
        0 when nothing needs attention, 1 otherwise.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--remote", action="store_true", help="Query GitHub for upstream commits."
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON.")
    args = parser.parse_args(argv)

    report = check(remote=args.remote)

    if args.json:
        print(json.dumps(report.as_dict(), indent=2))
        return 0 if report.ok else 1

    unverified = report.checked - report.hash_verified
    print(f"  {report.checked} vendored entr(ies) registered")
    print(f"    {report.hash_verified} verified by content hash")
    if unverified:
        print(
            f"    {unverified} recorded for provenance only -- adapted files are"
        )
        print(
            "      deliberately different from upstream, so a hash would assert"
        )
        print("      the wrong thing. Use --remote to check the commit instead.")
    if args.remote:
        print(f"    {report.commit_verified} checked against upstream's commit")
    for note in report.skipped:
        print(f"    - skipped {note}")
    if report.ok:
        print(
            "  no drift"
            if report.hash_verified or report.commit_verified
            else "  nothing to compare -- provenance recorded, no content checked"
        )
        return 0

    print()
    for finding in report.findings:
        print(f"  ! [{finding.problem}] {finding.dest}")
        print(f"      {finding.detail}")
    print(
        "\n  Nothing was changed. Decide per file: for `verbatim`, take the\n"
        "  upstream change or fork consciously; for `adapted`, re-read the\n"
        "  adaptation note in upstream.toml before following."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
