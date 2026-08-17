"""Task 5 — the relation graph and derived attributes.

The fixture spans all six default types on purpose: the properties under test
are about the *shape* of the model, and a fixture with only work items would
not exercise the definitional/delivery boundary that the whole design turns on.

Two claims are load-bearing here. Inverses are generated, never authored. And
coverage, delivery and roll-up are computed from edges at read time and never
written back — asserted by hashing every source file before and after.
"""

from __future__ import annotations

import hashlib

import pytest

from szsdlc import graph as G
from szsdlc.ids import IdSpace, Tombstones
from szsdlc.model import load_all


# ---------------------------------------------------------------------------
# A project spanning every default type
# ---------------------------------------------------------------------------


@pytest.fixture
def world(project, write_entity):
    """IDEA-0001 spawned an epic, two work items, a spike and a requirement.

    WI-0001 is done and implements REQ-0001; WI-0002 is executing and
    implements REQ-0001 too. REQ-0002 is approved and nothing implements it.
    """
    write_entity("idea", 1, "status: refined\ncaptured: 2026-08-01\n",
                 "valkey needs a sentinel quorum story\n", slug="valkey-sentinel")

    write_entity("epic", 1, "title: Sentinel quorum\nstatus: active\nopened: 2026-08-02\n"
                            "relations:\n  refined_from: IDEA-0001\n", slug="sentinel")

    write_entity("requirement", 1,
                 "title: Quorum survives a partition\nstatus: approved\nopened: 2026-08-02\n"
                 "tags: [valkey]\nrelations:\n  refined_from: IDEA-0001\n", slug="quorum")
    write_entity("requirement", 2,
                 "title: Nobody has built this\nstatus: approved\nopened: 2026-08-02\n",
                 slug="uncovered")

    write_entity("spike", 1, "title: Quorum under partition\nstatus: answered\n"
                             "opened: 2026-08-03\nrelations:\n  parent: EPIC-0001\n"
                             "  refined_from: IDEA-0001\n",
                 slug="partition", artifacts={"findings.md": "It works.\n"})

    write_entity("work_item", 1,
                 "title: Add sentinel config\nstatus: done\nopened: 2026-08-04\n"
                 "tags: [valkey, tls]\nrelations:\n  parent: EPIC-0001\n"
                 "  implements: [REQ-0001]\n  refined_from: IDEA-0001\n",
                 slug="add-config",
                 artifacts={"plan.md": "- [x] one\n- [x] two\n"})
    write_entity("work_item", 2,
                 "title: Document the quorum\nstatus: executing\nopened: 2026-08-05\n"
                 "tags: [valkey]\nrelations:\n  parent: EPIC-0001\n"
                 "  implements: [REQ-0001]\n  depends_on: [WI-0001]\n",
                 slug="document",
                 artifacts={"plan.md": "- [x] one\n- [ ] two\n- [ ] three\n"})

    write_entity("decision", 1, "title: Use sentinel not cluster\nstatus: accepted\n"
                                "opened: 2026-08-02\n", slug="sentinel-not-cluster")
    return project


@pytest.fixture
def world_graph(world):
    ids = IdSpace(world)
    return G.Graph(world, load_all(world, ids), ids)


def ref(graph, text):
    return graph.ids.parse(text)


def fingerprint(root):
    """Content hash of every markdown file, for the never-written-back proof."""
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*.md"))
    }


# ---------------------------------------------------------------------------
# Edges
# ---------------------------------------------------------------------------


def test_authored_edges_load_from_every_type(world_graph):
    authored = [e for e in world_graph if not e.generated]
    kinds = {e.kind for e in authored}
    assert kinds == {"parent", "implements", "depends_on", "refined_from"}
    assert len(world_graph.targets(ref(world_graph, "WI-0001"), "implements")) == 1


def test_inverses_are_generated_and_never_stored(world, world_graph):
    """The back-link is rendered, never authored."""
    req = ref(world_graph, "REQ-0001")
    implemented_by = world_graph.outgoing(req, "implemented_by")
    assert {e.target.text for e in implemented_by} == {"WI-0001", "WI-0002"}
    assert all(e.generated for e in implemented_by)

    # And nothing of the sort exists on disk.
    for path in world.root.rglob("*.md"):
        assert "implemented_by" not in path.read_text(encoding="utf-8")
        assert "children" not in path.read_text(encoding="utf-8")


def test_children_and_sources_are_id_ordered(world_graph):
    children = world_graph.children(ref(world_graph, "EPIC-0001"))
    assert [c.id.text for c in children] == ["SPK-0001", "WI-0001", "WI-0002"]


def test_refined_into_comes_from_the_generated_inverse(world_graph):
    """One idea yielding several entities is the normal case."""
    idea = ref(world_graph, "IDEA-0001")
    spawned = {e.target.text for e in world_graph.outgoing(idea, "refined_into")}
    assert spawned == {"EPIC-0001", "REQ-0001", "SPK-0001", "WI-0001"}


# ---------------------------------------------------------------------------
# Invalidity is representable
# ---------------------------------------------------------------------------


def test_a_dangling_reference_is_kept_and_reported(project, write_entity):
    write_entity("work_item", 1, "title: T\nstatus: idea\nopened: 2026-08-16\n"
                                 "relations:\n  implements: [REQ-0420]\n")
    graph = G.build(project)

    dangling = graph.dangling()
    assert len(dangling) == 1
    # The edge survives, so it can be reported...
    assert dangling[0].target.text == "REQ-0420"
    # ...and renders visibly broken rather than vanishing.
    assert dangling[0].target.label == "REQ-0420 (missing)"
    assert not dangling[0].target.resolved

    finding = next(f for f in graph.structural_findings() if f.kind == "dangling")
    assert finding.fix == "szsdlc unlink WI-0001 implements REQ-0420"


def test_an_unparseable_target_is_representable_too(project, write_entity):
    write_entity("work_item", 1, "title: T\nstatus: idea\nopened: 2026-08-16\n"
                                 "relations:\n  implements: ['not an id']\n")
    graph = G.build(project)
    target = graph.dangling()[0].target
    assert target.entity_id is None
    assert target.label == "not an id (unresolvable)"


def test_a_broken_entity_does_not_stop_the_graph(project, write_entity):
    write_entity("work_item", 1, "title: A\nstatus: idea\nopened: 2026-08-16\n", slug="a")
    write_entity("work_item", 2, raw="---\nbroken: [\n---\n", slug="b")
    write_entity("work_item", 3, "title: C\nstatus: idea\nopened: 2026-08-16\n"
                                 "relations:\n  depends_on: [WI-0001]\n", slug="c")
    graph = G.build(project)
    assert len(graph.store) == 2
    assert len(graph.store.unparseable) == 1
    assert graph.targets(ref(graph, "WI-0003"), "depends_on")[0].resolved


def test_a_tombstoned_target_resolves_and_is_flagged_for_rewriting(project, write_entity):
    write_entity("spike", 3, "title: Moved here\nstatus: open\nopened: 2026-08-16\n")
    write_entity("work_item", 1, "title: T\nstatus: idea\nopened: 2026-08-16\n"
                                 "relations:\n  depends_on: [WI-0042]\n")
    tombstones = Tombstones({"WI-0042": "SPK-0003"}, project.root / ".szsdlc" / "tombstones.yml")
    ids = IdSpace(project, tombstones)
    graph = G.Graph(project, load_all(project, ids), ids)

    assert graph.dangling() == []
    redirected = graph.redirected()
    assert redirected[0].target.entity_id.text == "SPK-0003"
    assert redirected[0].target.redirected_from == "WI-0042"


# ---------------------------------------------------------------------------
# Structural findings
# ---------------------------------------------------------------------------


def test_a_clean_world_has_no_structural_findings(world_graph):
    assert world_graph.structural_findings() == []


def test_cardinality_violation_is_reported_with_a_runnable_fix(project, write_entity):
    write_entity("epic", 1, "title: A\nstatus: open\nopened: 2026-08-16\n", slug="a")
    write_entity("epic", 2, "title: B\nstatus: open\nopened: 2026-08-16\n", slug="b")
    write_entity("work_item", 1, "title: T\nstatus: idea\nopened: 2026-08-16\n"
                                 "relations:\n  parent: [EPIC-0001, EPIC-0002]\n")
    finding = next(f for f in G.build(project).structural_findings()
                   if f.kind == "cardinality")
    assert "single-valued" in finding.message
    assert finding.fix == "szsdlc unlink WI-0001 parent EPIC-0002"


def test_a_parent_that_does_not_declare_can_parent_is_reported(project, write_entity):
    write_entity("work_item", 1, "title: A\nstatus: idea\nopened: 2026-08-16\n", slug="a")
    write_entity("work_item", 2, "title: B\nstatus: idea\nopened: 2026-08-16\n"
                                 "relations:\n  parent: WI-0001\n", slug="b")
    finding = next(f for f in G.build(project).structural_findings()
                   if f.kind == "relation-type")
    assert "allowed: epic" in finding.message


def test_a_work_item_with_no_parent_is_reported(project, write_entity):
    write_entity("work_item", 1, "title: T\nstatus: idea\nopened: 2026-08-16\n")
    finding = next(f for f in G.build(project).structural_findings()
                   if f.kind == "missing-relation")
    assert finding.fix == "szsdlc link WI-0001 parent <ref>"


def test_a_terminal_work_item_is_not_nagged_about_a_missing_parent(project, write_entity):
    write_entity("work_item", 1, "title: T\nstatus: dropped\nopened: 2026-08-16\n")
    assert not [f for f in G.build(project).structural_findings()
                if f.kind == "missing-relation"]


def test_cycles_are_detected_per_relation_and_name_it(project, write_entity):
    write_entity("epic", 1, "title: A\nstatus: open\nopened: 2026-08-16\n"
                            "relations:\n  parent: EPIC-0002\n", slug="a")
    write_entity("epic", 2, "title: B\nstatus: open\nopened: 2026-08-16\n"
                            "relations:\n  parent: EPIC-0001\n", slug="b")
    graph = G.build(project)

    cycles = graph.cycles()
    assert len(cycles) == 1
    relation_name, path = cycles[0]
    assert relation_name == "parent"
    assert {i.text for i in path} == {"EPIC-0001", "EPIC-0002"}

    finding = next(f for f in graph.structural_findings() if f.kind == "cycle")
    assert finding.fix.startswith("szsdlc unlink ")
    assert "<relation>" not in finding.fix


def test_a_self_cycle_is_detected(project, write_entity):
    write_entity("epic", 1, "title: A\nstatus: open\nopened: 2026-08-16\n"
                            "relations:\n  parent: EPIC-0001\n")
    assert len(G.build(project).cycles("parent")) == 1


def test_a_diamond_is_not_a_cycle(project, write_entity):
    write_entity("epic", 1, "title: Top\nstatus: open\nopened: 2026-08-16\n", slug="top")
    for number, slug in ((2, "left"), (3, "right")):
        write_entity("epic", number, f"title: {slug}\nstatus: open\nopened: 2026-08-16\n"
                                     "relations:\n  parent: EPIC-0001\n", slug=slug)
    write_entity("work_item", 1, "title: T\nstatus: idea\nopened: 2026-08-16\n"
                                 "relations:\n  parent: EPIC-0002\n"
                                 "  depends_on: [EPIC-0003]\n")
    assert G.build(project).cycles() == []


# ---------------------------------------------------------------------------
# Derived attributes
# ---------------------------------------------------------------------------


def test_requirement_coverage_and_delivery_are_derived(world_graph):
    store = world_graph.store
    covered = store.by_text("REQ-0001")
    uncovered = store.by_text("REQ-0002")

    assert world_graph.derived(covered) == {"covered": True, "delivered": False}
    # Approved and unimplemented: emphatically not delivered, and impossible to
    # lose track of because it is a graph query rather than a stored flag.
    assert world_graph.derived(uncovered) == {"covered": False, "delivered": False}


def test_delivery_flips_when_the_last_implementer_finishes(world, world_graph):
    work_item = world_graph.store.by_text("WI-0002")
    work_item.set_status("done")
    work_item.save()

    regraph = G.build(world)
    assert regraph.derived(regraph.store.by_text("REQ-0001"))["delivered"] is True


def test_a_requirement_file_never_changes_when_work_against_it_does(world, world_graph):
    """The definitional/delivery boundary, asserted at the byte level."""
    requirements = world.dir_for("requirement")
    before = fingerprint(requirements)

    work_item = world_graph.store.by_text("WI-0002")
    work_item.set_status("done")
    work_item.save()
    regraph = G.build(world)
    assert regraph.derived(regraph.store.by_text("REQ-0001"))["delivered"] is True

    assert fingerprint(requirements) == before


def test_epic_progress_is_measured_in_children(world_graph):
    epic = world_graph.store.by_text("EPIC-0001")
    aggregate = world_graph.derived(epic)["progress"]
    # SPK-0001 answered and WI-0001 done are terminal; WI-0002 is executing.
    assert (aggregate.children, aggregate.terminal) == (3, 2)
    assert aggregate.percent == 67
    assert aggregate.complete is False
    # Checkbox totals travel alongside as detail, without deciding the number.
    assert str(aggregate.tasks) == "3/5"


def test_an_epic_with_no_children_is_zero_not_complete(project, write_entity):
    write_entity("epic", 1, "title: Empty\nstatus: open\nopened: 2026-08-16\n")
    graph = G.build(project)
    aggregate = graph.derived(graph.store.by_text("EPIC-0001"))["progress"]
    assert aggregate.percent == 0 and aggregate.complete is False


def test_derivation_never_writes_anything(world):
    """Sync twice, diff every source file: zero change."""
    before = fingerprint(world.root)
    for _ in range(2):
        graph = G.build(world)
        for entity in graph.store:
            graph.derived(entity)
        graph.structural_findings()
        graph.cycles()
    assert fingerprint(world.root) == before


def test_asking_for_an_undeclared_derived_attribute_is_an_internal_error(world_graph):
    """A model invariant, not a project finding — the two must never be conflated."""
    from szsdlc.errors import InternalError

    with pytest.raises(InternalError):
        world_graph.derive(world_graph.store.by_text("WI-0001"), "covered")


# ---------------------------------------------------------------------------
# Trace
# ---------------------------------------------------------------------------


def test_trace_walks_both_directions_back_to_the_idea(world_graph):
    edges = world_graph.trace(ref(world_graph, "REQ-0001"), depth=2)
    reached = {e.source.text for e in edges} | {e.target.text for e in edges}
    assert "WI-0001" in reached and "WI-0002" in reached
    assert "IDEA-0001" in reached


def test_trace_reports_each_relationship_once(world_graph):
    edges = world_graph.trace(ref(world_graph, "EPIC-0001"), depth=2)
    assert all(not e.generated for e in edges)
    keys = [(e.source.text, e.kind, e.target.text) for e in edges]
    assert len(keys) == len(set(keys))


def test_trace_depth_is_honoured(world_graph):
    shallow = world_graph.trace(ref(world_graph, "REQ-0001"), depth=1)
    deep = world_graph.trace(ref(world_graph, "REQ-0001"), depth=3)
    assert len(shallow) < len(deep)
    assert world_graph.trace(ref(world_graph, "REQ-0001"), depth=0) == []
