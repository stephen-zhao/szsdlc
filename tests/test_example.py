"""The example project is the genericity claim, run as a test.

`examples/research-lab` declares five entity types, six relations, three
horizons and one record, and not one of them is a shipped default. It is a wet
lab: no work items, no requirements, no epics, no sprints, no software.

The claim under test is that **no module in szsdlc names an entity type**.
Prose can assert that; only running the shipped views against a configuration
with zero overlap can demonstrate it. Everything below would pass just as well
if the framework quietly special-cased `requirement` somewhere — which is why
the first test checks the *absence* of the default vocabulary rather than
trusting that the example is exotic enough.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from szsdlc import config as C
from szsdlc import validate as V
from szsdlc.graph import Graph
from szsdlc.ids import IdSpace
from szsdlc.model import load_all
from szsdlc.render import Renderer, compare
from szsdlc.roadmap import load_all as load_roadmaps

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "research-lab"

#: Every type, relation and horizon the framework ships. None may appear.
DEFAULT_VOCABULARY = {
    "idea", "epic", "requirement", "spike", "work_item", "decision",
    "implements", "depends_on", "refined_from", "informed_by",
    "now", "next", "later",
}


@pytest.fixture(scope="module")
def lab():
    return C.load(EXAMPLE)


@pytest.fixture(scope="module")
def world(lab):
    ids = IdSpace(lab)
    store = load_all(lab, ids)
    return lab, store, Graph(lab, store), load_roadmaps(lab)


# ---------------------------------------------------------------------------
# The claim
# ---------------------------------------------------------------------------


def test_the_example_shares_no_vocabulary_with_the_defaults(lab):
    used = set(lab.entity_types) | set(lab.relations)
    for spec in lab.roadmaps.values():
        used |= set(spec.horizons)
    assert not (used & DEFAULT_VOCABULARY), sorted(used & DEFAULT_VOCABULARY)


def test_it_declares_a_full_project(lab):
    assert len(lab.entity_types) == 5
    assert len(lab.records) == 1
    assert [list(spec.horizons) for spec in lab.roadmaps.values()] == [
        ["this-quarter", "next-quarter", "someday"]]


def test_capability_flags_carve_the_same_joints_in_a_wet_lab(lab):
    """The shipped `requirement` and this project's `hypothesis` land on
    identical flags — a long-lived definition, never scheduled, never worked —
    in domains that share no words at all."""
    hypothesis = lab.entity_types["hypothesis"]
    assert hypothesis.persistent and not hypothesis.actionable
    assert not hypothesis.schedulable and not hypothesis.can_parent

    experiment = lab.entity_types["experiment"]
    assert experiment.actionable and experiment.tracks_progress
    assert experiment.schedulable and not experiment.persistent


# ---------------------------------------------------------------------------
# It actually works
# ---------------------------------------------------------------------------


def test_the_example_is_valid(world):
    findings = V.run(*world)
    assert [f for f in findings if f.is_error] == []


def test_every_generated_file_is_current(world):
    """Committed generated output matches what this code renders right now.

    Which makes the example a canary: change a shipped template and this fails
    until the example is re-synced, so the committed sample can never drift
    into showing output the tool no longer produces.
    """
    assert compare(Renderer(*world).all()) == []


def test_the_shipped_views_render_this_project_in_its_own_words(world):
    rendered = {item.name: item.body for item in Renderer(*world).all()}

    register = rendered["hypothesis-register"]
    assert "# Hypothesis register" in register
    assert "Requirements register" not in register
    # Derived over `tests`, a relation the framework has never heard of.
    assert "| HYP-001 | supported | ✓ | ✓ | EXP-001 |" in register

    assert "# Bench schedule" in rendered["bench-schedule"]
    assert "this-quarter" in rendered["bench-schedule"]

    assert "# Open questions" in rendered["open-questions"]


def test_the_project_specific_record_renders(world):
    body = {item.name: item.body for item in Renderer(*world).all()}["instruments"]
    assert "# Instrument calibration" in body
    # A YAML date reaching a `"type": "string"` schema, and surviving.
    assert "2026-05-02" in body


def test_derived_facts_come_from_the_graph_not_the_files(world):
    lab, store, graph, _ = world
    hypothesis = store.by_text("HYP-001")
    facts = graph.derived(hypothesis)
    assert facts["covered"]
    assert "covered" not in hypothesis.data
    assert "delivered" not in hypothesis.data
