"""szsdlc trace: relations both ways, back to the idea."""

from __future__ import annotations

from szsdlc.cli import main

from .conftest import entities


# ---------------------------------------------------------------------------
# trace
# ---------------------------------------------------------------------------
def test_trace_walks_back_to_the_originating_idea(run, project, write_entity, capsys):
    main(["-C", str(project.root), "capture", "valkey needs a quorum story"])
    main(["-C", str(project.root), "refine", "IDEA-0001", "--into", "epic",
          "--title", "Quorum"])
    main(["-C", str(project.root), "refine", "IDEA-0001", "--into", "work_item",
          "--title", "Do it"])
    main(["-C", str(project.root), "link", "WI-0001", "parent", "EPIC-0001"])
    capsys.readouterr()

    code, output, _ = run("trace", "WI-0001")
    assert code == 0
    assert "WI-0001 refined_from IDEA-0001" in output
    assert "EPIC-0001 refined_from IDEA-0001" in output


def test_trace_defaults_to_depth_two(run, world):
    _, shallow, _ = run("trace", "REQ-0001", "--depth", "1")
    _, default, _ = run("trace", "REQ-0001")
    assert len(default) >= len(shallow)


def test_trace_renders_a_broken_edge_visibly(run, world, project):
    entity = entities(project).by_text("WI-0002")
    entity.set_relation("implements", ["REQ-0420"])
    entity.save()
    _, output, _ = run("trace", "WI-0002")
    assert "REQ-0420 (missing)" in output


def test_trace_is_bounded(run, bulk):
    bulk()
    _, output, _ = run("trace", "EPIC-0001", "--depth", "1")
    lines = output.strip().splitlines()
    assert len(lines) == 21
    assert lines[-1].startswith("showing 20 of ")
