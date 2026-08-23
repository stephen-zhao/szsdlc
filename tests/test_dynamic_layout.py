"""Task 20 — `layout: dynamic`, and moving an entry between shapes.

The three layouts exist so that storage cost matches fidelity. `dynamic` is
the admission that fidelity is not known at capture time: a thought arrives as
a section of one shared file, earns its own file if it grows, and earns a
directory the moment something has to live beside it. Deciding at capture is
the same mistake as asserting a type at capture, and intake has been typeless
since Task 7 for exactly that reason.

What has to hold through a move:

- **The id never changes**, so nothing that referenced it has to be found and
  rewritten. Relations name ids, never paths, so this is free — but free only
  as long as nothing ever renumbers, which is asserted here.
- **The bytes never change.** A section is a whole entity document, so a move
  is a move rather than a re-serialisation.
- **A half-finished move is reportable.** The new shape is written before the
  old one is removed, so an interruption claims the id twice — which
  `validate` reports with both paths — rather than losing it.
"""

from __future__ import annotations

import pytest

from szsdlc import config as C
from szsdlc.cli import main
from szsdlc.errors import EXIT_BAD_INPUT
from szsdlc.graph import Graph
from szsdlc.ids import IdSpace
from szsdlc.model import create_entity, load_all, relayout
from szsdlc.validate import run as validate_run

SHAPES = ("section", "file", "directory")


@pytest.fixture
def run(project, capsys):
    def invoke(*argv: str):
        code = main(["-C", str(project.root), *argv])
        captured = capsys.readouterr()
        return code, captured.out, captured.err
    return invoke


def capture(project, body: str = "a loose thought"):
    return create_entity(project, IdSpace(project), project.type_for("idea"),
                         body=body + "\n")


def reload(project, id_text: str):
    return load_all(project).by_text(id_text)


def move(project, id_text: str, layout: str):
    entity = reload(project, id_text)
    return relayout(project, IdSpace(project), entity, layout)


# ---------------------------------------------------------------------------
# The type
# ---------------------------------------------------------------------------


def test_a_dynamic_type_accepts_all_three_shapes(project):
    idea = project.type_for("idea")
    assert idea.is_dynamic_layout
    assert set(idea.layouts) == set(SHAPES)
    # …and still arrives in the cheapest one.
    assert idea.new_entry_layout == "section"


def test_a_declared_layout_accepts_only_itself(make_project):
    project = make_project({"entity_types": {"idea": {"layout": "file"}}})
    assert project.type_for("idea").layouts == ("file",)
    assert not project.type_for("idea").is_dynamic_layout


def test_initial_layout_is_refused_on_a_type_with_one_shape(make_project):
    """A key that does nothing is a key someone is relying on."""
    with pytest.raises(C.ConfigError) as excinfo:
        make_project({"entity_types": {"idea": {"layout": "file",
                                                "initial_layout": "file"}}})
    assert "only one shape to arrive in" in excinfo.value.problem


def test_the_arrival_shape_is_configurable(make_project):
    project = make_project({"entity_types": {"idea": {"initial_layout": "file"}}})
    entity = capture(project)
    assert entity.layout == "file"
    assert entity.path.name.startswith("IDEA-0001")


# ---------------------------------------------------------------------------
# Moving
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("target", ["file", "directory"])
def test_an_entry_moves_out_of_the_shared_file(project, target):
    capture(project, "drafts are lost on refresh")
    before = reload(project, "IDEA-0001")

    moved = move(project, "IDEA-0001", target)

    assert moved.layout == target
    assert moved.id == before.id
    assert moved.render() == before.render()
    assert "IDEA-0001" not in project.section_path("idea").read_text(encoding="utf-8")
    assert reload(project, "IDEA-0001").layout == target


@pytest.mark.parametrize("start", ["file", "directory"])
def test_an_entry_moves_back_into_the_shared_file(project, start):
    capture(project, "drafts are lost on refresh")
    move(project, "IDEA-0001", start)
    before = reload(project, "IDEA-0001")

    moved = move(project, "IDEA-0001", "section")

    assert moved.layout == "section"
    assert moved.render() == before.render()
    assert moved.path == project.section_path("idea")


def test_a_move_leaves_the_other_entries_alone(project):
    for body in ("first", "second", "third"):
        capture(project, body)
    path = project.section_path("idea")
    before = path.read_text(encoding="utf-8")

    move(project, "IDEA-0002", "file")

    after = path.read_text(encoding="utf-8")
    # Byte-for-byte what was there, minus the one entry that left.
    head, rest = before.split("## IDEA-0002", 1)
    assert after == head + "## IDEA-0003" + rest.split("## IDEA-0003", 1)[1]


def test_a_move_never_renumbers(project):
    """Every relation names an id. A move that reissued one would silently
    repoint every reference to it."""
    capture(project, "drafts are lost on refresh")
    for target in ("file", "directory", "section"):
        assert move(project, "IDEA-0001", target).id.text == "IDEA-0001"
    assert IdSpace(project).next_id("idea").text == "IDEA-0002"


def test_relations_survive_a_move(project):
    idea = capture(project, "drafts are lost on refresh")
    work = create_entity(project, IdSpace(project), project.type_for("work_item"),
                         title="Autosave", relations={"refined_from": idea.id.text})

    move(project, "IDEA-0001", "directory")

    store = load_all(project)
    graph = Graph(project, store, IdSpace(project))
    assert store.by_text(work.id.text).targets("refined_from") == ["IDEA-0001"]
    assert [e.id.text for e in graph.sources(idea.id, "refined_from")] == [work.id.text]


# ---------------------------------------------------------------------------
# Through the CLI
# ---------------------------------------------------------------------------


def test_move_reports_where_it_went(run, project):
    run("capture", "drafts are lost on refresh")
    code, output, _ = run("move", "IDEA-0001", "file")
    assert code == 0
    assert "section → file" in output
    assert "ideas/IDEA-0001" in output


def test_moving_a_type_with_one_shape_is_refused(run, project):
    run("new", "requirement", "--title", "Reachable over TLS")
    code, _, err = run("move", "REQ-0001", "file")
    assert code == EXIT_BAD_INPUT
    assert "nothing to move it to" in err
    assert "layout to dynamic" in err


def test_moving_where_it_already_is_is_refused(run, project):
    run("capture", "a thought")
    code, _, err = run("move", "IDEA-0001", "section")
    assert code == EXIT_BAD_INPUT
    assert "already stored as a section" in err


def test_moving_artifacts_somewhere_they_do_not_fit_is_refused(run, project):
    run("capture", "a thought")
    run("move", "IDEA-0001", "directory")
    entity = reload(project, "IDEA-0001")
    (entity.home / "screenshot.txt").write_text("evidence\n", encoding="utf-8")

    code, _, err = run("move", "IDEA-0001", "section")
    assert code == EXIT_BAD_INPUT
    assert "screenshot.txt" in err
    # Not "move it to a directory" — it is already in one. The only thing that
    # can actually be done is to deal with the files.
    assert "remove those artifacts" in err


def test_an_unknown_layout_is_refused_by_the_parser(run, project):
    run("capture", "a thought")
    code, _, err = run("move", "IDEA-0001", "folder")
    assert code == EXIT_BAD_INPUT
    assert "folder" in err


# ---------------------------------------------------------------------------
# Wanting an artifact is how an entry earns a directory
# ---------------------------------------------------------------------------


@pytest.fixture
def journalled(make_project):
    """A dynamic type that keeps a journal — the shipped `idea` does not."""
    return make_project({"entity_types": {"idea": {
        "artifacts": ["journal.md"], "journal_artifact": "journal.md"}}})


def test_logging_promotes_an_entry_rather_than_refusing(journalled, capsys):
    entity = capture(journalled, "drafts are lost on refresh")
    assert entity.layout == "section"

    assert main(["-C", str(journalled.root), "log", "IDEA-0001", "still happening"]) == 0

    promoted = reload(journalled, "IDEA-0001")
    assert promoted.layout == "directory"
    assert "still happening" in promoted.read_artifact("journal.md")
    assert "IDEA-0001" not in journalled.section_path("idea").read_text(encoding="utf-8")


def test_a_gate_on_a_missing_artifact_names_the_move(make_project):
    """The refusal has to end in something runnable. For a dynamic entry with
    nowhere to write yet, that is the move."""
    from szsdlc.workflow import unmet_gates

    project = make_project({"entity_types": {"idea": {
        "artifacts": ["findings.md"],
        "workflow": {"states": {"refined": {"requires_artifact": ["findings.md"]}}},
    }}})
    entity = capture(project)
    entity.set_status("refining")
    entity.save()

    gates = unmet_gates(reload(project, "IDEA-0001"), "refined")
    assert gates
    assert "szsdlc move IDEA-0001 directory" in gates[0].remedy


# ---------------------------------------------------------------------------
# A half-finished move
# ---------------------------------------------------------------------------


def test_one_id_in_two_shapes_is_reported_with_both_places(project):
    """The move writes the new shape before removing the old, deliberately:
    an interruption claims the id twice, which is reportable, rather than
    losing it, which is not."""
    capture(project, "drafts are lost on refresh")
    entity = reload(project, "IDEA-0001")
    stray = project.dir_for("idea") / "IDEA-0001-drafts-are-lost-on-refresh.md"
    stray.write_bytes(entity.render().encode("utf-8"))

    store = load_all(project)
    findings = validate_run(project, store, Graph(project, store, IdSpace(project)))
    duplicates = [f for f in findings if f.kind == "duplicate-id"]
    assert len(duplicates) == 1
    assert "index.md" in duplicates[0].message
    assert stray.name in duplicates[0].message


def test_an_attached_file_is_not_reported_as_an_orphan(project):
    """A dynamic type that declares no artifacts has said the opposite of a
    fixed artifact set: whatever turned up is why the directory exists."""
    capture(project, "drafts are lost on refresh")
    home = move(project, "IDEA-0001", "directory").home
    (home / "screenshot.txt").write_text("evidence\n", encoding="utf-8")

    store = load_all(project)
    findings = validate_run(project, store, Graph(project, store, IdSpace(project)))
    assert [f for f in findings if f.kind == "orphan-file"] == []


def test_a_declared_artifact_set_is_still_enforced(make_project):
    project = make_project({"entity_types": {"idea": {"artifacts": ["findings.md"]}}})
    capture(project, "drafts are lost on refresh")
    home = move(project, "IDEA-0001", "directory").home
    (home / "stray.txt").write_text("x\n", encoding="utf-8")

    store = load_all(project)
    findings = validate_run(project, store, Graph(project, store, IdSpace(project)))
    assert [f.kind for f in findings if f.kind == "orphan-file"] == ["orphan-file"]
