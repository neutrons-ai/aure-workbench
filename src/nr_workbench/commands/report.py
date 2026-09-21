"""``nrw report`` -- scaffold a sample's closing narrative from its fit chain.

A sample ends up with a dozen immutable result directories, each with its own
note, and no file that says what the sequence was *for*. The individual notes
cannot supply that: each one is written looking forward, without knowing which
branch turned out to matter. So the record ends as a pile of fits where a reader
cannot tell the keeper from the control from the abandoned attempt --- and an
oxide-free control run deliberately as a negative test looks identical to one run
by mistake.

What this writes is the ordered chain plus the questions only the analyst can
answer. The table is generated because it is derivable and nobody should retype
it; the prose is left blank because it is the part that is actually the analysis.

Ordering comes from ``started_at`` in the fit index rather than from filenames or
mtimes: mtimes are destroyed by the first edit, and model names carry no ordinal.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import click

from nr_workbench.project.layout import ProjectLayout, ProjectNotFoundError
from nr_workbench.provenance.index import FitIndex
from nr_workbench.provenance.summary import annotate

#: A fit id is ``YYYYMMDD-HHMMSSZ-<hash>``, so its *leading* characters are the
#: date and identical for every fit of a session. The trailing hash is the part
#: that identifies one, and it is what `nrw note` and `nrw whence` accept.
_TRAILING_HASH = re.compile(r"-([0-9a-f]+)$")

#: How much of a note's first line to carry into the table.
NOTE_EXCERPT = 96

#: Fences the generated table so a refresh can replace it without touching prose.
_BEGIN = "<!-- nrw:sequence -->"
_END = "<!-- /nrw:sequence -->"

#: The sections each tier has to answer live in
#: :mod:`nr_workbench.reporting.tiers`. They are blank on purpose: this is the
#: analysis, and a generator that guessed at it would be inventing conclusions.


def _layout(root: str | None = None) -> ProjectLayout:
    """Discover the project, or fail with guidance."""
    try:
        return (
            ProjectLayout(root=Path(root).resolve())
            if root
            else ProjectLayout.discover()
        )
    except ProjectNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc


def _slug(text: str) -> str:
    """A filesystem-safe stem from a title."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:60] or "report"


def _short(fit_id: str) -> str:
    """The identifying tail of a fit id, which is what the CLI accepts."""
    match = _TRAILING_HASH.search(fit_id)
    return match.group(1) if match else fit_id


def _first_line(text: str) -> str:
    """The first line of a note that the analyst actually typed.

    The template is mostly scaffolding -- a heading, a quoted echo of the run,
    and multi-line HTML comments prompting each section -- and `nrw assess`
    appends a generated block. All of it has to go, and the comments have to be
    stripped as *spans* rather than as lines: a line inside a multi-line comment
    does not itself begin with a marker, so a line-wise filter reads the prompt
    text back out as if the scientist had written it.
    """
    # The generated block first, and by its fences -- stripping comments would
    # remove the very markers that identify it, after which its chi-squared line
    # reads exactly like a sentence someone wrote.
    without_generated = re.sub(
        r"<!--\s*nrw:generated\s*-->.*?<!--\s*/nrw:generated\s*-->",
        "",
        text,
        flags=re.DOTALL,
    )
    cleaned = re.sub(r"<!--.*?-->", "", without_generated, flags=re.DOTALL)
    for raw in cleaned.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ">", "-", "*", "|", "<")):
            continue
        return line
    return ""


def _note_excerpt(layout: ProjectLayout, row: dict[str, Any]) -> str:
    """What the analyst wrote about a fit, trimmed for a table cell."""
    from nr_workbench.provenance.lookup import fit_dir

    directory = fit_dir(layout, row)
    if directory is None:
        return ""
    notes = directory / "NOTES.md"
    if not notes.is_file():
        return ""
    try:
        line = _first_line(notes.read_text(encoding="utf-8"))
    except OSError:
        return ""
    if len(line) > NOTE_EXCERPT:
        line = line[: NOTE_EXCERPT - 1].rstrip() + "…"
    # A stack written as `D2O|Cu|Ti|Si` is the most natural thing to put in a
    # note about a co-refinement, and unescaped it silently splits the row.
    return line.replace("|", "\\|")


def undocumented_fits(layout: ProjectLayout, sample: str) -> list[str]:
    """Fits for this sample whose ``NOTES.md`` holds nothing an analyst wrote.

    The identifiers rather than the count, because the count tells a reader
    there is a gap and the identifiers let them close it.

    Args:
        layout: The project layout.
        sample: Sample identifier.

    Returns:
        Fit ids with no written note, oldest first.
    """
    index = FitIndex(layout.index_file)
    missing = []
    for row in reversed(index.fits(sample=sample)):
        if not _note_excerpt(layout, row):
            missing.append(str(row.get("fit_id", "")))
    return [fit_id for fit_id in missing if fit_id]


def sequence_markdown(
    layout: ProjectLayout, sample: str, *, mode: str = "full"
) -> tuple[str, int]:
    """Render the sample's fits as a numbered chain, oldest first.

    Oldest first because the sequence is an argument and an argument reads
    forward. ``nrw ls`` is newest-first, which is right for "what did I just
    run" and wrong for "how did this analysis get here".

    Args:
        layout: The project layout.
        sample: Sample identifier.
        mode: ``full`` for every fit; ``reportable`` for only those a sampler
            produced. An amoeba run is an exploration, not a citable result,
            and listing one in a paper's supporting information implies a
            posterior that was never computed.

    Returns:
        ``(markdown, undocumented_count)``.
    """
    index = FitIndex(layout.index_file)
    rows = list(reversed(annotate(index.fits(sample=sample))))
    if mode == "reportable":
        rows = [r for r in rows if str(r.get("method", "")).lower() == "dream"]
        if not rows:
            return (
                "_No sampler fits recorded yet. An amoeba exploration cannot "
                "carry an uncertainty,\nso nothing here is quotable until one "
                "is re-run with `--method dream`._\n",
                0,
            )
    if not rows:
        return ("_No fits recorded for this sample yet._\n", 0)

    promoted: dict[str, str] = {}
    for promotion_label in {str(e.get("label")) for e in index.promotions()}:
        entry = index.current_label(promotion_label)
        if entry is not None:
            promoted[str(entry.get("fit_id"))] = promotion_label

    lines = [
        "| # | fit | model | method | χ² | note |",
        "|---|---|---|---|---|---|",
    ]
    undocumented = 0
    details: list[str] = []
    for position, row in enumerate(rows, start=1):
        fit_id = str(row.get("fit_id", ""))
        chisq = row.get("chisq")
        chisq_text = f"{chisq:.3g}" if isinstance(chisq, int | float) else "–"
        if row.get("converged") is False:
            chisq_text += " (not converged)"
        method = str(row.get("method") or "–")
        label = promoted.get(fit_id)
        model = str(row.get("model", ""))
        if label:
            model += f" **[{label.upper()}]**"
        excerpt = _note_excerpt(layout, row)
        if not excerpt:
            undocumented += 1
            excerpt = "_nothing written down_"
        lines.append(
            f"| {position} | `{_short(fit_id)}` | {model} | {method} | "
            f"{chisq_text} | {excerpt} |"
        )
        details.append(
            f"{position}. `{fit_id}` — {row.get('change', '')}".rstrip().rstrip("—")
        )

    lines.append("")
    lines.append(
        "<details><summary>Full fit ids and what changed at each step</summary>"
    )
    lines.append("")
    lines.extend(details)
    lines.append("")
    lines.append("</details>")
    lines.append("")
    return ("\n".join(lines), undocumented)


#: How many concepts the plain tier asks to have explained.
#:
#: The tier asks for two to three pages and each explanation is a paragraph or
#: two. A busy sample detects a dozen, which would make the explanations longer
#: than the report they exist to support -- so the ones most fits ran into get
#: a brief and the rest are named in a comment.
CONCEPT_LIMIT = 6

#: The headline sentence, identical in all three tiers by construction. It is
#: what `--check` compares: three documents that disagree about the answer are
#: worse than one document, and this is where that shows up first.
HEADLINE = "**In one line:** _the answer, before the reasoning. Write this last._"

#: Marks a tier and the stem it belongs to, so tooling never has to parse a
#: filename to know what it is holding.
TIER_MARKER = "<!-- nrw:tier {key} stem={stem} -->"

_TIER_RE = re.compile(r"<!--\s*nrw:tier\s+(\S+)\s+stem=(\S+)\s*-->")


def report_stem(sample: str, topic: str | None = None) -> str:
    """The shared filename stem for one report's three tiers.

    Args:
        sample: Sample identifier.
        topic: Optional short slug for a report about a separate question.

    Returns:
        The stem, without a tier suffix or an extension.
    """
    return (
        _slug(f"{sample}-{topic}") if topic else _slug(f"{sample}-what-the-fits-show")
    )


def tier_paths(layout: ProjectLayout, sample: str, stem: str) -> dict[str, Path]:
    """Where each tier of one report lives.

    Args:
        layout: The project layout.
        sample: Sample identifier.
        stem: Shared filename stem.

    Returns:
        Mapping of tier key to path.
    """
    from nr_workbench.reporting.tiers import ALL

    reports = layout.sample(sample) / "reports"
    return {tier.key: reports / f"{stem}{tier.filename_suffix}.md" for tier in ALL}


def _tier_body(
    layout: ProjectLayout,
    sample: str,
    tier: Any,
    stem: str,
    *,
    extra_concepts: tuple[str, ...] = (),
) -> tuple[str, int]:
    """Render one tier's scaffold.

    Returns:
        ``(text, undocumented_count)``.
    """
    from nr_workbench.reporting import concepts as concepts_mod
    from nr_workbench.reporting.tiers import ALL

    today = datetime.now(UTC).strftime("%Y-%m-%d")
    heading = tier.heading.format(sample=sample)

    body = [
        f"# {heading}",
        "",
        TIER_MARKER.format(key=tier.key, stem=stem),
        f"<!-- {today} -->",
        "",
        f"> **Who this is for:** {tier.audience}",
        "",
        tier.blurb,
        "",
        HEADLINE,
        "",
    ]

    siblings = [t for t in ALL if t.key != tier.key]
    body += [
        "_The same analysis is also written as "
        + ", ".join(f"`{stem}{t.filename_suffix}.md`" for t in siblings)
        + ". All three must agree on the headline above and on the fit ids they "
        "cite;\n`nrw report --check` enforces that._",
        "",
    ]

    undocumented = 0
    if tier.sequence != "none":
        sequence, undocumented = sequence_markdown(layout, sample, mode=tier.sequence)
        label = (
            "Every fit, oldest first."
            if tier.sequence == "full"
            else "The sampler fits only -- an amoeba run carries no posterior "
            "and is not quotable."
        )
        body += [
            "## The sequence",
            "",
            f"{label} This table is generated by `nrw report`; the prose",
            "below it is not. `nrw report --force` refreshes the table and "
            "leaves the prose alone.",
            "",
            _BEGIN,
            sequence + _END,
            "",
        ]

    for section, prompt in tier.prompts:
        body += [f"## {section}", "", f"<!-- {prompt} -->", ""]

    if tier.concepts:
        detection = concepts_mod.detect(layout, sample)
        chosen, remainder = detection.top(CONCEPT_LIMIT)
        # An explicitly requested concept is never crowded out: asking for one
        # is a stronger signal than any trigger count.
        slugs = list(chosen) + [s for s in extra_concepts if s not in chosen]
        remainder = tuple(s for s in remainder if s not in extra_concepts)

        body += [
            "## The ideas you need to read this",
            "",
            "Chosen from what this analysis actually ran into, not from a "
            "syllabus. Under each\nheading is a brief: write the explanation "
            "yourself, about this sample, using the\nnumbers you have. You have "
            "just used these ideas to reach the answer, and a\nparagraph about "
            "this measurement beats a textbook one every time. Delete any that\n"
            "turn out not to matter to the story.",
            "",
        ]
        if not slugs:
            body += [
                "_Nothing detected. If the fits have not been assessed yet, "
                "`nrw assess <fit_id>`\nis what supplies this -- it records the "
                "correlated pairs, the bounded parameters\nand the skewed "
                "posteriors that select these._",
                "",
            ]
        for slug in slugs:
            body += concepts_mod.render(slug, detection.reasons.get(slug, ""))

        if remainder:
            # Named rather than briefed. This tier is meant to be short, and a
            # dozen explanations is a textbook -- but a reader deciding what to
            # add should not have to rerun detection to see what was left out.
            body += [
                "<!-- Also detected here, held back to keep this short:",
                "     " + ", ".join(remainder) + ".",
                f"     Add one: `nrw report {sample} --tier plain --force "
                "--concept <slug>`. -->",
                "",
            ]

    return ("\n".join(body), undocumented)


def run_report(
    *,
    sample: str,
    topic: str | None = None,
    tier: str | None = None,
    concept: tuple[str, ...] = (),
    root: str | None = None,
    force: bool = False,
    stdout: bool = False,
    list_concepts: bool = False,
) -> None:
    """Scaffold a sample's report -- all three tiers, unless one is named.

    All three are always written, because the audience for a beamtime result is
    a team rather than a person: the reviewer, the co-author and the chemist
    who owns the sample are three different readers, and a document that serves
    one of them badly serves the other two not at all.

    Args:
        sample: Sample identifier.
        topic: Short slug for a report about a separate question, so new work
            never has to overwrite a finished report.
        tier: Write only this tier.
        concept: Extra concept slugs to explain in the plain tier.
        root: Project root; discovered if omitted.
        force: Refresh the generated table in reports that already exist.
        stdout: Print instead of writing.
        list_concepts: Report which concepts were detected, and stop.

    Raises:
        click.ClickException: If the sample or the tier does not exist, or a
            report exists and neither ``force`` nor ``stdout`` was given.
    """
    from nr_workbench.reporting import concepts as concepts_mod
    from nr_workbench.reporting.tiers import ALL, BY_KEY

    layout = _layout(root)
    directory = layout.sample(sample)
    if not directory.is_dir():
        raise click.ClickException(layout.missing_sample_message(sample))

    for slug in concept:
        if slug not in concepts_mod.library():
            raise click.ClickException(
                f"No concept {slug!r}. `nrw report --concepts` lists them."
            )

    if list_concepts:
        _print_concepts(layout, sample)
        return

    if tier is not None and tier not in BY_KEY:
        raise click.ClickException(f"No tier {tier!r}. Known: {', '.join(BY_KEY)}.")

    wanted = [BY_KEY[tier]] if tier else list(ALL)
    stem = report_stem(sample, topic)
    paths = tier_paths(layout, sample, stem)

    if stdout:
        for spec in wanted:
            text, _ = _tier_body(
                layout, sample, spec, stem, extra_concepts=tuple(concept)
            )
            click.echo(text)
        return

    existing = [spec for spec in wanted if paths[spec.key].exists()]
    if existing and not force:
        listing = "\n".join(
            f"    {paths[spec.key].relative_to(layout.root)}" for spec in existing
        )
        raise click.ClickException(
            f"These already exist:\n{listing}\n\n"
            "A written report is a finished artefact -- someone has read it. "
            "Pick one:\n"
            "  --force            refresh the generated table, leaving the "
            "prose alone\n"
            f"  --topic <slug>     start a separate report, e.g. `nrw report "
            f"{sample} --topic buried-change`\n"
            "  --stdout           see the new scaffold without touching "
            "anything"
        )

    reports = directory / "reports"
    reports.mkdir(parents=True, exist_ok=True)

    undocumented = 0
    for spec in wanted:
        text, missing = _tier_body(
            layout, sample, spec, stem, extra_concepts=tuple(concept)
        )
        undocumented = max(undocumented, missing)
        path = paths[spec.key]
        if path.exists():
            sequence, _ = sequence_markdown(layout, sample, mode=spec.sequence)
            text = _refresh(path.read_text(encoding="utf-8"), sequence)
        path.write_text(text, encoding="utf-8")
        click.echo(f"  {path.relative_to(layout.root)}")

    if undocumented:
        click.secho(
            f"  ! {undocumented} fit(s) in the table have nothing written down. A "
            "sequence\n    with gaps in it is not a narrative -- `nrw note <fit_id> "
            '-m "..."`.',
            fg="yellow",
        )
    click.echo("  Name fit ids in the prose and they become links.")
    if not tier:
        click.echo(
            "  All three tiers must agree on the headline and the fit ids they "
            "cite:\n  `nrw report --check " + sample + "`."
        )


def _print_concepts(layout: ProjectLayout, sample: str) -> None:
    """Say which concepts were detected, on what evidence, and what else exists."""
    from nr_workbench.reporting import concepts as concepts_mod

    detection = concepts_mod.detect(layout, sample)

    click.echo(
        f"  {detection.assessed} of {detection.total} fit(s) have an assessment "
        "to read."
    )
    if detection.total and not detection.assessed:
        click.secho(
            "  ! No assessments found. `nrw assess <fit_id>` is what detects "
            "correlated pairs,\n    bounded parameters and skewed posteriors -- "
            "without it only the coarse\n    triggers fire.",
            fg="yellow",
        )
    click.echo()
    click.echo("  Detected, and why:")
    for slug in detection.slugs:
        click.echo(f"    {slug:<36} {detection.reasons.get(slug, '')}")
    if not detection.slugs:
        click.echo("    (none)")

    rest = [s for s in concepts_mod.library() if s not in detection.slugs]
    if rest:
        click.echo()
        click.echo("  Available, not detected -- add with --concept <slug>:")
        for slug in rest:
            click.echo(f"    {slug}")


def run_report_check(*, sample: str | None = None, root: str | None = None) -> None:
    """Verify a sample's report tiers agree with each other.

    Args:
        sample: Sample identifier; every sample if omitted.
        root: Project root; discovered if omitted.

    Raises:
        SystemExit: With code 1 if any tier set is inconsistent.
    """
    layout = _layout(root)
    samples = [sample] if sample else layout.list_samples()

    problems: list[str] = []
    for name in samples:
        problems.extend(check_tiers(layout, name))

    if not problems:
        click.echo(f"  {len(samples)} sample(s) checked; tiers consistent.")
        return

    for problem in problems:
        click.echo(f"  {problem}")
    raise SystemExit(1)


def check_tiers(layout: ProjectLayout, sample: str) -> list[str]:
    """Find the ways one sample's report tiers disagree.

    Three documents saying different things is worse than one saying nothing,
    and the failure is silent: each reads fine on its own. So the invariants
    are checked rather than trusted -- every tier present, one headline, and no
    fit cited in a summary that the full record does not also cite.

    Args:
        layout: The project layout.
        sample: Sample identifier.

    Returns:
        Human-readable problems; empty when consistent.
    """
    from nr_workbench.reporting.tiers import ALL

    reports = layout.sample(sample) / "reports"
    if not reports.is_dir():
        return []

    groups: dict[str, dict[str, Path]] = {}
    for path in sorted(reports.glob("*.md")):
        try:
            head = path.read_text(encoding="utf-8")[:2000]
        except OSError:
            continue
        match = _TIER_RE.search(head)
        if match:
            groups.setdefault(match.group(2), {})[match.group(1)] = path

    problems: list[str] = []
    for stem, found in sorted(groups.items()):
        missing = [t.key for t in ALL if t.key not in found]
        if missing:
            problems.append(
                f"{sample}/{stem}: missing tier(s) {', '.join(missing)} -- "
                f"`nrw report {sample} --tier {missing[0]}` writes one."
            )

        headlines: dict[str, str] = {}
        cited: dict[str, set[str]] = {}
        for key, path in found.items():
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            headlines[key] = _headline_of(text)
            cited[key] = _cited_fits(text)

        written = {k: v for k, v in headlines.items() if v and not _is_placeholder(v)}
        if len(set(written.values())) > 1:
            problems.append(
                f"{sample}/{stem}: the tiers give different one-line answers. "
                "They are three\n      renderings of one result, so the "
                "headline has to be the same sentence."
            )

        # No `if record` guard: a summary citing fits when the full record
        # cites none is the *worst* version of this, not an exempt one. On a
        # fresh scaffold every set is empty, so nothing fires.
        record = cited.get("technical", set())
        for key in ("si", "plain"):
            extra = cited.get(key, set()) - record
            if extra:
                problems.append(
                    f"{sample}/{stem}: {key} cites fit(s) the technical "
                    f"tier does not: {', '.join(sorted(extra))}. A summary "
                    "cannot rest on\n      evidence the full record omits."
                )
    return problems


def _headline_of(text: str) -> str:
    """The one-line answer, or an empty string."""
    for line in text.splitlines():
        if line.strip().startswith("**In one line:**"):
            return line.strip()
    return ""


def _is_placeholder(headline: str) -> bool:
    """Whether the headline is still the scaffolded prompt."""
    return "the answer, before the reasoning" in headline


def _cited_fits(text: str) -> set[str]:
    """Fit ids named in a document's prose.

    Both forms count: the full ``YYYYMMDD-HHMMSSZ-hash`` and the trailing hash
    on its own, which is what the tables print and what the CLI accepts.
    """
    body = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    full = set(re.findall(r"\b\d{8}-\d{6}Z-([0-9a-f]{6,})\b", body))
    short = set(re.findall(r"`([0-9a-f]{8})`", body))
    return full | short


#: Suffix of the manifest written beside a report figure script.
FIGURE_MANIFEST_SUFFIX = ".figures.json"

#: Schema of that manifest.
FIGURE_SCHEMA = "nrw-report-figures/1"


def run_report_figure(
    *,
    script: str,
    root: str | None = None,
    stdout_to: str | None = None,
) -> None:
    """Run a report figure script and record what it read and wrote.

    The pattern this supports already happens: the reference project grew four
    scripts under ``reports/`` that read result directories and emitted the
    plots and parameter tables the report actually shows. They were the right
    instinct with nothing behind them -- no record of which fits a figure came
    from, so `nrw whence` on a published plot said "unknown", which is the one
    question this project exists to answer.

    Inputs are taken from the script's own source rather than by tracing its
    file access: a fit id in the script *is* the citation, it survives being
    read by a person, and tracing would also capture every incidental file the
    interpreter touched.

    Args:
        script: Path to a script under ``samples/<id>/reports/``.
        root: Project root; discovered if omitted.
        stdout_to: Write the script's standard output here, relative to the
            script's directory. For a script that emits a markdown table.

    Raises:
        click.ClickException: If the script is outside a sample's reports
            directory, or exits non-zero.
    """
    import hashlib
    import subprocess
    import sys

    layout = _layout(root)
    path = Path(script).resolve()
    if not path.is_file():
        raise click.ClickException(f"No script at {script}.")

    try:
        relative = path.relative_to(layout.root)
    except ValueError as exc:
        raise click.ClickException(
            f"{script} is outside the project. A figure script has to live "
            "beside the report it feeds, under samples/<id>/reports/."
        ) from exc
    if relative.parent.name != "reports":
        raise click.ClickException(
            f"{relative} is not in a reports/ directory. Put figure scripts in "
            "samples/<id>/reports/ so they sit beside the prose that shows "
            "their output."
        )

    source = path.read_text(encoding="utf-8")
    fit_ids = sorted(_cited_fits(source))
    if not fit_ids:
        click.secho(
            "  ! No fit id appears in this script. Whatever it plots cannot be "
            "traced back to\n    a recorded fit -- name the fits it reads, in a "
            "comment if nowhere else.",
            fg="yellow",
        )

    before = _tree_state(relative.parent, layout.root)

    completed = subprocess.run(  # noqa: S603 - argv is the interpreter and a project path
        [sys.executable, str(path)],
        cwd=layout.root,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.stderr.strip():
        click.echo(completed.stderr.rstrip())
    if completed.returncode != 0:
        raise click.ClickException(
            f"{relative} exited {completed.returncode}. Nothing was recorded."
        )

    if stdout_to:
        target = path.parent / stdout_to
        target.write_text(completed.stdout, encoding="utf-8")
    elif completed.stdout.strip():
        click.echo(completed.stdout.rstrip())

    after = _tree_state(relative.parent, layout.root)
    produced = sorted(
        name for name, digest in after.items() if before.get(name) != digest
    )

    manifest = {
        "schema": FIGURE_SCHEMA,
        "script": relative.as_posix(),
        "script_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "generated": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "fits": fit_ids,
        "outputs": [{"path": name, "sha256": after[name]} for name in produced],
    }
    manifest_path = path.with_name(path.name + FIGURE_MANIFEST_SUFFIX)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    click.echo(f"  {manifest_path.relative_to(layout.root)}")
    for name in produced:
        click.echo(f"    wrote {name}")
    if not produced:
        click.secho(
            "  ! The script wrote nothing under reports/. If it saves "
            "elsewhere, move the\n    output beside the report -- a figure "
            "nobody can locate is not provenance.",
            fg="yellow",
        )
    if fit_ids:
        click.echo(f"    from {len(fit_ids)} fit(s): {', '.join(fit_ids)}")


def _tree_state(relative_dir: Path, root: Path) -> dict[str, str]:
    """Digest every file under a directory, keyed by project-relative path."""
    import hashlib

    directory = root / relative_dir
    state: dict[str, str] = {}
    if not directory.is_dir():
        return state
    for path in sorted(directory.rglob("*")):
        if not path.is_file():
            continue
        if path.name.endswith(FIGURE_MANIFEST_SUFFIX):
            continue
        try:
            state[path.relative_to(root).as_posix()] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
        except OSError:
            continue
    return state


#: Fences the superseded banner, so it can be found and replaced rather than
#: accumulating one copy per time somebody ran the command.
_SUPERSEDED_BEGIN = "<!-- nrw:superseded -->"
_SUPERSEDED_END = "<!-- /nrw:superseded -->"


def run_supersede(
    *,
    sample: str,
    old_stem: str,
    new_stem: str,
    reason: str,
    root: str | None = None,
) -> None:
    """Bannerise every tier of a report as replaced by another.

    Deliberately not a delete. A report that turned out to be wrong is evidence
    about the sample and about how the wrong answer was reachable, and the next
    session needs to be able to find it -- the reference project's single most
    useful artefact is an escalation whose author retracted it in the same
    session, with both versions preserved.

    Args:
        sample: Sample identifier.
        old_stem: Stem of the report being superseded.
        new_stem: Stem of the report that replaces it.
        reason: Why. Written into the banner.
        root: Project root; discovered if omitted.

    Raises:
        click.ClickException: If either report cannot be found.
    """
    layout = _layout(root)
    old_paths = tier_paths(layout, sample, old_stem)
    new_paths = tier_paths(layout, sample, new_stem)

    present = {key: path for key, path in old_paths.items() if path.exists()}
    if not present:
        raise click.ClickException(
            f"No report with stem {old_stem!r} under samples/{sample}/reports/. "
            "The stem is the filename without its `-technical`/`-si`/`-plain` "
            "suffix."
        )
    if not any(path.exists() for path in new_paths.values()):
        raise click.ClickException(
            f"No report with stem {new_stem!r} to supersede it with. Write it "
            f"first: `nrw report {sample} --topic <slug>`."
        )

    today = datetime.now(UTC).strftime("%Y-%m-%d")
    for key, path in present.items():
        banner = (
            f"{_SUPERSEDED_BEGIN}\n"
            f"> **Superseded on {today} by `{new_stem}{_suffix_for(key)}.md`.**\n"
            f">\n"
            f"> {reason}\n"
            f">\n"
            "> Kept because the reasoning is still worth reading, and because "
            "how a wrong\n> answer was reachable is itself a finding. Do not "
            "cite the conclusions below.\n"
            f"{_SUPERSEDED_END}"
        )
        text = path.read_text(encoding="utf-8")
        if _SUPERSEDED_BEGIN in text and _SUPERSEDED_END in text:
            head, _, rest = text.partition(_SUPERSEDED_BEGIN)
            _, _, tail = rest.partition(_SUPERSEDED_END)
            text = f"{head}{banner}{tail}"
        else:
            lines = text.splitlines(keepends=True)
            # After the title, so the document still opens with its own name.
            cut = 1 if lines and lines[0].startswith("# ") else 0
            text = "".join(lines[:cut]) + "\n" + banner + "\n" + "".join(lines[cut:])
        path.write_text(text, encoding="utf-8")
        click.echo(f"  {path.relative_to(layout.root)}")

    click.echo(f"  superseded by {new_stem}")


def _suffix_for(key: str) -> str:
    """The filename suffix belonging to a tier key."""
    from nr_workbench.reporting.tiers import BY_KEY

    tier = BY_KEY.get(key)
    return tier.filename_suffix if tier else ""


def _refresh(existing: str, sequence: str) -> str:
    """Replace the generated sequence in a report, leaving the prose alone.

    Without the fences an older report has no machine-findable table, so the new
    one is appended under a dated heading rather than guessing which lines were
    generated. Deleting someone's prose to save them a paste is not a trade this
    should make.
    """
    if _BEGIN in existing and _END in existing:
        head, _, rest = existing.partition(_BEGIN)
        _, _, tail = rest.partition(_END)
        return f"{head}{_BEGIN}\n{sequence}{_END}{tail}"
    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    return (
        f"{existing.rstrip()}\n\n## The sequence, as of {stamp}\n\n"
        f"{_BEGIN}\n{sequence}{_END}\n"
    )
