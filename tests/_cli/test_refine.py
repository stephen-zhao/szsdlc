"""szsdlc refine: spawning a typed entity from an idea."""

from __future__ import annotations

from szsdlc.errors import EXIT_BAD_INPUT

from .conftest import entities


# ---------------------------------------------------------------------------
# refine
# ---------------------------------------------------------------------------
def test_refine_spawns_a_typed_entity_and_reports_both(run, project):
    run("capture", "valkey needs a sentinel quorum story")
    code, output, _ = run("refine", "IDEA-0001", "--into", "spike",
                          "--title", "Sentinel quorum under partition")
    assert code == 0
    # C1 — the resulting state, so no confirming `show` is needed.
    assert output.strip() == "SPK-0001  (IDEA-0001: inbox → refined)"

    spike = entities(project).by_text("SPK-0001")
    assert spike.title == "Sentinel quorum under partition"
    # Provenance is written on the child, the single authored side.
    assert spike.targets("refined_from") == ["IDEA-0001"]


def test_one_idea_yields_several_entities(run, project):
    """The normal case — "a set of related thoughts" requires it."""
    run("capture", "a page of related thoughts")
    run("refine", "IDEA-0001", "--into", "spike", "--title", "Investigate")
    run("refine", "IDEA-0001", "--into", "epic", "--title", "The outcome")
    code, _, _ = run("refine", "IDEA-0001", "--into", "work_item", "--title", "Do it")
    assert code == 0

    store = entities(project)
    spawned = {e.id.text: e.targets("refined_from") for e in store
               if e.targets("refined_from")}
    assert spawned == {"SPK-0001": ["IDEA-0001"], "EPIC-0001": ["IDEA-0001"],
                       "WI-0001": ["IDEA-0001"]}
    # Nothing was renamed and no tombstone was needed; the idea is provenance.
    assert store.by_text("IDEA-0001") is not None


def test_the_idea_is_never_written_to_by_the_child(run, project):
    run("capture", "a thought")
    idea_path = entities(project).by_text("IDEA-0001").path
    run("refine", "IDEA-0001", "--into", "spike", "--title", "S")
    after_first = idea_path.read_bytes()

    run("refine", "IDEA-0001", "--into", "work_item", "--title", "W")
    # The second spawn changes nothing about the idea: `refined_into` is
    # generated from the children's edges, never stored here.
    assert idea_path.read_bytes() == after_first
    assert b"refined_into" not in after_first


def test_the_title_defaults_to_the_ideas_derived_title(run, project):
    run("capture", "valkey needs a sentinel quorum story")
    run("refine", "IDEA-0001", "--into", "work_item")
    assert entities(project).by_text("WI-0001").title == \
        "valkey needs a sentinel quorum story"


def test_refining_a_dropped_idea_is_refused(run):
    run("capture", "a thought")
    run("drop", "IDEA-0001", "--reason", "already covered")
    code, _, err = run("refine", "IDEA-0001", "--into", "work_item")
    assert code == EXIT_BAD_INPUT
    assert "dropped idea is not refined further" in err
    assert "Fix: szsdlc set IDEA-0001 status=inbox" in err


def test_refining_a_typed_entity_is_refused(run):
    run("capture", "a thought")
    run("refine", "IDEA-0001", "--into", "work_item", "--title", "W")
    code, _, err = run("refine", "WI-0001", "--into", "spike")
    assert code == EXIT_BAD_INPUT
    assert "not an intake entity" in err


def test_refining_into_an_intake_type_is_refused(run):
    run("capture", "a thought")
    code, _, err = run("refine", "IDEA-0001", "--into", "idea")
    assert code == EXIT_BAD_INPUT
    assert "only make another idea" in err


def test_an_unknown_reference_suggests_the_nearest(run):
    run("capture", "a thought")
    code, _, err = run("refine", "IDEA-0010", "--into", "work_item")
    assert code == EXIT_BAD_INPUT
    assert "did you mean IDEA-0001" in err
