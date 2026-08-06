"""Filling in the physics half of a spec from the scientist's notes.

The safety property under test is a division of labour: a language model may
propose what the sample *is*, never what was *measured*. Angles, runs, file
paths and series come from the files on disk and are already exact, so a
plausible wrong answer there would be unrecoverable -- the fit would converge
and be wrong. The filter is structural, not a request in the prompt.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nr_workbench.spec.authoring import (
    CORE_SKILLS,
    PROPOSABLE,
    AuthoringError,
    Proposal,
    agent_instructions,
    build_prompt,
    find_skills,
    merge_proposal,
    missing_relevant,
    parse_proposal,
    relevant_skills,
)

SKELETON = {
    "schema": "nrw-model/1",
    "name": "m",
    "sample": "S1",
    "stack": [{"name": "Film", "material": "Film", "thickness": 100, "roughness": 5}],
    "states": [
        {
            "name": "ocv1",
            "run": 218386,
            "thetas": [0.45, 1.201, 3.5003],
            "data_dir": "samples/S1/data/steady",
        }
    ],
    "series": [{"name": "tnr", "run": 218389, "theta": 0.5997}],
    "constraints": [
        {
            "series": "tnr",
            "form": "linear_in_time",
            "from": "ocv1",
            "to": "ocv2",
            "paths": ["Film.thickness"],
        }
    ],
}

PROPOSED = {
    "description": "Cu on Ti on Si in d8-THF.",
    "materials": {"THF": {"rho": 6.35}, "Cu": {"rho": 6.55}, "Si": {"rho": 2.07}},
    "stack": [
        {"name": "THF", "material": "THF", "thickness": 0, "roughness": 20},
        {"name": "Cu", "material": "Cu", "thickness": 500, "roughness": 10},
        {"name": "Si", "material": "Si"},
    ],
    "parameters": [{"path": "Cu.thickness", "range": [400, 600], "per": "model"}],
    "notes": "The oxide thickness is a guess.",
}


# --------------------------------------------------------------------------
# The safety boundary
# --------------------------------------------------------------------------


def test_measured_facts_survive_a_proposal_that_tries_to_change_them() -> None:
    """Angles come from file headers and must not be overwritten.

    theta feeds `dT = dq/q * tan(theta)`, so a plausible wrong angle broadens
    every fringe and the fit absorbs it into roughness. This is the property
    the whole split exists to guarantee.
    """
    hostile = dict(PROPOSED)
    hostile["states"] = [{"name": "WRONG", "thetas": [9.9]}]
    hostile["series"] = [{"name": "WRONG", "theta": 9.9}]
    hostile["sample"] = "OTHER"

    proposal = parse_proposal(json.dumps(hostile))
    merged = merge_proposal(SKELETON, proposal)

    assert merged["states"] == SKELETON["states"]
    assert merged["series"] == SKELETON["series"]
    assert merged["sample"] == "S1"
    assert sorted(proposal.rejected) == ["sample", "series", "states"]


def test_only_the_physics_keys_are_accepted() -> None:
    """The allowed set is explicit, so a new key upstream cannot slip through."""
    proposal = parse_proposal(json.dumps(PROPOSED))

    assert set(proposal.document) <= set(PROPOSABLE)
    assert "description" in proposal.document
    assert proposal.notes == "The oxide thickness is a guess."


def test_a_proposal_replacing_the_stack_repairs_the_leftover_constraint() -> None:
    """The scaffold constrains `Film.thickness`; a new stack has no Film.

    Leaving it produces a spec that fails its own `nrw model validate` -- an
    authored spec that does not validate is worse than the placeholder it
    replaced.
    """
    merged = merge_proposal(SKELETON, parse_proposal(json.dumps(PROPOSED)))

    assert "constraints" not in merged, "a constraint with no valid path is dropped"


def test_a_constraint_keeps_the_paths_that_still_exist() -> None:
    """Only the dead paths go, not the whole constraint."""
    skeleton = dict(SKELETON)
    skeleton["constraints"] = [
        {
            "series": "tnr",
            "form": "linear_in_time",
            "from": "a",
            "to": "b",
            "paths": ["Film.thickness", "Cu.thickness", "probe.intensity"],
        }
    ]

    merged = merge_proposal(skeleton, parse_proposal(json.dumps(PROPOSED)))

    assert merged["constraints"][0]["paths"] == ["Cu.thickness", "probe.intensity"]


# --------------------------------------------------------------------------
# Parsing what a model actually returns
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "wrapper",
    [
        "{body}",
        "```json\n{body}\n```",
        "```\n{body}\n```",
        "Here is the spec you asked for:\n\n{body}\n\nLet me know if...",
    ],
)
def test_json_is_found_however_it_is_wrapped(wrapper: str) -> None:
    """Models fence, prefix and chatter. None of that should lose the answer."""
    reply = wrapper.format(body=json.dumps(PROPOSED))

    proposal = parse_proposal(reply)

    assert proposal.document["materials"]["Cu"]["rho"] == 6.55


def test_a_reply_with_no_json_is_an_error_not_an_empty_spec() -> None:
    """Silently writing the placeholder would hide that the call failed."""
    with pytest.raises(AuthoringError, match="no JSON object"):
        parse_proposal("I am unable to help with that request.")


def test_an_empty_proposal_leaves_the_skeleton_alone() -> None:
    merged = merge_proposal(SKELETON, Proposal())

    assert merged == SKELETON


# --------------------------------------------------------------------------
# Skill selection
# --------------------------------------------------------------------------


def test_core_skills_are_always_sent(project: Path) -> None:
    """The schema, the physics and the beamline conventions are never optional."""
    skills = find_skills(project)

    chosen = relevant_skills("", skills)

    assert [name for name in CORE_SKILLS if name in skills] == chosen


def test_notes_select_extra_skills(project: Path) -> None:
    """A sample in THF should bring the solvent skill, if it is installed."""
    skills = dict.fromkeys(
        [*CORE_SKILLS, "solvent-contrast-matching", "polymer-films"],
        Path("x"),
    )

    chosen = relevant_skills("Copper electrode in d8-THF", skills)

    assert "solvent-contrast-matching" in chosen
    assert "polymer-films" not in chosen, "nothing in the notes calls for it"


def test_relevant_but_uninstalled_skills_are_reported() -> None:
    """The most relevant skills are exactly the ones init does not seed.

    Silently proceeding without them wastes the context that makes the answer
    good, so they are named and `nrw skills sync` is suggested.
    """
    installed = dict.fromkeys(CORE_SKILLS, Path("x"))

    absent = missing_relevant("Copper oxide electrode in d8-THF", installed)

    assert "metal-oxide-interfaces" in absent
    assert "solvent-contrast-matching" in absent


# --------------------------------------------------------------------------
# The prompt, and its printed twin
# --------------------------------------------------------------------------


def test_the_prompt_carries_the_skills_and_forbids_the_facts(project: Path) -> None:
    skills = find_skills(project)

    system, user = build_prompt(skeleton=SKELETON, notes="Cu in THF", skills=skills)

    assert "nrw-model/1" in system
    assert "Do NOT return" in system
    for forbidden in ("states", "thetas", "data_dir"):
        assert forbidden in system
    assert "ocv1" in user, "the model needs the group names to write `in:` lists"
    assert "0.45" in user


def test_the_prompt_includes_measured_facts_when_given(project: Path) -> None:
    _, user = build_prompt(
        skeleton=SKELETON,
        notes="",
        skills=find_skills(project),
        facts="ocv1: critical edge Qc=0.0147 implies a topmost SLD near 4.28",
    )

    assert "critical edge" in user


def test_agent_instructions_name_the_files_and_the_boundary() -> None:
    """The assistant can read the repo, so point at files rather than inline them."""
    text = agent_instructions(
        spec_path="samples/S1/models/m.yaml",
        notes_path="samples/S1/sample.md",
        skills=["nrw-model-spec", "refl-bl4b-instrument"],
        sample="S1",
    )

    assert "samples/S1/models/m.yaml" in text
    assert "samples/S1/sample.md" in text
    assert "skills/reflectometry/nrw-model-spec/SKILL.md" in text
    assert "Leave `states`, `series`, `thetas`" in text
    assert "nrw model validate" in text
    assert 'do not "tidy" 1.201 to 1.2' in text


# --------------------------------------------------------------------------
# The command
# --------------------------------------------------------------------------


def sample_with_data(project: Path) -> Path:
    """Give the scaffolded project a sample with two segments and notes."""
    import numpy as np

    steady = project / "samples" / "Sample1" / "data" / "steady"
    steady.mkdir(parents=True, exist_ok=True)
    q = np.linspace(0.01, 0.2, 40)
    r = 1e-3 * (0.01 / q) ** 4
    body = "\n".join(
        f"{a:.6e} {b:.6e} {c:.6e} {d:.6e}"
        for a, b, c, d in zip(q, r, 0.05 * r, 0.02 * q, strict=True)
    )
    for segment, theta in ((1, 0.007853), (2, 0.020960)):
        (steady / f"REFL_100001_{segment}_10000{segment}_partial.txt").write_text(
            f'# Meta:{{"theta": {theta}, "run_number": 100001, '
            f'"sequence_number": {segment}, "norm_run": 99999}}\n' + body,
            encoding="utf-8",
        )
    (project / "samples" / "Sample1" / "sample.md").write_text(
        "# Sample1\n\nCopper electrode in d8-THF with a native oxide.\n",
        encoding="utf-8",
    )
    return project


def test_print_prompt_writes_the_skeleton_it_tells_the_agent_to_edit(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pointing an assistant at a file that does not exist is not a workflow."""
    from click.testing import CliRunner

    from nr_workbench.cli import main

    root = sample_with_data(project)
    monkeypatch.chdir(root)

    result = CliRunner().invoke(
        main, ["model", "new", "Sample1", "--name", "m", "--print-prompt"]
    )

    assert result.exit_code == 0, result.output
    assert (root / "samples/Sample1/models/m.yaml").is_file()
    assert "Fill in the model spec at" in result.output


def test_from_notes_without_an_endpoint_explains_the_other_path(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No endpoint is a normal state; the placeholder spec is still useful."""
    from click.testing import CliRunner

    from nr_workbench import aure_adapter
    from nr_workbench.cli import main

    root = sample_with_data(project)
    monkeypatch.chdir(root)
    monkeypatch.setattr(aure_adapter, "llm_info", lambda: {"available": False})

    result = CliRunner().invoke(
        main, ["model", "new", "Sample1", "--name", "m", "--from-notes"]
    )

    assert result.exit_code == 0, result.output
    assert "No language-model endpoint" in result.output
    assert "--print-prompt" in result.output
    assert (root / "samples/Sample1/models/m.yaml").is_file()


def test_from_notes_merges_a_proposal_and_records_who_made_it(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A proposed stack must be labelled as proposed, not presented as measured."""
    import yaml
    from click.testing import CliRunner

    from nr_workbench import aure_adapter
    from nr_workbench.cli import main

    root = sample_with_data(project)
    monkeypatch.chdir(root)
    monkeypatch.setattr(
        aure_adapter,
        "llm_info",
        lambda: {"available": True, "provider": "stub", "model": "test-model"},
    )
    monkeypatch.setattr(
        aure_adapter, "complete", lambda system, user, **kw: json.dumps(PROPOSED)
    )

    result = CliRunner().invoke(
        main, ["model", "new", "Sample1", "--name", "m", "--from-notes"]
    )

    assert result.exit_code == 0, result.output
    written = (root / "samples/Sample1/models/m.yaml").read_text(encoding="utf-8")

    assert "PROPOSED by stub/test-model" in written
    assert "PLACEHOLDER" not in written
    document = yaml.safe_load(written)
    assert [layer["name"] for layer in document["stack"]] == ["THF", "Cu", "Si"]
    # and the measured angles are still the ones read from the headers,
    # rounded but not rounded to the nominal settings
    assert document["states"][0]["thetas"] == [0.4499, 1.2009]


def test_from_notes_keeps_the_placeholder_when_the_reply_is_unusable(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A refusal or a garbled reply must not produce a broken spec."""
    from click.testing import CliRunner

    from nr_workbench import aure_adapter
    from nr_workbench.cli import main

    root = sample_with_data(project)
    monkeypatch.chdir(root)
    monkeypatch.setattr(
        aure_adapter,
        "llm_info",
        lambda: {"available": True, "provider": "stub", "model": "test-model"},
    )
    monkeypatch.setattr(
        aure_adapter, "complete", lambda system, user, **kw: "I cannot help with that."
    )

    result = CliRunner().invoke(
        main, ["model", "new", "Sample1", "--name", "m", "--from-notes"]
    )

    assert result.exit_code == 0, result.output
    assert "Keeping the placeholder stack" in result.output
    written = (root / "samples/Sample1/models/m.yaml").read_text(encoding="utf-8")
    assert "PLACEHOLDER" in written


def test_an_omitted_constraint_is_rebuilt_from_the_per_state_parameters() -> None:
    """A proposal often describes the constraint change instead of returning it.

    A real reply said "the existing linear_in_time constraint should be moved
    from Film.thickness to CuOx.thickness" -- in `notes` -- and omitted the
    block. Dropping it leaves every slice refitting the whole structure
    independently, which is never what was wanted and is invisible in the spec.

    The right paths are derivable: a parameter declared `per: state` across
    both endpoints is by definition something the experiment changed between
    them.
    """
    skeleton = {
        "stack": [{"name": "Film", "material": "Film", "thickness": 100}],
        "states": [{"name": "ocv1"}, {"name": "ocv2"}],
        "series": [{"name": "tnr"}],
        "constraints": [
            {"series": "tnr", "form": "linear_in_time", "from": "ocv1",
             "to": "ocv2", "paths": ["Film.thickness"]}
        ],
    }
    reply = json.dumps({
        "stack": [
            {"name": "CuOx", "material": "CuOx", "thickness": 40},
            {"name": "Cu", "material": "Cu", "thickness": 500},
            {"name": "Si", "material": "Si"},
        ],
        "parameters": [
            {"path": "CuOx.thickness", "range": [10, 80], "per": "state",
             "in": ["ocv1", "ocv2"]},
            {"path": "CuOx.rho", "range": [4, 5.5], "per": "model"},
            {"path": "probe.intensity", "value": 1.0, "pm": 0.1, "per": "state"},
        ],
    })

    merged = merge_proposal(skeleton, parse_proposal(reply))

    assert merged["constraints"] == [
        {"series": "tnr", "form": "linear_in_time", "from": "ocv1", "to": "ocv2",
         "paths": ["CuOx.thickness"]}
    ]


def test_the_rebuild_excludes_model_scoped_and_probe_parameters() -> None:
    """Only quantities that differ between the endpoints should interpolate.

    A `per: model` value is the same in both states, so interpolating it would
    be a no-op dressed up as physics; an intensity is a nuisance, not structure.
    """
    skeleton = {
        "stack": [{"name": "Film", "material": "Film"}],
        "states": [{"name": "a"}, {"name": "b"}],
        "series": [{"name": "s"}],
        "constraints": [
            {"series": "s", "form": "linear_in_time", "from": "a", "to": "b",
             "paths": ["Film.thickness"]}
        ],
    }
    reply = json.dumps({
        "stack": [{"name": "Cu", "material": "Cu"}],
        "parameters": [
            {"path": "Cu.rho", "range": [5, 7], "per": "model"},
            {"path": "probe.intensity", "value": 1.0, "pm": 0.1, "per": "state"},
        ],
    })

    merged = merge_proposal(skeleton, parse_proposal(reply))

    assert "constraints" not in merged, "nothing varies between the states"


def test_a_returned_constraint_is_preferred_over_a_rebuild() -> None:
    """The rebuild is a fallback, not an override."""
    skeleton = {
        "stack": [{"name": "Film", "material": "Film"}],
        "states": [{"name": "a"}, {"name": "b"}],
        "series": [{"name": "s"}],
        "constraints": [
            {"series": "s", "form": "linear_in_time", "from": "a", "to": "b",
             "paths": ["Film.thickness"]}
        ],
    }
    reply = json.dumps({
        "stack": [{"name": "Cu", "material": "Cu"}],
        "parameters": [
            {"path": "Cu.thickness", "range": [1, 2], "per": "state",
             "in": ["a", "b"]},
        ],
        "constraints": [
            {"series": "s", "form": "logistic", "from": "a", "to": "b",
             "paths": ["Cu.thickness"]}
        ],
    })

    merged = merge_proposal(skeleton, parse_proposal(reply))

    assert merged["constraints"][0]["form"] == "logistic"


def test_the_prompt_lists_the_constraint_forms_and_how_to_choose(
    project: Path,
) -> None:
    """"How do I ask for a linear constraint" must have an answer in the prompt."""
    system, _ = build_prompt(
        skeleton=SKELETON, notes="", skills=find_skills(project)
    )

    for form in ("linear_in_time", "logistic", "exponential", "piecewise_linear"):
        assert form in system
    assert "RETURN a `constraints` block" in system
    assert "oscillatory" in system.lower()
