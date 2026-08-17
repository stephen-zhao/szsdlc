"""Task 9 — the token-saving surface.

The design rule: **counters in context, details on demand.** Every condition
worth knowing about is a scalar in `szsdlc context`; the corresponding listing
is only spent when the scalar is non-zero and the detail is wanted.

The fixture that matters most is the 200-entity one where *everything is
unscheduled* — the migration case, and the one where an uncapped listing would
hurt most.
"""

from __future__ import annotations

import json

import pytest

from szsdlc.cli import main
from szsdlc.errors import EXIT_BAD_INPUT
from szsdlc.ids import IdSpace
from szsdlc.model import create_entity, load_all
from szsdlc.roadmap import Roadmap


@pytest.fixture
def run(project, capsys):
    def invoke(*argv: str):
        code = main(["-C", str(project.root), *argv])
        captured = capsys.readouterr()
        return code, captured.out, captured.err
    return invoke


def entities(project):
    return load_all(project, IdSpace(project))


@pytest.fixture
def world(run, project, write_entity):
    """A small but complete project: an epic, two work items, a spike, two reqs."""
    write_entity("idea", 1, "status: inbox\ncaptured: 2026-01-01\n", "an old thought\n")
    write_entity("epic", 1, "title: Sentinel quorum\nstatus: active\nopened: 2026-08-01\n")
    write_entity("requirement", 1, "title: Quorum survives\nstatus: approved\n"
                                   "opened: 2026-08-01\n", slug="quorum")
    write_entity("requirement", 2, "title: Nobody built this\nstatus: approved\n"
                                   "opened: 2026-08-01\n", slug="uncovered")
    write_entity("spike", 1, "title: Investigate\nstatus: researching\nopened: 2026-08-01\n"
                             "relations:\n  parent: EPIC-0001\n")
    write_entity("work_item", 1, "title: Add config\nstatus: executing\nopened: 2026-08-01\n"
                                 "tags: [valkey, tls]\nrelations:\n  parent: EPIC-0001\n"
                                 "  implements: [REQ-0001]\n",
                 slug="add-config",
                 artifacts={"plan.md": "- [x] one\n- [ ] two\n- [ ] three\n",
                            "design.md": "# Design\n\nUse a three-node quorum.\n",
                            "journal.md": "- 2026-08-10 started\n- 2026-08-11 continued\n"})
    write_entity("work_item", 2, "title: Document it\nstatus: ready\nopened: 2026-08-02\n"
                                 "tags: [valkey]\nrelations:\n  parent: EPIC-0001\n"
                                 "  depends_on: [WI-0001]\n", slug="document")
    return project


@pytest.fixture
def bulk(project):
    """200 entities, every one of them unscheduled — the migration case."""

    def build(count: int = 200):
        ids = IdSpace(project)
        epic = create_entity(project, ids, project.type_for("epic"), title="Everything")
        for number in range(count):
            create_entity(project, ids, project.type_for("work_item"),
                          title=f"Work item number {number}", status="ready",
                          tags=["bulk"], relations={"parent": epic.id.text})
        return project

    return build


# ---------------------------------------------------------------------------
# next
# ---------------------------------------------------------------------------


def test_next_draws_only_from_actionable_types(run, world):
    _, output, _ = run("next")
    listed = {line.split()[0] for line in output.strip().splitlines()}
    # Epics, requirements and ideas are not work.
    assert listed <= {"SPK-0001", "WI-0001", "WI-0002"}
    assert "EPIC-0001" not in listed and "REQ-0001" not in listed


def test_next_hides_blocked_work(run, world):
    """WI-0002 depends on WI-0001, which is still executing."""
    _, output, _ = run("next")
    assert "WI-0002" not in output
    assert "WI-0001" in output


def test_next_shows_work_once_its_dependency_is_terminal(run, world, project):
    entity = entities(project).by_text("WI-0001")
    entity.set_status("dropped")
    entity.save()
    _, output, _ = run("next")
    assert "WI-0002" in output


def test_next_follows_roadmap_order_not_a_per_entity_field(run, world, project):
    roadmap = Roadmap.load(project, "roadmap")
    roadmap.place("WI-0001", "later")
    roadmap.place("SPK-0001", "now")
    roadmap.save()

    _, output, _ = run("next")
    listed = [line.split()[0] for line in output.strip().splitlines()]
    assert listed.index("SPK-0001") < listed.index("WI-0001")


def test_next_can_be_filtered_by_horizon_and_parent(run, world, project):
    roadmap = Roadmap.load(project, "roadmap")
    roadmap.place("SPK-0001", "now")
    roadmap.place("WI-0001", "later")
    roadmap.save()

    _, output, _ = run("next", "--horizon", "now")
    assert output.strip().split()[0] == "SPK-0001"
    assert "WI-0001" not in output

    _, output, _ = run("next", "--parent", "EPIC-0001")
    assert "SPK-0001" in output and "WI-0001" in output


def test_next_stays_under_the_cap_at_two_hundred_entities(run, bulk):
    bulk()
    _, output, _ = run("next")
    lines = output.strip().splitlines()
    assert len(lines) == 11
    assert lines[-1] == "showing 10 of 200 — rerun with --limit 0"
    assert len(output) < 900


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------


def test_show_prints_the_file(run, world):
    code, output, _ = run("show", "WI-0001")
    assert code == 0
    assert output.startswith("---\n")
    assert "title: Add config" in output


def test_show_accepts_a_sloppy_reference(run, world):
    assert run("show", "wi-1")[0] == 0


def test_show_context_is_a_budgeted_bundle_not_the_files(run, world):
    code, output, _ = run("show", "WI-0001", "--context")
    assert code == 0
    assert "WI-0001  executing  Add config" in output
    assert "progress: 1/3 (33%)" in output
    assert "next task: two" in output
    assert "parent: EPIC-0001" in output
    assert "implements: REQ-0001" in output
    # A design summary, not the design.
    assert "design.md: Use a three-node quorum." in output
    assert "# Design" not in output
    # The journal tail, not the journal.
    assert "2026-08-11 continued" in output
    assert len(output) <= 2400


def test_show_context_includes_generated_inverses(run, world):
    _, output, _ = run("show", "EPIC-0001", "--context")
    assert "children: SPK-0001, WI-0001, WI-0002" in output


def test_show_json_carries_the_derived_attributes(run, world):
    _, output, _ = run("show", "REQ-0001", "--json")
    payload = json.loads(output)
    assert payload["derived"] == {"covered": "True", "delivered": "False"}
    assert payload["relations"] == {}


# ---------------------------------------------------------------------------
# context
# ---------------------------------------------------------------------------


def test_context_reports_in_flight_work_and_the_current_task(run, world):
    code, output, _ = run("context")
    assert code == 0
    assert "in flight:" in output
    assert "WI-0001" in output and "executing" in output and "Add config" in output
    assert "current task (WI-0001): two" in output
    # WI-0002 is `ready` — the front of the workflow, so backlog, not in flight.
    assert "WI-0002" not in output


def test_in_flight_ids_share_a_column(run, world):
    """`SPK-0001` and `WI-0001` differ in width; the status column must not.

    Ragged columns are cheap to produce and expensive to read, and this block
    is prepended to a model's context at the start of every single session.
    """
    _, output, _ = run("context")
    rows = [line for line in output.splitlines() if line.startswith("  ")]
    assert len(rows) >= 2
    starts = {line.index(line.strip().split()[1]) for line in rows}
    assert len(starts) == 1, rows


def test_context_ends_in_a_counters_line_of_scalars(run, world):
    _, output, _ = run("context")
    counters = output.strip().splitlines()[-1]
    assert "inbox 1" in counters
    assert "uncovered reqs 1" in counters
    assert "unscheduled 4" in counters
    assert "entities 7" in counters


def test_context_replaces_a_two_hundred_line_listing_with_one_number(run, bulk):
    """The migration case: everything unscheduled, and it costs one scalar."""
    bulk()
    _, context_output, _ = run("context")
    _, listing_output, _ = run("list", "--unscheduled", "--limit", "0")

    assert "unscheduled 201" in context_output
    assert len(context_output) <= 1800
    # The listing it replaces is an order of magnitude larger.
    assert len(listing_output) > 10 * len(context_output)


def test_context_is_capped_regardless_of_project_size(run, bulk, project):
    bulk()
    for number in range(1, 30):
        entity = entities(project).by_text(f"WI-{number:04d}")
        for status in ("designing",):
            entity.set_status(status)
        entity.save()
    _, output, _ = run("context")
    assert len(output) <= 1800
    assert "more — szsdlc next" in output


def test_context_json_exposes_the_same_counters(run, world):
    _, output, _ = run("context", "--json")
    payload = json.loads(output)
    assert payload["counters"]["inbox"] == 1
    assert payload["current_entity"] == "WI-0001"


def test_an_empty_project_still_reports_its_counters(run, project):
    code, output, _ = run("context")
    assert code == 0
    assert "entities 0" in output
    assert "in flight" not in output


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


def test_list_filters_compose(run, world):
    _, output, _ = run("list", "--type", "work_item", "--tag", "valkey")
    assert {line.split()[0] for line in output.strip().splitlines()} == \
        {"WI-0001", "WI-0002"}

    _, output, _ = run("list", "--type", "work_item", "--tag", "tls")
    assert output.strip().split()[0] == "WI-0001"

    _, output, _ = run("list", "--status", "approved")
    assert len(output.strip().splitlines()) == 2


def test_list_by_parent(run, world):
    _, output, _ = run("list", "--parent", "EPIC-0001")
    assert len(output.strip().splitlines()) == 3


def test_uncovered_is_a_graph_query(run, world):
    """An approved requirement nobody has implemented is impossible to lose."""
    _, output, _ = run("list", "--uncovered")
    assert output.strip().split()[0] == "REQ-0002"


def test_unscheduled_excludes_terminal_and_non_schedulable(run, world):
    _, output, _ = run("list", "--unscheduled")
    listed = {line.split()[0] for line in output.strip().splitlines()}
    assert "REQ-0001" not in listed and "IDEA-0001" not in listed
    assert "EPIC-0001" in listed and "WI-0001" in listed


def test_a_tag_filter_normalizes_its_input(run, world):
    _, output, _ = run("list", "--tag", "  VALKEY  ")
    assert len(output.strip().splitlines()) == 2


def test_list_is_bounded_and_prints_the_total(run, bulk):
    bulk()
    _, output, _ = run("list")
    lines = output.strip().splitlines()
    assert len(lines) == 21
    assert lines[-1] == "showing 20 of 201 — rerun with --limit 0"


def test_limit_zero_is_the_documented_escape(run, bulk):
    bulk()
    _, output, _ = run("list", "--limit", "0")
    assert len(output.strip().splitlines()) == 201


def test_json_is_exempt_from_the_limit(run, bulk):
    bulk()
    _, output, _ = run("list", "--json")
    assert len(json.loads(output)) == 201


def test_an_unknown_type_filter_lists_the_known_ones(run, world):
    code, _, err = run("list", "--type", "ticket")
    assert code == 5
    assert "work_item" in err


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


# ---------------------------------------------------------------------------
# standards
# ---------------------------------------------------------------------------


@pytest.fixture
def standards(project):
    directory = project.standards_dir
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "templates.md").write_text(
        '---\napplies_to: ["templates/**"]\n---\n'
        "Never hardcode a version; reference the pinned digest.\n", encoding="utf-8")
    (directory / "docs.md").write_text(
        '---\napplies_to: ["docs/*.md"]\n---\nOne sentence per line.\n',
        encoding="utf-8")
    (directory / "everything.md").write_text(
        '---\napplies_to: ["**/*.py"]\n---\nType hints on public functions.\n',
        encoding="utf-8")
    return project


def test_standards_match_returns_only_what_applies(run, standards):
    code, output, _ = run("standards", "match", "templates/base.yaml")
    assert code == 0
    assert output.strip() == "templates"


def test_a_single_star_does_not_cross_a_separator(run, standards):
    """The distinction is the whole reason to write a glob rather than a substring."""
    _, shallow, _ = run("standards", "match", "docs/plan.md")
    _, deep, _ = run("standards", "match", "docs/nested/plan.md")
    assert shallow.strip() == "docs"
    assert deep.strip() == ""


def test_a_double_star_does_cross(run, standards):
    _, output, _ = run("standards", "match", "src/szsdlc/cli.py")
    assert output.strip() == "everything"


def test_matching_nothing_emits_nothing(run, standards):
    code, output, _ = run("standards", "match", "README.md")
    assert code == 0 and output == ""


def test_several_paths_are_matched_at_once(run, standards):
    _, output, _ = run("standards", "match", "templates/a.yaml", "docs/b.md")
    assert set(output.split()) == {"templates", "docs"}


def test_standards_match_json_carries_the_body(run, standards):
    _, output, _ = run("standards", "match", "templates/a.yaml", "--json")
    payload = json.loads(output)
    assert payload[0]["body"].startswith("Never hardcode a version")


def test_standards_match_without_paths_is_refused(run, standards):
    code, _, err = run("standards", "match")
    assert code == EXIT_BAD_INPUT
    assert "Fix: szsdlc standards match <path>" in err


def test_a_project_with_no_standards_is_silent(run, project):
    code, output, _ = run("standards", "match", "anything.md")
    assert code == 0 and output == ""
