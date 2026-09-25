"""What each measurement was measured under, in words a reader can use.

An ISAAC record is supposed to say that run 218393 was held at -0.5 mA/cm^2
while 218386 and 218397 sat at open circuit. Nothing in a fit knows that. It is
in ``sample.md``, in the table the scientist filled in before any of this ran,
and it has to travel to reach the record.

Downstream, ``data-assembler`` parses a free-text description into structured
fields with a regex that recognises open circuit, a numeric potential in volts,
pH and molar electrolytes. It does **not** recognise a current density, so a
galvanostatic hold is invisible to it. That is worth knowing rather than
working around: the text this module produces is written so the regex picks up
what it can, and so a human reads the rest --- and the current density is
stated plainly instead of being bent into a potential it is not.

Two ways to get that text, and the cheap one runs first:

* **The measurement table.** ``sample.md`` conventionally carries a
  ``| Run | Type | Condition |`` table. Reading the row for a run is exact,
  needs no model, and already answers the common case.
* **A language model**, when one is configured. It reads the whole of
  ``sample.md`` plus the state's own description and writes one sentence per
  state. That is what handles a sample.md whose conditions are in prose rather
  than a table, or where the table says "as above".

Nothing here invents a condition. If neither route finds anything, the state
keeps whatever description the spec gave it, and the record says less rather
than something untrue.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass
class Conditions:
    """What was found for one state.

    Attributes:
        text: The description to hand downstream.
        source: ``table``, ``llm``, ``spec``, or ``none``.
    """

    text: str
    source: str

    @property
    def as_dict(self) -> dict[str, Any]:
        """Return the JSON form."""
        return {"text": self.text, "source": self.source}


def from_table(markdown: str, run: str) -> str | None:
    """Read one run's condition out of a ``sample.md`` measurement table.

    Args:
        markdown: The sample's prose.
        run: The run number, as a string.

    Returns:
        The condition cell, or ``None`` if there is no row for that run.
    """
    if not markdown or not run:
        return None
    from nr_workbench.sample_md import (
        CONDITION_HEADERS,
        RUN_HEADERS,
        column,
        table_cells,
    )

    header: list[str] = []
    for line in markdown.splitlines():
        cells = table_cells(line)
        if cells is None:
            continue
        lowered = [c.lower() for c in cells]
        if column(lowered, RUN_HEADERS) is not None:
            header = lowered
            continue
        if not header or set("".join(cells)) <= set("-: "):
            continue
        run_at = column(header, RUN_HEADERS)
        if run_at is None or run_at >= len(cells) or cells[run_at] != run:
            continue
        # A column called "condition" is authoritative, *including when its
        # cell is empty*: falling through to another column then reported the
        # Type ("full Q") as the condition, and that reached the ISAAC record.
        at = column(header, CONDITION_HEADERS)
        if at is not None:
            value = cells[at] if at < len(cells) else ""
            return value or None
        # No condition column: a "notes" column, else the last cell, which is
        # where a free-text note lands in practice.
        if "notes" in header and header.index("notes") < len(cells):
            value = cells[header.index("notes")]
            if value:
                return value
        rest = [c for i, c in enumerate(cells) if i != run_at and c]
        return rest[-1] if rest else None
    return None


def describe(
    *,
    states: list[tuple[str, str, str | None]],
    sample_markdown: str,
    use_llm: bool = True,
) -> dict[str, Conditions]:
    """Work out a condition description for each state.

    Args:
        states: ``(name, run, spec_condition)`` for each state.
        sample_markdown: The sample's prose.
        use_llm: Ask a language model when one is configured.

    Returns:
        One :class:`Conditions` per state name.
    """
    found: dict[str, Conditions] = {}
    for name, run, spec_condition in states:
        cell = from_table(sample_markdown, run)
        if cell:
            # Both halves: the table says what was applied, the spec says what
            # it was for. Neither alone is the whole condition.
            text = f"{cell}; {spec_condition}" if spec_condition else cell
            found[name] = Conditions(text=text, source="table")
        elif spec_condition:
            found[name] = Conditions(text=spec_condition, source="spec")
        else:
            found[name] = Conditions(text="", source="none")

    if use_llm:
        enriched = _from_llm(states, sample_markdown, found)
        found.update(enriched)
    return found


def _from_llm(
    states: list[tuple[str, str, str | None]],
    sample_markdown: str,
    current: dict[str, Conditions],
) -> dict[str, Conditions]:
    """Ask a model to write one condition sentence per state.

    Returns an empty mapping when no endpoint is configured or the reply
    cannot be used --- the table and spec answers already in hand are better
    than a failure.
    """
    from nr_workbench.agent.guard import agent_is_driving
    from nr_workbench.aure_adapter import AureUnavailableError, complete, llm_available

    if agent_is_driving():
        # An agent exporting records should write the condition sentences into
        # sample.md's table, where a person can check them, rather than have a
        # second model invent them at export time into a record that leaves
        # the project.
        return {}

    if not llm_available() or not sample_markdown.strip():
        return {}

    listing = "\n".join(
        f"- {name} (run {run}): spec says {spec or '(nothing)'}; "
        f"table says {current.get(name, Conditions('', 'none')).text or '(nothing)'}"
        for name, run, spec in states
    )
    system = (
        "You extract experimental conditions for a neutron reflectometry "
        "metadata record. Reply with JSON only: an object mapping each state "
        "name to one sentence. Use ONLY facts present in the notes. State the "
        "electrochemical control explicitly -- open circuit as 'OCV', an "
        "applied potential as a number followed by V and its reference "
        "electrode, a galvanostatic hold as its current density. Name the "
        "electrolyte and its concentration when given. Do not convert a "
        "current density into a potential. Do not guess. If the notes say "
        "nothing about a state, give it an empty string."
    )
    user = f"## Sample notes\n\n{sample_markdown}\n\n## States\n\n{listing}"

    try:
        reply = complete(system, user)
    except AureUnavailableError:
        return {}

    import json

    match = re.search(r"\{[\s\S]*\}", reply)
    if not match:
        return {}
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}

    out: dict[str, Conditions] = {}
    known = {name for name, _, _ in states}
    for name, text in payload.items():
        if name in known and isinstance(text, str) and text.strip():
            out[name] = Conditions(text=text.strip(), source="llm")
    return out
