"""szsdlc link and unlink: one edge, authored on one side."""

from __future__ import annotations

from szsdlc.errors import EXIT_BAD_INPUT

from .conftest import entities


# ---------------------------------------------------------------------------
# link / unlink
# ---------------------------------------------------------------------------
def test_link_reports_the_edge_and_its_generated_inverse(run, project):
    run("new", "epic", "--title", "E")
    run("new", "work_item", "--title", "W")
    code, output, _ = run("link", "WI-0001", "parent", "EPIC-0001")
    assert code == 0
    assert output.strip() == "WI-0001 parent EPIC-0001  (EPIC-0001 children WI-0001)"

    text = entities(project).by_text("EPIC-0001").path.read_text(encoding="utf-8")
    assert "children" not in text


def test_a_single_valued_relation_replaces_rather_than_appends(run, project):
    for title in ("A", "B"):
        run("new", "epic", "--title", title)
    run("new", "work_item", "--title", "W")
    run("link", "WI-0001", "parent", "EPIC-0001")
    run("link", "WI-0001", "parent", "EPIC-0002")
    assert entities(project).by_text("WI-0001").targets("parent") == ["EPIC-0002"]


def test_a_many_relation_appends(run, project):
    for title in ("A", "B"):
        run("new", "requirement", "--title", title)
    run("new", "work_item", "--title", "W")
    run("link", "WI-0001", "implements", "REQ-0001")
    run("link", "WI-0001", "implements", "REQ-0002")
    assert entities(project).by_text("WI-0001").targets("implements") == \
        ["REQ-0001", "REQ-0002"]


def test_linking_a_disallowed_target_type_lists_what_is_allowed(run, project):
    run("new", "work_item", "--title", "A")
    run("new", "work_item", "--title", "B")
    code, _, err = run("link", "WI-0001", "parent", "WI-0002")
    assert code == EXIT_BAD_INPUT
    assert "may not point at a work_item" in err
    assert "epic" in err


def test_authoring_a_generated_inverse_is_refused_with_the_real_direction(run, project):
    run("new", "epic", "--title", "E")
    run("new", "work_item", "--title", "W")
    code, _, err = run("link", "EPIC-0001", "children", "WI-0001")
    assert code == EXIT_BAD_INPUT
    assert "generated inverse" in err
    assert "Fix: szsdlc link <ref> parent EPIC-0001" in err


def test_a_relation_this_type_cannot_author_suggests_a_near_one(run, project):
    run("new", "requirement", "--title", "R")
    run("new", "work_item", "--title", "W")
    code, _, err = run("link", "REQ-0001", "implement", "WI-0001")
    assert code == EXIT_BAD_INPUT
    assert "cannot author" in err


def test_linking_to_itself_is_refused(run, project):
    run("new", "epic", "--title", "A")
    run("new", "epic", "--title", "B")
    code, _, err = run("link", "EPIC-0001", "parent", "EPIC-0001")
    assert code == EXIT_BAD_INPUT
    assert "cannot parent itself" in err


def test_linking_the_same_edge_twice_is_refused(run, seeded):
    code, _, err = run("link", "WI-0001", "parent", "EPIC-0001")
    assert code == EXIT_BAD_INPUT
    assert "already parent" in err


def test_unlink_reports_what_remains(run, project):
    for title in ("A", "B"):
        run("new", "requirement", "--title", title)
    run("new", "work_item", "--title", "W")
    run("link", "WI-0001", "implements", "REQ-0001")
    run("link", "WI-0001", "implements", "REQ-0002")

    code, output, _ = run("unlink", "WI-0001", "implements", "REQ-0001")
    assert code == 0
    assert output.strip() == "WI-0001 implements: REQ-0002"


def test_unlinking_an_absent_edge_shows_what_is_there(run, seeded):
    code, _, err = run("unlink", "WI-0001", "implements", "REQ-0001")
    assert code == EXIT_BAD_INPUT
    assert "does not implements" in err


def test_a_dangling_edge_can_still_be_unlinked(run, seeded, project):
    """The one moment the target genuinely cannot be resolved."""
    entity = entities(project).by_text("WI-0001")
    entity.set_relation("implements", ["REQ-0420"])
    entity.save()
    code, output, _ = run("unlink", "WI-0001", "implements", "REQ-0420")
    assert code == 0
    assert output.strip() == "WI-0001 implements: (none)"
