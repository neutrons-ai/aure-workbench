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
PROPOSABLE = (
    "description",
    "materials",
    "stack",
    "parameters",
    "constraints",
    # Choices, not facts. `series_select` says which slices to model and
    # `probe.back_reflection` says which side the beam enters -- both are in
    # the scientist's notes and neither is derivable from the file listing.
    "series_select",
    "probe",
)

#: The only probe keys a proposal may set. `resolution` is fixed by the
#: schema and `dq_is_fwhm` is a property of the reduction, not something to
#: infer from prose.
PROPOSABLE_PROBE = ("back_reflection",)

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
        dropped_paths: Constrained paths removed for having no range.
        notes: Any commentary it offered, for the human to read.
        raw: The reply as received, for debugging.
    """

    document: dict[str, Any] = field(default_factory=dict)
    rejected: list[str] = field(default_factory=list)
    dropped_paths: list[str] = field(default_factory=list)
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
        '  "parameters"  : [ {path, range|value, per, in, ...}, ... ]\n'
        '  "constraints" : [ {series, form, from, to, paths} ] -- REQUIRED\n'
        "                  whenever the skeleton has a series\n"
        '  "series_select": {"<series name>": {"labels": ["*eis*"]}} --\n'
        "                  when the notes restrict which slices to model\n"
        '  "probe"       : {"back_reflection": true} -- ONLY if the notes\n'
        "                  say the beam enters through the substrate or\n"
        "the back of the sample\n"
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
        "- If the skeleton has a `series`, RETURN a `constraints` block. Do "
        "not describe the change you would make in `notes` and omit the block "
        "-- an omitted constraint leaves every slice refitting the whole "
        "structure independently. Without one "
        "every slice refits the whole structure independently, which is almost "
        "never what is wanted. Choose the form from the evidence, in this "
        "order of preference:\n"
        "    linear_in_time    a(t) rises steadily; slices unevenly spaced.\n"
        "                      THE DEFAULT for a monotonic change.\n"
        "    linear_in_index   same, but only if slices are evenly spaced.\n"
        "    logistic          a(t) is sigmoidal -- induction, transition,\n"
        "                      plateau. Fits t_half and width.\n"
        "    exponential       a first-order relaxation to equilibrium.\n"
        "                      Fits tau.\n"
        "    piecewise_linear  structure no closed form captures. Costs K\n"
        "                      knots; use only if the simpler forms fail.\n"
        "    free              one parameter per slice. Last resort.\n"
        "    fixed             nothing changes across the series.\n"
        "  `from` and `to` are the steady states either side of the series, so "
        "the interpolating forms add no free parameters. Write `free` for "
        "either endpoint to fit it instead -- one extra parameter per path. "
        "Do that when the series has no bracketing state, or when the notes "
        "say something happened between the steady measurement and the run.\n"
        "  `paths` decides what is allowed to change across the series. The "
        "NOTES are authoritative here -- if they say which parameters change, "
        'list exactly those, and "the other parameters, even the Ti layer, '
        'will change" means include them all. Only when the notes are silent, '
        "fall back to the assessment: an oscillatory template in Q means a "
        "THICKNESS change, one-sign means an SLD contrast change.\n"
        "- Give every free parameter a physically sensible range, not a wide "
        "one.\n"
        "- If the notes do not say what a layer is made of, say so in `notes` "
        "rather than inventing a material.\n"
        "- SCOPE every parameter from the words the notes use. This mapping "
        "is the most common source of a wrong model, and the notes are usually "
        "explicit about it:\n"
        '    "each partial data file", "per segment", "each angle"\n'
        "      -> per: measurement\n"
        '    "common for all time slices", "one for the series"\n'
        "      -> per: state, in: [<the series>]\n"
        '    "common to all data", "the same everywhere", "shared"\n'
        "      -> per: model\n"
        '    "per state", "may differ between the two states"\n'
        "      -> per: state, in: [<the steady states>]\n"
        "  A structural parameter declared `per: state` with NO `in:` covers "
        "the series too and will collide with the constraint. Always scope "
        "structural parameters with `in:` when a series is present.\n"
        "- If the notes say the series should NOT be tied to the states either "
        'side -- "not tied", "there was a lag", "do not anchor", "the '
        'endpoints should be fitted" -- use `from: free` and `to: free` on the '
        "constraint instead of naming the states. Each free endpoint adds one "
        "parameter per path and is the right answer when the states do not "
        "continue smoothly into the run.\n"
        '- If the notes restrict which slices to model -- "only the eis '
        'data", "never the hold intervals", "the string to look for is X" -- '
        'set `series_select`, e.g. {"tnr218389": {"labels": ["*eis*"]}}.\n'
        "- Read the notes for *instrument* problems as well as sample "
        "composition, and add the matching nuisance parameter when one is "
        "described. These are easy to miss and each one, left out, pushes its "
        "error into a layer:\n"
        "    sample misaligned / angle uncertain / offset\n"
        "      -> {path: probe.theta_offset, range: [-0.02, 0.02], per: ...}\n"
        "    sample curved / bent / warped / mosaic / fringes damped\n"
        "      -> {path: probe.sample_broadening, range: [0.0, 0.05], per: ...}\n"
        "    high background / poor statistics at high Q\n"
        "      -> {path: probe.background, range: [0.0, 1.0e-5], per: state}\n"
        "  SCOPE the first two by asking whether the sample was physically "
        "MOVED, not whether it changed. They describe how it sits in the beam. "
        "An in-situ cell measured continuously -- an OCV, a tNR run, another "
        "OCV -- is never remounted, so use `per: model`: one alignment for the "
        "whole experiment. Use `per: state` only if the notes say the sample "
        "was remounted, moved, or realigned between measurements. Fitting one "
        "per state on a sample that never moved is several parameters "
        "describing one quantity, and they absorb the real differences "
        "between the states.\n"
        "  `probe.intensity` is the exception and is nearly always `per: "
        "state`: each reduction used its own direct beam.\n"
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
        if key in ("series_select", "probe"):
            continue
        value = proposal.document.get(key)
        if value:
            merged[key] = value

    _apply_series_select(merged, proposal.document.get("series_select"))
    _apply_probe(merged, proposal.document.get("probe"))
    pinned = _drop_model_scoped_from_constraints(merged)
    _scope_constrained_parameters(merged)
    _repair_constraints(merged)
    proposal.dropped_paths = pinned + _drop_unrangeable_paths(merged)
    return merged


def _drop_unrangeable_paths(document: dict[str, Any]) -> list[str]:
    """Remove constrained paths that a free endpoint could not be bounded from.

    A `free` endpoint borrows its range from the path's `parameters`
    declaration. A path listed in `paths` with no declaration and no
    `endpoint_range` therefore has nothing to bound it, and resolution refuses
    -- correctly, since an unbounded endpoint drags the whole trajectory.

    Inventing a range would be the wrong repair: a plausible-looking interval
    on an SLD is exactly the kind of guess this package exists to avoid. So the
    path is dropped and named, and the human can declare it and regenerate.

    Args:
        document: The merged spec.

    Returns:
        The paths removed, for the caller to report.
    """
    from nr_workbench.spec.constraints import FREE_ENDPOINT

    declared = {
        str(parameter.get("path"))
        for parameter in document.get("parameters") or []
        if isinstance(parameter, dict) and parameter.get("range") is not None
    }

    dropped: list[str] = []
    kept_constraints = []
    for constraint in document.get("constraints") or []:
        if not isinstance(constraint, dict):
            continue
        free_ends = FREE_ENDPOINT in (constraint.get("from"), constraint.get("to"))
        if not free_ends or constraint.get("endpoint_range"):
            kept_constraints.append(constraint)
            continue
        paths = []
        for path in constraint.get("paths") or []:
            if str(path) in declared:
                paths.append(path)
            else:
                dropped.append(str(path))
        if paths:
            kept_constraints.append({**constraint, "paths": paths})

    if kept_constraints:
        document["constraints"] = kept_constraints
    elif "constraints" in document:
        document.pop("constraints")
    return dropped


def _scope_constrained_parameters(document: dict[str, Any]) -> None:
    """Scope `per: state` structural parameters away from the constrained series.

    `per: state` with no `in:` covers *every* group, series included, so a path
    that is also in a constraint's `paths` is assigned twice and the spec does
    not validate. The prompt asks for the `in:` and it is still omitted about
    half the time -- which is the signal that this belongs in code. A prompt is
    a request; producing a spec that validates is a correctness property.

    Only paths the constraint already owns are touched, and only when no `in:`
    was given, so a deliberate scoping is never overridden.
    """
    states = [
        str(state.get("name"))
        for state in document.get("states") or []
        if isinstance(state, dict) and state.get("name")
    ]
    if not states:
        return

    constrained: set[str] = set()
    for constraint in document.get("constraints") or []:
        if isinstance(constraint, dict):
            constrained.update(str(path) for path in constraint.get("paths") or [])
    if not constrained:
        return

    for parameter in document.get("parameters") or []:
        if not isinstance(parameter, dict):
            continue
        if parameter.get("per") != "state" or parameter.get("in"):
            continue
        if str(parameter.get("path")) in constrained:
            parameter["in"] = list(states)


def _drop_model_scoped_from_constraints(document: dict[str, Any]) -> list[str]:
    """Remove `per: model` paths from constraint paths.

    `per: model` is an explicit statement that a quantity is the same
    everywhere -- "use the steady states to pin down the copper SLD" -- and a
    constraint gives it a per-slice trajectory. The two contradict, and the
    spec does not validate.

    The declaration wins: it is the more specific statement of intent, and it
    is what the notes usually say in so many words.

    Returns:
        The paths removed, for the caller to report.
    """
    pinned = {
        str(parameter.get("path"))
        for parameter in document.get("parameters") or []
        if isinstance(parameter, dict) and parameter.get("per") == "model"
    }
    if not pinned:
        return []

    removed: list[str] = []
    kept = []
    for constraint in document.get("constraints") or []:
        if not isinstance(constraint, dict):
            continue
        paths = []
        for path in constraint.get("paths") or []:
            if str(path) in pinned:
                removed.append(str(path))
            else:
                paths.append(path)
        if paths:
            kept.append({**constraint, "paths": paths})

    if kept:
        document["constraints"] = kept
    elif "constraints" in document:
        document.pop("constraints")
    return removed


def _apply_series_select(document: dict[str, Any], selection: Any) -> None:
    """Set `select` on the named series, leaving every other field alone.

    Which slices to model is a choice -- "only the eis intervals, never the
    holds" -- while the run number, directory and angle are facts read off the
    disk. So the selection is merged in rather than the series being replaced.
    """
    if not isinstance(selection, dict):
        return
    for series in document.get("series") or []:
        if not isinstance(series, dict):
            continue
        chosen = selection.get(str(series.get("name")))
        if isinstance(chosen, dict) and chosen:
            series["select"] = chosen


def _apply_probe(document: dict[str, Any], probe: Any) -> None:
    """Merge the probe keys a proposal is allowed to set."""
    if not isinstance(probe, dict):
        return
    existing = dict(document.get("probe") or {})
    for key in PROPOSABLE_PROBE:
        if key in probe:
            existing[key] = probe[key]
    document["probe"] = existing


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

    if not kept:
        kept = _rebuild_constraints(document, layers)

    if kept:
        document["constraints"] = kept
    else:
        document.pop("constraints", None)


def _rebuild_constraints(
    document: dict[str, Any], layers: set[str]
) -> list[dict[str, Any]]:
    """Re-aim a constraint at the paths the new stack actually varies.

    A proposal often replaces the stack and describes the constraint change in
    prose instead of returning one -- a real reply said "the existing
    linear_in_time constraint should be moved from Film.thickness to
    CuOx.thickness" and then omitted the block. Dropping it leaves every slice
    refitting the whole structure independently, which is never what was
    wanted and is invisible in the spec.

    The right paths are derivable: a parameter declared `per: state` across the
    endpoint states is, by definition, a quantity the experiment changed
    between them, so interpolating it across the series is exactly what the
    form is for.

    Args:
        document: The merged spec.
        layers: Layer names present in the stack.

    Returns:
        A single rebuilt constraint, or an empty list if nothing qualifies.
    """
    series = document.get("series") or []
    states = document.get("states") or []
    if not series or len(states) < 2:
        return []

    endpoints = [str(state.get("name")) for state in states[:1] + states[-1:]]
    paths = []
    for parameter in document.get("parameters") or []:
        if not isinstance(parameter, dict):
            continue
        path = str(parameter.get("path", ""))
        if parameter.get("per") != "state" or path.startswith("probe."):
            continue
        if "." not in path or path.split(".", 1)[0] not in layers:
            continue
        targets = [str(t) for t in (parameter.get("in") or endpoints)]
        if all(name in targets for name in endpoints):
            paths.append(path)

    if not paths:
        return []
    return [
        {
            "series": str(series[0].get("name")),
            "form": "linear_in_time",
            "from": endpoints[0],
            "to": endpoints[-1],
            "paths": paths,
        }
    ]


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
       {{path: probe.theta_offset, range: [-0.02, 0.02], per: ...}}
     curved / bent / mosaic / fringes look damped
       {{path: probe.sample_broadening, range: [0.0, 0.05], per: ...}}
     high background at high Q
       {{path: probe.background, range: [0.0, 1.0e-5], per: state}}

   Scope the first two by asking whether the sample was physically MOVED, not
   whether it changed -- they describe how it sits in the beam. An in-situ cell
   measured continuously is never remounted, so `per: model`: one alignment for
   the whole experiment. `per: state` only if the notes say it was remounted or
   realigned. `probe.intensity` is nearly always `per: state`, since each
   reduction used its own direct beam.

   Both need per-angle data; scope with `in:` if a state is combined.

5. If the spec has a `series`, it needs a `constraints:` block -- without one,
   every slice refits the whole structure independently. Run
   `nrw model forms` for the list. Choose from the evidence:

     a(t) rises steadily        -> linear_in_time   (the usual answer)
     a(t) is sigmoidal          -> logistic         (fits t_half, width)
     first-order relaxation     -> exponential      (fits tau)
     none of the above fits     -> piecewise_linear (costs K knots)

   `from`/`to` name the states either side. Write `free` for either to fit
   that endpoint instead -- for a series with no bracketing state, or when
   where the sample finished is itself the measurement.

   `nrw tnr assess` names the right one in its verdict -- read
   `assessments/*/[label]_assessment.json` if it has been run. An oscillatory
   template means a THICKNESS change; one-sign means an SLD contrast change,
   and `paths` should say which.

6. Check your work:
     nrw data features <one of the data files>   # critical edge -> top-layer SLD
     nrw model validate {spec_path}
     nrw model preview {spec_path} --build       # initial chi-squared

7. If the notes do not say what a layer is made of, leave a TODO comment rather
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
