"""Turning a scientist's notes into the physics half of a model spec.

``nrw model new`` fills in everything that is a *fact*: which runs exist, which
files belong to them, each segment's incident angle read from its header. It
cannot fill in the *stack* -- what the sample is made of, in what order, roughly
how thick -- because that is in the scientist's head and, if they wrote it down,
in ``sample.md``.

This module closes that gap two ways, from one prompt:

* :func:`propose_stack` calls a configured endpoint directly.
* :func:`agent_instructions` prints the same request for pasting to a coding
  assistant that is already in the repository.

Both are the same text, so the two paths cannot drift apart.

**The division of labour is enforced, not requested.** A model may propose only
``description``, ``materials``, ``stack`` and ``parameters``. Everything read
from disk -- states, series, angles, file paths -- is filtered out of whatever
comes back, in :func:`merge_proposal`. A language model is being asked what the
sample is, never what was measured: the second is already known exactly, and a
plausible wrong answer there is unrecoverable.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Keys a proposal may set. Anything else it returns is discarded.
PROPOSABLE = ("description", "materials", "stack", "parameters", "constraints")

#: Skills always sent: the schema, the physics, and the beamline conventions.
CORE_SKILLS = (
    "nrw-model-spec",
    "neutron-reflectometry",
    "refl-bl4b-instrument",
)

#: Skills sent when the notes mention them. Keyed by the words that select
#: each, lowercased. Cheap to check, and it is what the tags are for.
CONTEXTUAL_SKILLS: dict[str, tuple[str, ...]] = {
    "metal-oxide-interfaces": (
        "cu",
        "copper",
        "oxide",
        "cuox",
        "titanium",
        "ti ",
        "chromium",
        "gold",
        "au ",
        "electrode",
        "metal",
    ),
    "polymer-films": (
        "polymer",
        "ionomer",
        "nafion",
        "pfsa",
        "brush",
        "peo",
        "pmma",
        "polystyrene",
        "film",
        "swell",
    ),
    "solvent-contrast-matching": (
        "d2o",
        "h2o",
        "thf",
        "solvent",
        "deuterat",
        "contrast",
        "toluene",
        "ethanol",
        "water",
    ),
    "thin-layer-degeneracy": ("oxide", "thin", "adhesion", "interfacial"),
}


@dataclass
class Proposal:
    """What a model suggested, and what was done with it.

    Attributes:
        document: The accepted keys, ready to merge.
        rejected: Keys the model returned that it is not allowed to set.
        notes: Any commentary it offered, for the human to read.
        raw: The reply as received, for debugging.
    """

    document: dict[str, Any] = field(default_factory=dict)
    rejected: list[str] = field(default_factory=list)
    notes: str = ""
    raw: str = ""


class AuthoringError(Exception):
    """Raised when a proposal cannot be obtained or understood."""


def relevant_skills(notes: str, available: dict[str, Path]) -> list[str]:
    """Choose which skills to send as context.

    Args:
        notes: The ``sample.md`` text.
        available: Skill name to its ``SKILL.md`` path.

    Returns:
        Skill names, core ones first, then any the notes select.
    """
    lowered = (notes or "").lower()
    chosen = [name for name in CORE_SKILLS if name in available]
    for name, triggers in CONTEXTUAL_SKILLS.items():
        if (
            name in available
            and name not in chosen
            and any(trigger in lowered for trigger in triggers)
        ):
            chosen.append(name)
    return chosen


def missing_relevant(notes: str, installed: dict[str, Path]) -> list[str]:
    """Name skills the notes call for that are not installed here.

    `nrw init` seeds the skills that apply to any sample; the material-specific
    ones are bundled but left to `nrw skills sync`. So the ones a particular
    sample most needs are exactly the ones likely to be absent, and silently
    proceeding without them wastes the context that makes the answer good.

    Args:
        notes: The ``sample.md`` text.
        installed: Skill name to path, as from :func:`find_skills`.

    Returns:
        Skill names worth installing before asking.
    """
    lowered = (notes or "").lower()
    wanted = []
    for name, triggers in CONTEXTUAL_SKILLS.items():
        if name not in installed and any(t in lowered for t in triggers):
            wanted.append(name)
    return sorted(wanted)


def find_skills(root: Path) -> dict[str, Path]:
    """Locate the project's installed skills.

    Args:
        root: Project root.

    Returns:
        Skill name to ``SKILL.md`` path.
    """
    found: dict[str, Path] = {}
    base = Path(root) / "skills"
    if not base.is_dir():
        return found
    for path in sorted(base.rglob("SKILL.md")):
        found[path.parent.name] = path
    return found


def build_prompt(
    *,
    skeleton: dict[str, Any],
    notes: str,
    skills: dict[str, Path],
    facts: str = "",
) -> tuple[str, str]:
    """Build the system and user halves of the request.

    Args:
        skeleton: The scaffolded spec, with its facts already filled in.
        notes: The ``sample.md`` text.
        skills: Skill name to path, as from :func:`find_skills`.
        facts: Extra measured context, e.g. `nrw data features` output.

    Returns:
        ``(system, user)``.
    """
    import yaml

    chosen = relevant_skills(notes, skills)
    skill_text = "\n\n".join(
        f"===== SKILL: {name} =====\n{skills[name].read_text(encoding='utf-8')}"
        for name in chosen
    )

    system = (
        "You are helping a neutron reflectometry scientist at SNS REF_L (BL-4B) "
        "turn their notes into an `nrw-model/1` model specification.\n\n"
        "The reference material below is the project's own skills. Follow them; "
        "they carry this beamline's conventions and the schema.\n\n"
        f"{skill_text}\n\n"
        "===== YOUR TASK =====\n"
        "Propose the physics half of the spec, and only that.\n\n"
        "Return a single JSON object with these keys and no others:\n"
        '  "description" : one or two sentences describing the sample\n'
        '  "materials"   : {name: {"rho": float}} -- SLD in 1e-6/A2\n'
        '  "stack"       : [ {name, material, thickness, roughness}, ... ]\n'
        "                  ordered AMBIENT FIRST, SUBSTRATE LAST; the substrate\n"
        "                  entry has no thickness or roughness\n"
        '  "parameters"  : [ {path, range|value, per, ...}, ... ]\n'
        '  "notes"       : anything you are unsure about, for the human\n\n'
        "Rules:\n"
        "- Do NOT return `states`, `series`, `thetas`, `data_dir`, `run`, "
        "`reduced_dir`, `schema`, `name` or `sample`. Those are read from the "
        "files on disk and are already correct. Anything you return for them "
        "will be discarded.\n"
        "- Use the state and series names given in the skeleton when you write "
        "`in:` lists.\n"
        "- Prefer FEWER layers. Every layer under about 30 A is barely "
        "resolvable; do not add one without a reason from the notes.\n"
        "- Give every free parameter a physically sensible range, not a wide "
        "one.\n"
        "- If the notes do not say what a layer is made of, say so in `notes` "
        "rather than inventing a material.\n"
        "- Read the notes for *instrument* problems as well as sample "
        "composition, and add the matching nuisance parameter when one is "
        "described. These are easy to miss and each one, left out, pushes its "
        "error into a layer:\n"
        "    sample misaligned / angle uncertain / offset\n"
        "      -> {path: probe.theta_offset, range: [-0.02, 0.02], per: state}\n"
        "    sample curved / bent / warped / mosaic / fringes damped\n"
        "      -> {path: probe.sample_broadening, range: [0.0, 0.05], per: state}\n"
        "    high background / poor statistics at high Q\n"
        "      -> {path: probe.background, range: [0.0, 1.0e-5], per: state}\n"
        "  theta_offset and sample_broadening only work on states measured per "
        "angle (`segments: auto`); scope them with `in:` if any state is "
        "`kind: combined`.\n"
        "- Output JSON only. No markdown fence, no commentary outside the JSON."
    )

    skeleton_yaml = yaml.safe_dump(skeleton, sort_keys=False)
    user_parts = [
        "===== THE SCIENTIST'S NOTES (sample.md) =====",
        notes.strip() or "(empty -- the scientist has not written anything yet)",
        "",
        "===== WHAT IS ON DISK =====",
        "This part is already correct. Do not change it; use its names.",
        "",
        skeleton_yaml,
    ]
    if facts:
        user_parts += ["===== MEASURED FROM THE DATA =====", facts]
    return system, "\n".join(user_parts)


def parse_proposal(reply: str) -> Proposal:
    """Read a model's reply into a proposal.

    Args:
        reply: The raw reply text.

    Returns:
        The parsed proposal, with disallowed keys recorded in ``rejected``.

    Raises:
        AuthoringError: If no JSON object can be found in the reply.
    """
    payload = _extract_json(reply)
    if payload is None:
        raise AuthoringError(
            "The reply contained no JSON object. First 200 characters:\n"
            f"  {reply.strip()[:200]}"
        )

    proposal = Proposal(raw=reply, notes=str(payload.get("notes") or "").strip())
    for key, value in payload.items():
        if key == "notes":
            continue
        if key in PROPOSABLE:
            proposal.document[key] = value
        else:
            proposal.rejected.append(key)
    return proposal


def merge_proposal(skeleton: dict[str, Any], proposal: Proposal) -> dict[str, Any]:
    """Merge a proposal into the scaffolded skeleton.

    The skeleton wins for anything read from disk. This is the enforcement
    point for that division: only :data:`PROPOSABLE` keys are taken, whatever
    the model returned and whatever the prompt asked for.

    Args:
        skeleton: The scaffolded spec.
        proposal: What the model suggested.

    Returns:
        A new merged document.
    """
    merged = dict(skeleton)
    for key in PROPOSABLE:
        value = proposal.document.get(key)
        if value:
            merged[key] = value
    _repair_constraints(merged)
    return merged


def _repair_constraints(document: dict[str, Any]) -> None:
    """Drop constraint paths naming layers the stack no longer has.

    The scaffold writes a `linear_in_time` constraint over `Film.thickness`,
    because `Film` is the placeholder stack's only layer. When a proposal
    replaces that stack, the constraint is left pointing at a layer that no
    longer exists and the spec does not validate -- an authored spec that fails
    its own `nrw model validate` is worse than the placeholder it replaced.

    Paths that survive are kept, so a proposal that renames the stack but keeps
    a thickness constraint still gets one. A constraint left with no paths is
    removed entirely.
    """
    layers = {
        str(entry.get("name"))
        for entry in document.get("stack", [])
        if isinstance(entry, dict) and entry.get("name")
    }
    if not layers:
        return

    kept = []
    for constraint in document.get("constraints", []) or []:
        if not isinstance(constraint, dict):
            continue
        paths = [
            path
            for path in constraint.get("paths", [])
            if not isinstance(path, str)
            or "." not in path
            or path.split(".", 1)[0] in layers
            or path.startswith("probe.")
        ]
        if paths:
            kept.append({**constraint, "paths": paths})

    if kept:
        document["constraints"] = kept
    else:
        document.pop("constraints", None)


def agent_instructions(
    *,
    spec_path: str,
    notes_path: str,
    skills: list[str],
    sample: str,
) -> str:
    """Write the instruction to hand a coding assistant.

    For the common case: VS Code with Claude Code or Copilot open on the
    project, no endpoint configured for nr-workbench itself. The assistant can
    already read every file named here, so the instruction points at them
    rather than inlining them.

    Args:
        spec_path: The scaffolded spec, relative to the project root.
        notes_path: The sample's ``sample.md``.
        skills: Skill names worth reading for this sample.
        sample: The sample identifier.

    Returns:
        A ready-to-paste instruction.
    """
    skill_lines = "\n".join(
        f"   - skills/reflectometry/{name}/SKILL.md" for name in skills
    )
    return f"""\
Fill in the model spec at {spec_path} for sample {sample}.

1. Read these skills first and follow them:
{skill_lines}

2. Read {notes_path} for what the sample is and what was done to it.

3. Edit ONLY these parts of the spec:
     description, materials, stack, parameters

   Leave `states`, `series`, `thetas`, `data_dir`, `run` and `reduced_dir`
   exactly as they are. Those were read from the data files' own headers and
   are already correct -- the incident angles in particular are measured
   values, not the nominal settings, so do not "tidy" 1.201 to 1.2.

4. Order the stack ambient first, substrate last. Prefer fewer layers: below
   about 30 A a layer is barely resolvable, so do not add one without a reason
   from the notes.

   Read the notes for instrument problems too, and add the matching nuisance
   parameter -- left out, each pushes its error into a layer:

     misaligned / angle uncertain
       {{path: probe.theta_offset, range: [-0.02, 0.02], per: state}}
     curved / bent / mosaic / fringes look damped
       {{path: probe.sample_broadening, range: [0.0, 0.05], per: state}}
     high background at high Q
       {{path: probe.background, range: [0.0, 1.0e-5], per: state}}

   The first two need per-angle data; scope with `in:` if a state is combined.

5. Check your work:
     nrw data features <one of the data files>   # critical edge -> top-layer SLD
     nrw model validate {spec_path}
     nrw model preview {spec_path} --build       # initial chi-squared

6. If the notes do not say what a layer is made of, leave a TODO comment rather
   than inventing a material.
"""


def _extract_json(reply: str) -> dict[str, Any] | None:
    """Pull the first JSON object out of a reply, fenced or bare."""
    text = reply.strip()

    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fenced:
        text = fenced.group(1)

    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except ValueError:
        pass

    # Fall back to the outermost braces, which handles a reply with a sentence
    # of preamble in front of the object.
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        value = json.loads(text[start : end + 1])
    except ValueError:
        return None
    return value if isinstance(value, dict) else None
