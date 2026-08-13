"""The structure shorthand: ``THF|Cu|Ti|Si``.

A fit id identifies a run and describes nothing. `nrw ls` and the web fits page
were showing a model name, a chi-squared, and a change line that most often
read "first run of this model" -- none of which answers the question a
reflectometrist actually asks, which is *what was this a fit of*.

The two sources are tested against each other on purpose: a stack read off the
live problem and the same stack read back out of the frozen result directory
have to agree, or the listing changes its mind about a fit depending on how
old the fit is.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nr_workbench.provenance import stack

# --------------------------------------------------------------------------
# Formatting
# --------------------------------------------------------------------------


def test_layers_are_joined_in_beam_order() -> None:
    assert stack.format_layers(["THF", "Cu", "Ti", "Si"]) == "THF|Cu|Ti|Si"


def test_unnamed_layers_are_dropped_rather_than_shown_as_gaps() -> None:
    """`||` in the middle reads as a missing layer, not an unnamed one."""
    assert stack.format_layers(["THF", "", "Ti", "  "]) == "THF|Ti"


def test_nothing_named_gives_nothing() -> None:
    assert stack.format_layers([]) == ""
    assert stack.format_layers(["", "   "]) == ""


def test_a_long_stack_keeps_both_ends_and_elides_the_middle() -> None:
    """A 21-layer multilayer wraps a table row into three lines. The ambient
    and the substrate say what the sample is in and on; the middle can go."""
    names = ["D2O", "SEI", *[f"L{i}" for i in range(1, 16)], "Ti", "Si"]

    result = stack.format_layers(names)

    assert result.startswith("D2O|SEI|L1|L2")
    assert result.endswith("Ti|Si")
    assert "+13" in result, "the count of what is hidden, not a bare ellipsis"
    assert len(result.split("|")) < len(names)


def test_a_stack_exactly_at_the_limit_is_shown_in_full() -> None:
    names = [f"L{i}" for i in range(stack.MAX_LAYERS)]

    assert stack.format_layers(names).split("|") == names


# --------------------------------------------------------------------------
# From a live problem
# --------------------------------------------------------------------------


class _Material:
    def __init__(self, name: str) -> None:
        self.name = name


class _Layer:
    def __init__(self, name: str | None, material: str | None = None) -> None:
        self.name = name
        self.material = _Material(material) if material else None


class _Model:
    def __init__(self, layers: list[_Layer]) -> None:
        self.sample = layers


class _Problem:
    def __init__(self, models: list[_Model]) -> None:
        self._models = models

    @property
    def models(self):
        # bumps hands back a generator, not a list -- reading it twice reads
        # it empty, which is the shape of bug this mirrors deliberately.
        return (model for model in self._models)


def test_a_problem_reports_its_stack() -> None:
    problem = _Problem([_Model([_Layer("THF"), _Layer("Cu"), _Layer("Si")])])

    assert stack.describe(problem) == "THF|Cu|Si"


def test_states_sharing_one_stack_report_it_once() -> None:
    """A spec declares one stack and instantiates it per state, so a
    ten-state co-refinement must not print the same structure ten times."""
    layers = [_Layer("THF"), _Layer("Cu"), _Layer("Si")]
    problem = _Problem([_Model(layers), _Model(layers), _Model(layers)])

    assert stack.describe(problem) == "THF|Cu|Si"


def test_a_genuine_co_refinement_of_two_structures_reports_both() -> None:
    """Reporting only the first would describe half the fit."""
    problem = _Problem(
        [
            _Model([_Layer("THF"), _Layer("Cu"), _Layer("Si")]),
            _Model([_Layer("D2O"), _Layer("Ti"), _Layer("Si")]),
        ]
    )

    assert stack.describe(problem) == "THF|Cu|Si / D2O|Ti|Si"


def test_a_layer_with_no_name_falls_back_to_its_material() -> None:
    problem = _Problem([_Model([_Layer(None, "THF"), _Layer("Cu"), _Layer("Si")])])

    assert stack.describe(problem) == "THF|Cu|Si"


def test_a_problem_that_cannot_be_read_costs_nothing() -> None:
    """A label is not worth failing a fit for."""

    class Hostile:
        @property
        def models(self):
            raise RuntimeError("no models here")

    assert stack.describe(Hostile()) == ""
    assert stack.describe(object()) == ""


# --------------------------------------------------------------------------
# From a frozen result directory
#
# This is the path that matters for a project that already has fits in it:
# every one of them predates the stack being recorded in the index.
# --------------------------------------------------------------------------


def write_export(directory: Path, names: list[str], index: int = 1) -> None:
    """Write a bumps ``*-expt.json`` holding a named stack."""
    (directory / "fit").mkdir(parents=True, exist_ok=True)
    (directory / "fit" / f"problem-{index}-expt.json").write_text(
        json.dumps(
            {
                "$schema": "bumps-draft-03",
                "object": {
                    "sample": {
                        "name": "Stack",
                        "layers": [
                            {"name": name, "material": {"name": name}} for name in names
                        ],
                    }
                },
            }
        ),
        encoding="utf-8",
    )


def test_the_stack_is_recovered_from_the_bumps_export(tmp_path: Path) -> None:
    write_export(tmp_path, ["THF", "Cu", "Ti", "Si"])

    assert stack.from_fit_dir(tmp_path) == "THF|Cu|Ti|Si"


def test_the_frozen_spec_answers_when_there_is_no_export(tmp_path: Path) -> None:
    """A fit that failed before bumps exported anything is exactly the fit
    somebody is trying to tell apart from the one before it."""
    pytest.importorskip("yaml")
    (tmp_path / "spec.yaml").write_text(
        "schema: nrw-model/1\nname: m\nstack:\n"
        "  - {name: D2O, material: D2O}\n"
        "  - {name: Cu, material: Cu}\n"
        "  - {name: Si, material: Si}\n",
        encoding="utf-8",
    )

    assert stack.from_fit_dir(tmp_path) == "D2O|Cu|Si"


def test_a_directory_with_neither_source_says_nothing(tmp_path: Path) -> None:
    assert stack.from_fit_dir(tmp_path) == ""


def test_unreadable_files_are_not_an_error(tmp_path: Path) -> None:
    """A half-written export must degrade the label, not the page."""
    (tmp_path / "fit").mkdir()
    (tmp_path / "fit" / "problem-1-expt.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "spec.yaml").write_text(": : :\n", encoding="utf-8")

    assert stack.from_fit_dir(tmp_path) == ""


def test_the_export_wins_over_the_spec(tmp_path: Path) -> None:
    """The export is what was fitted; the spec is what was asked for. They
    differ exactly when a generated script was edited by hand afterwards."""
    pytest.importorskip("yaml")
    write_export(tmp_path, ["THF", "Cu", "CuO", "Si"])
    (tmp_path / "spec.yaml").write_text(
        "stack:\n  - {name: THF}\n  - {name: Cu}\n  - {name: Si}\n", encoding="utf-8"
    )

    assert stack.from_fit_dir(tmp_path) == "THF|Cu|CuO|Si"
