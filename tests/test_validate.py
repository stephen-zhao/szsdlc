"""Task 11 — validation rules.

`sync` renders an invalid project happily; this is what objects. Every finding
here is a statement about the *project*. A broken model invariant is a szsdlc
bug and travels on the internal-error channel instead — the two must never be
conflated in output, and one test holds that line.

The fixture project exercises every rule twice: once passing, once failing.
"""

from __future__ import annotations

import json

import pytest

from szsdlc import validate as V
from szsdlc.cli import main
from szsdlc.errors import EXIT_INVALID
from szsdlc.graph import Graph
from szsdlc.ids import IdSpace, Tombstones
from szsdlc.model import load_all
from szsdlc.roadmap import Roadmap


@pytest.fixture
def run(project, capsys):
    def invoke(*argv: str):
        code = main(["-C", str(project.root), *argv])
        captured = capsys.readouterr()
        return code, captured.out, captured.err
    return invoke


def findings(config, kind: str | None = None):
    ids = IdSpace(config)
    store = load_all(config, ids)
    found = V.run(config, store, Graph(config, store, ids))
    return [f for f in found if kind is None or f.kind == kind]


@pytest.fixture
def clean(project, write_entity, run):
    """A project that validates green — the baseline every rule is measured from."""
    write_entity("epic", 1, "title: Sentinel quorum\nstatus: active\nopened: 2026-08-01\n")
    write_entity("requirement", 1, "title: Quorum survives\nstatus: approved\n"
                                   "opened: 2026-08-01\ntags: [valkey]\n")
    write_entity("work_item", 1, "title: Add config\nstatus: ready\nopened: 2026-08-01\n"
                                 "tags: [valkey]\nrelations:\n  parent: EPIC-0001\n"
                                 "  implements: [REQ-0001]\n",
                 artifacts={"plan.md": "- [ ] one\n"})
    roadmap = Roadmap.load(project, "roadmap")
    roadmap.place("WI-0001", "now")
    roadmap.place("EPIC-0001", "next")
    roadmap.save()
    run("sync")
    return project


def test_a_clean_project_reports_nothing(run, clean):
    """C4 — this runs at every turn end; output on the happy path has no consumer."""
    code, output, err = run("validate")
    assert (code, output, err) == (0, "", "")


def test_verbose_opts_in(run, clean):
    _, output, _ = run("validate", "--verbose")
    assert output.strip() == "clean"


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def test_an_unparseable_file_is_reported_with_its_path(clean, write_entity):
    write_entity("work_item", 9, raw="---\ntitle: [unclosed\n---\n", slug="broken")
    found = findings(clean, "unparseable")
    assert len(found) == 1
    assert "entity.md" in found[0].message
    assert found[0].is_error


def test_a_duplicate_id_names_both_paths(clean, write_entity):
    write_entity("work_item", 1, "title: Other\nstatus: idea\nopened: 2026-08-01\n",
                 slug="second")
    found = findings(clean, "duplicate-id")
    assert len(found) == 1
    assert "WI-0001-thing" in found[0].message and "WI-0001-second" in found[0].message


def test_a_frontmatter_violation_is_reported(clean, write_entity):
    write_entity("work_item", 9, "title: T\nstatus: idea\npriority: high\n"
                                 "opened: 2026-08-01\nrelations:\n  parent: EPIC-0001\n",
                 slug="extra")
    assert any("priority" in f.message for f in findings(clean, "frontmatter"))


def test_a_status_outside_the_workflow_is_reported(clean, write_entity):
    write_entity("work_item", 9, "title: T\nstatus: marinating\nopened: 2026-08-01\n"
                                 "relations:\n  parent: EPIC-0001\n", slug="odd")
    found = findings(clean, "unknown-status")
    assert found and "marinating" in found[0].message


def test_a_name_that_does_not_open_with_its_id_is_a_warning(clean, write_entity):
    write_entity("work_item", 9, "title: T\nstatus: idea\nopened: 2026-08-01\n"
                                 "relations:\n  parent: EPIC-0001\n", slug="ok")
    directory = clean.dir_for("work_item")
    (directory / "WI-0009-ok").rename(directory / "WI-9-ok")
    found = findings(clean, "name-mismatch")
    assert found and not found[0].is_error


# ---------------------------------------------------------------------------
# Relations
# ---------------------------------------------------------------------------


def test_a_dangling_reference_is_an_error(clean, write_entity):
    write_entity("work_item", 9, "title: T\nstatus: idea\nopened: 2026-08-01\n"
                                 "relations:\n  parent: EPIC-0001\n"
                                 "  implements: [REQ-0420]\n", slug="dangles")
    found = findings(clean, "dangling")
    assert found and found[0].fix == "szsdlc unlink WI-0009 implements REQ-0420"


def test_a_parent_cycle_is_an_error(clean, write_entity):
    write_entity("epic", 8, "title: A\nstatus: open\nopened: 2026-08-01\n"
                            "relations:\n  parent: EPIC-0009\n", slug="a")
    write_entity("epic", 9, "title: B\nstatus: open\nopened: 2026-08-01\n"
                            "relations:\n  parent: EPIC-0008\n", slug="b")
    assert findings(clean, "cycle")


def test_a_work_item_with_no_parent_is_an_error(clean, write_entity):
    write_entity("work_item", 9, "title: T\nstatus: idea\nopened: 2026-08-01\n",
                 slug="orphan")
    assert findings(clean, "missing-relation")


def test_a_reference_to_a_tombstoned_id_is_a_warning_with_the_rewrite(clean,
                                                                     write_entity):
    write_entity("spike", 5, "title: Moved\nstatus: open\nopened: 2026-08-01\n")
    write_entity("work_item", 9, "title: T\nstatus: idea\nopened: 2026-08-01\n"
                                 "relations:\n  parent: EPIC-0001\n"
                                 "  depends_on: [WI-0300]\n", slug="old-ref")
    Tombstones({"WI-0300": "SPK-0005"},
               clean.root / ".szsdlc" / "tombstones.yml").save()

    found = findings(clean, "tombstoned-ref")
    assert found and not found[0].is_error
    assert found[0].fix == "szsdlc unlink WI-0009 depends_on WI-0300"


# ---------------------------------------------------------------------------
# Status against reality
# ---------------------------------------------------------------------------


def test_a_status_whose_gate_is_unmet_is_reported(clean, write_entity):
    """Only reachable by hand-editing, which is exactly why it is checked."""
    write_entity("work_item", 9, "title: T\nstatus: planned\nopened: 2026-08-01\n"
                                 "relations:\n  parent: EPIC-0001\n", slug="ungated")
    found = findings(clean, "gate-disagreement")
    assert found and "design.md" in found[0].message


def test_every_task_ticked_but_not_terminal_is_a_warning(clean, write_entity):
    write_entity("work_item", 9, "title: T\nstatus: executing\nopened: 2026-08-01\n"
                                 "relations:\n  parent: EPIC-0001\n", slug="done-ish",
                 artifacts={"design.md": "d\n", "plan.md": "- [x] one\n- [x] two\n"})
    found = findings(clean, "progress-disagreement")
    assert found and not found[0].is_error
    assert found[0].fix == "szsdlc set WI-0009 status=review"


# ---------------------------------------------------------------------------
# Roadmap
# ---------------------------------------------------------------------------


def test_a_ready_entity_on_no_roadmap_is_reported(clean, write_entity):
    write_entity("work_item", 9, "title: T\nstatus: ready\nopened: 2026-08-01\n"
                                 "relations:\n  parent: EPIC-0001\n", slug="unplaced",
                 artifacts={"plan.md": "- [ ] one\n"})
    assert findings(clean, "roadmap-missing")


def test_scheduling_twice_dangling_terminal_and_wrong_type_are_reported(clean):
    path = clean.roadmap_path("roadmap")
    path.write_text("now:\n  - WI-0001\n  - REQ-0001\n  - WI-0420\n"
                    "next:\n  - WI-0001\n  - EPIC-0001\nlater: []\n", encoding="utf-8")
    kinds = {f.kind for f in findings(clean)}
    assert {"roadmap-duplicate", "roadmap-dangling", "roadmap-type"} <= kinds


# ---------------------------------------------------------------------------
# Definitions against delivery
# ---------------------------------------------------------------------------


def test_a_settled_definition_nobody_implements_is_a_warning(clean, write_entity):
    write_entity("requirement", 9, "title: Nobody built this\nstatus: approved\n"
                                   "opened: 2026-08-01\n", slug="uncovered")
    found = findings(clean, "uncovered")
    assert found and not found[0].is_error
    assert found[0].ref == "REQ-0009"


def test_a_draft_definition_nobody_implements_is_not_reported(clean, write_entity):
    """`approved` is read off the workflow as the last non-terminal status, so
    this works for a project that calls it something else."""
    write_entity("requirement", 9, "title: Still being written\nstatus: draft\n"
                                   "opened: 2026-08-01\n", slug="draft")
    assert not [f for f in findings(clean, "uncovered") if f.ref == "REQ-0009"]


def test_implementing_an_unsettled_definition_is_a_warning(clean, write_entity):
    write_entity("requirement", 9, "title: Not agreed yet\nstatus: draft\n"
                                   "opened: 2026-08-01\n", slug="draft")
    write_entity("work_item", 9, "title: T\nstatus: idea\nopened: 2026-08-01\n"
                                 "relations:\n  parent: EPIC-0001\n"
                                 "  implements: [REQ-0009]\n", slug="early")
    found = findings(clean, "unsettled-definition")
    assert found and not found[0].is_error
    assert found[0].fix == "szsdlc set REQ-0009 status=approved"


# ---------------------------------------------------------------------------
# Intake
# ---------------------------------------------------------------------------


def test_a_refined_idea_with_no_children_is_an_error(clean, write_entity):
    write_entity("idea", 1, "status: refined\ncaptured: 2026-08-01\n", "a thought\n")
    found = findings(clean, "intake-childless")
    assert found and found[0].is_error


def test_a_refined_idea_with_children_is_fine(clean, write_entity):
    write_entity("idea", 1, "status: refined\ncaptured: 2026-08-01\n", "a thought\n")
    write_entity("spike", 9, "title: Spawned\nstatus: open\nopened: 2026-08-01\n"
                             "relations:\n  refined_from: IDEA-0001\n", slug="spawned")
    assert not findings(clean, "intake-childless")


def test_a_stale_inbox_entry_is_a_warning(clean, write_entity):
    write_entity("idea", 1, "status: inbox\ncaptured: 2020-01-01\n", "an old thought\n")
    found = findings(clean, "intake-stale")
    assert found and not found[0].is_error
    assert "drop" in found[0].fix


def test_a_fresh_inbox_entry_is_not_reported(clean, write_entity):
    import datetime as dt

    write_entity("idea", 1, f"status: inbox\ncaptured: {dt.date.today().isoformat()}\n",
                 "a new thought\n")
    assert not findings(clean, "intake-stale")


# ---------------------------------------------------------------------------
# Tags — warnings only, never errors
# ---------------------------------------------------------------------------


def test_a_near_duplicate_tag_is_a_warning(clean, write_entity):
    write_entity("work_item", 9, "title: T\nstatus: idea\nopened: 2026-08-01\n"
                                 "tags: [dns]\nrelations:\n  parent: EPIC-0001\n",
                 slug="dns")
    write_entity("work_item", 8, "title: T\nstatus: idea\nopened: 2026-08-01\n"
                                 "tags: [dsn]\nrelations:\n  parent: EPIC-0001\n",
                 slug="dsn")
    found = findings(clean, "tag-near-duplicate")
    assert found and not found[0].is_error


def test_no_tag_rule_is_ever_an_error(clean, write_entity):
    write_entity("work_item", 9, "title: T\nstatus: idea\nopened: 2026-08-01\n"
                                 "tags: [DNS, dsn]\nrelations:\n  parent: EPIC-0001\n",
                 slug="tagged")
    tag_findings = [f for f in findings(clean) if f.kind.startswith("tag-")]
    assert tag_findings
    assert all(not f.is_error for f in tag_findings)


def test_unnormalized_tags_are_reported_with_the_rewrite(clean, write_entity):
    write_entity("work_item", 9, "title: T\nstatus: idea\nopened: 2026-08-01\n"
                                 "tags: [DNS]\nrelations:\n  parent: EPIC-0001\n",
                 slug="shouty")
    found = findings(clean, "tag-unnormalized")
    assert found and found[0].fix == "szsdlc tag WI-0009 dns"


def test_singletons_are_only_reported_past_the_configured_threshold(make_project,
                                                                   write_entity):
    project = make_project({"validate": {"tag_singleton_threshold": 2}})
    for number, tag in ((1, "alpha"), (2, "beta"), (3, "gamma")):
        write_entity("decision", number,
                     f"title: D\nstatus: proposed\nopened: 2026-08-01\ntags: [{tag}]\n",
                     slug=f"d{number}")
    assert len(findings(project, "tag-singleton")) == 3


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------


def test_an_orphan_file_in_an_entity_directory_is_a_warning(clean):
    entity_dir = next(clean.dir_for("work_item").glob("WI-0001-*"))
    (entity_dir / "scratch.txt").write_text("notes\n", encoding="utf-8")
    found = findings(clean, "orphan-file")
    assert found and not found[0].is_error
    assert "scratch.txt" in found[0].message


def test_a_broken_wikilink_is_reported(clean):
    entity_dir = next(clean.dir_for("work_item").glob("WI-0001-*"))
    entity = entity_dir / "entity.md"
    entity.write_text(entity.read_text(encoding="utf-8") + "\nSee [[WI-0420]].\n",
                      encoding="utf-8")
    assert findings(clean, "broken-wikilink")


def test_a_working_wikilink_is_not_reported(clean):
    entity_dir = next(clean.dir_for("work_item").glob("WI-0001-*"))
    entity = entity_dir / "entity.md"
    entity.write_text(entity.read_text(encoding="utf-8") + "\nSee [[REQ-0001]].\n",
                      encoding="utf-8")
    assert not findings(clean, "broken-wikilink")


def test_a_broken_relative_link_is_reported_and_a_url_is_not(clean):
    entity_dir = next(clean.dir_for("work_item").glob("WI-0001-*"))
    entity = entity_dir / "entity.md"
    entity.write_text(entity.read_text(encoding="utf-8")
                      + "\n[gone](missing.md) and [ok](https://example.com)\n",
                      encoding="utf-8")
    found = findings(clean, "broken-link")
    assert len(found) == 1
    assert "missing.md" in found[0].message


# ---------------------------------------------------------------------------
# Generated files — staleness is a rule like any other
# ---------------------------------------------------------------------------


def test_a_stale_generated_file_is_reported(clean, write_entity, run):
    write_entity("decision", 9, "title: New decision\nstatus: proposed\n"
                                "opened: 2026-08-01\n", slug="new")
    found = findings(clean, "stale-generated")
    assert found and found[0].fix == "szsdlc sync"


def test_a_hand_edited_generated_file_is_distinguished_from_staleness(clean):
    board = clean.views_dir / "board.md"
    board.write_text(board.read_text(encoding="utf-8") + "\nI typed this.\n",
                     encoding="utf-8")
    kinds = {f.kind for f in findings(clean)}
    assert "hand-edited" in kinds
    # Not merely "stale" — this one loses work when sync runs.
    assert "stale-generated" not in kinds


def test_a_never_generated_view_is_reported(project, write_entity):
    write_entity("epic", 1, "title: E\nstatus: open\nopened: 2026-08-01\n")
    assert len(findings(project, "missing-generated")) == len(project.views)


def test_there_is_one_definition_of_stale(clean, write_entity):
    """validate borrows render's compare(); the two can never disagree."""
    from szsdlc import render as R

    write_entity("decision", 9, "title: New\nstatus: proposed\nopened: 2026-08-01\n",
                 slug="new")
    ids = IdSpace(clean)
    store = load_all(clean, ids)
    renderer = R.Renderer(clean, store, Graph(clean, store, ids))
    assert {item.name for item in R.compare(renderer.all())} == \
        {f.ref for f in findings(clean, "stale-generated")}


# ---------------------------------------------------------------------------
# The command
# ---------------------------------------------------------------------------


def test_errors_exit_four_and_warnings_alone_exit_zero(run, clean, write_entity):
    write_entity("idea", 1, "status: inbox\ncaptured: 2020-01-01\n", "old\n")
    run("sync")
    code, output, _ = run("validate")
    assert code == 0
    assert "warning" in output

    write_entity("work_item", 9, "title: T\nstatus: idea\nopened: 2026-08-01\n",
                 slug="no-parent")
    code, output, _ = run("validate")
    assert code == EXIT_INVALID


def test_findings_are_grouped_most_severe_first(run, clean, write_entity):
    write_entity("idea", 1, "status: inbox\ncaptured: 2020-01-01\n", "old\n")
    write_entity("work_item", 9, "title: T\nstatus: idea\nopened: 2026-08-01\n",
                 slug="no-parent")
    _, output, _ = run("validate")
    levels = [line.split()[0] for line in output.strip().splitlines()[:-1]
              if line.split()[0] in {"error", "warning"}]
    assert levels == sorted(levels)


def test_the_listing_is_bounded_and_the_total_is_printed(run, clean, write_entity):
    for number in range(30):
        write_entity("work_item", 100 + number,
                     "title: T\nstatus: idea\nopened: 2026-08-01\n", slug=f"o{number}")
    _, output, _ = run("validate")
    lines = output.strip().splitlines()
    assert len(lines) == 22  # 20 findings + truncation note + totals
    assert "rerun with --limit 0" in lines[-2]
    assert lines[-1].endswith("warnings") or lines[-1].endswith("warning")


def test_json_carries_every_finding_with_its_fix(run, clean, write_entity):
    write_entity("work_item", 9, "title: T\nstatus: idea\nopened: 2026-08-01\n",
                 slug="no-parent")
    code, output, _ = run("validate", "--json")
    payload = json.loads(output)
    assert code == EXIT_INVALID
    assert {"level", "kind", "ref", "message", "fix"} == set(payload[0])


def test_a_model_invariant_breach_is_not_a_finding(run, clean, monkeypatch):
    """A szsdlc bug is reported as one, never as a statement about the project."""
    from szsdlc.errors import InternalError

    def explode(*_args, **_kwargs):
        raise InternalError("unknown derived kind")

    monkeypatch.setattr("szsdlc.validate.run", explode)
    code, output, err = run("validate")
    assert code == 1
    assert output == ""
    assert err.startswith("internal error:")


def test_context_counters_come_from_the_same_rules(run, clean, write_entity):
    write_entity("work_item", 9, "title: T\nstatus: idea\nopened: 2026-08-01\n",
                 slug="no-parent")
    _, validate_out, _ = run("validate", "--json")
    _, context_out, _ = run("context", "--json")

    payload = json.loads(validate_out)
    counters = json.loads(context_out)["counters"]
    assert counters["errors"] == sum(1 for f in payload if f["level"] == "error")
    assert counters["warnings"] == sum(1 for f in payload if f["level"] == "warning")
