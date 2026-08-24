"""Task 4 — the entity model.

Two properties matter most. Nothing downstream may branch on storage layout:
a `section` entity, a `file` entity and a `directory` entity all answer the
same questions. And loading must be permissive, because `sync` has to render a
half-built project — a corrupt file becomes a node in the graph carrying its
error, not an exception that stops the other 199 entities from loading.
"""

from __future__ import annotations

import textwrap

import pytest

from szsdlc.ids import IdSpace
from szsdlc.model import (
    Entity,
    Progress,
    UnparseableEntity,
    frontmatter_schema,
    load_all,
    parse_progress,
    schema_findings,
)


def load(project):
    return load_all(project, IdSpace(project))


def one(project, type_name: str, number: int) -> Entity:
    ids = IdSpace(project)
    return load(project).require(ids.make(project.type_for(type_name).prefix, number))


# ---------------------------------------------------------------------------
# Every layout, one interface
# ---------------------------------------------------------------------------


def test_every_layout_answers_the_same_questions(make_project, write_entity_in):
    """A section, a file and a directory, asked the same things.

    The three layouts exist so that storage cost matches fidelity, not so that
    callers can tell them apart. The moment a view or a command has to know
    which one it is holding, the abstraction has stopped paying for itself.
    """
    project = make_project({"entity_types": {"decision": {"layout": "file"}}})
    write = write_entity_in(project)

    write("idea", 1, "status: inbox\ncaptured: 2026-08-16\n", "A loose thought\n",
          layout="section")
    write("decision", 1, "title: Use X\nstatus: proposed\nopened: 2026-08-16\n")
    write("work_item", 1, "title: Do it\nstatus: idea\nopened: 2026-08-16\n")

    store = load(project)
    idea = store.by_text("IDEA-0001")
    decision = store.by_text("ADR-0001")
    work_item = store.by_text("WI-0001")

    for entity in (idea, decision, work_item):
        assert entity.status
        assert entity.title
        assert entity.tags == []
        assert entity.relations == {}
        # Asking for an artifact it cannot have answers "no" rather than raising.
        assert entity.has_artifact("design.md") is False
        assert entity.stored_name.startswith(entity.id.text)

    assert idea.home is None and decision.home is None
    assert idea.path == project.section_path("idea")
    assert decision.path.name.startswith("ADR-0001")
    assert work_item.home is not None
    assert work_item.path.name == "entity.md"


def test_artifacts_are_only_real_when_non_empty(project, write_entity):
    write_entity("work_item", 1, "title: T\nstatus: designing\nopened: 2026-08-16\n",
                 artifacts={"design.md": "", "plan.md": "- [ ] one\n"})
    work_item = one(project, "work_item", 1)
    # An empty design.md has not been written; a gate that accepted it would
    # pass on the file existing rather than on the work being done.
    assert work_item.has_artifact("design.md") is False
    assert work_item.has_artifact("plan.md") is True
    assert work_item.has_artifact("absent.md") is False


def test_artifact_files_excludes_the_entity_file(project, write_entity):
    write_entity("work_item", 1, "title: T\nstatus: idea\nopened: 2026-08-16\n",
                 artifacts={"design.md": "x", "notes.txt": "y"})
    names = [p.name for p in one(project, "work_item", 1).artifact_files()]
    assert names == ["design.md", "notes.txt"]


# ---------------------------------------------------------------------------
# Reading state
# ---------------------------------------------------------------------------


def test_title_is_derived_from_the_body_when_absent(project, write_entity):
    """Intake costs one command only if capturing never requires naming."""
    write_entity("idea", 1, "status: inbox\ncaptured: 2026-08-16\n",
                 "\n\n# valkey needs a sentinel quorum story\n\nmore text\n")
    idea = one(project, "idea", 1)
    assert idea.title == "valkey needs a sentinel quorum story"
    assert idea.has_stored_title is False


def test_a_stored_title_wins(project, write_entity):
    write_entity("idea", 1, "title: Stated\nstatus: inbox\ncaptured: 2026-08-16\n",
                 "Derived would be this\n")
    assert one(project, "idea", 1).title == "Stated"


def test_an_empty_body_derives_an_empty_title(project, write_entity):
    write_entity("idea", 1, "status: inbox\ncaptured: 2026-08-16\n", "")
    assert one(project, "idea", 1).title == ""


def test_tags_are_exposed_both_as_written_and_normalized(project, write_entity):
    write_entity("work_item", 1,
                 "title: T\nstatus: idea\nopened: 2026-08-16\ntags: [DNS, ' tls ', dns]\n")
    work_item = one(project, "work_item", 1)
    assert work_item.raw_tags == ["DNS", " tls ", "dns"]
    # Normalizing on read as well as write is what lets validate see that the
    # file needs rewriting without the view showing three spellings.
    assert work_item.tags == ["dns", "tls"]


def test_relation_targets_are_held_as_written(project, write_entity):
    """A dangling reference must be representable, or it could never be reported."""
    write_entity("work_item", 1, textwrap.dedent("""\
        title: T
        status: idea
        opened: 2026-08-16
        relations:
          parent: EPIC-0011
          implements: [REQ-0007, REQ-0420]
        """))
    work_item = one(project, "work_item", 1)
    assert work_item.parent() == "EPIC-0011"
    assert work_item.targets("implements") == ["REQ-0007", "REQ-0420"]
    assert work_item.targets("depends_on") == []


def test_a_scalar_authored_for_a_many_relation_still_reads_as_a_list(project, write_entity):
    write_entity("work_item", 1,
                 "title: T\nstatus: idea\nopened: 2026-08-16\n"
                 "relations:\n  implements: REQ-0007\n")
    assert one(project, "work_item", 1).targets("implements") == ["REQ-0007"]


def test_a_status_outside_the_workflow_loads_as_written(project, write_entity):
    """A hand-edited file must be readable; `set` is what refuses to create one."""
    write_entity("work_item", 1, "title: T\nstatus: marinating\nopened: 2026-08-16\n")
    work_item = one(project, "work_item", 1)
    assert work_item.status == "marinating"
    assert work_item.is_terminal is False


# ---------------------------------------------------------------------------
# Progress
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text,done,total", [
    ("", 0, 0),
    ("- [ ] one\n- [x] two\n", 1, 2),
    ("* [X] star\n+ [ ] plus\n", 1, 2),
    ("  - [x] indented\n", 1, 1),
    ("- [] malformed\n- [ ]no space\n", 0, 0),
    ("prose about - [ ] not a task\n", 0, 0),
])
def test_progress_counts_checkboxes(text, done, total):
    progress = parse_progress(text)
    assert (progress.done, progress.total) == (done, total)


def test_progress_is_none_for_a_type_that_does_not_track_it(project, write_entity):
    """None, not zero — "0% done" is a lie about something with no tasks."""
    write_entity("spike", 1, "title: T\nstatus: open\nopened: 2026-08-16\n")
    assert one(project, "spike", 1).progress is None


def test_progress_comes_from_the_configured_artifact(project, write_entity):
    write_entity("work_item", 1, "title: T\nstatus: executing\nopened: 2026-08-16\n",
                 artifacts={"plan.md": "- [x] a\n- [x] b\n- [ ] c\n",
                            "design.md": "- [ ] not counted\n"})
    progress = one(project, "work_item", 1).progress
    assert str(progress) == "2/3"
    assert progress.percent == 67
    assert progress.complete is False
    assert progress.remaining == 1


def test_an_empty_plan_is_not_complete():
    assert Progress(0, 0).complete is False
    assert Progress(3, 3).complete is True


# ---------------------------------------------------------------------------
# Mutation
# ---------------------------------------------------------------------------


def test_saving_after_one_change_rewrites_one_line(project, write_entity):
    home = write_entity("work_item", 1, "title: T\nstatus: idea\nopened: 2026-08-16\n",
                        "Body stays.\n")
    path = home / project.entity_filename
    before = path.read_bytes()

    work_item = one(project, "work_item", 1)
    work_item.set_status("groomed")
    work_item.save()

    after = path.read_bytes()
    assert after != before
    assert after == before.replace(b"status: idea", b"status: groomed")


def test_set_tags_normalizes_and_drops_the_key_when_empty(project, write_entity):
    write_entity("work_item", 1, "title: T\nstatus: idea\nopened: 2026-08-16\n")
    work_item = one(project, "work_item", 1)

    work_item.set_tags(["DNS", "  tls  ", "dns"])
    assert "tags: [dns, tls]" in work_item.render()

    work_item.set_tags([])
    assert "tags:" not in work_item.render()


def test_set_relation_follows_the_configured_cardinality(project, write_entity):
    write_entity("work_item", 1, "title: T\nstatus: idea\nopened: 2026-08-16\n")
    work_item = one(project, "work_item", 1)

    work_item.set_relation("parent", ["EPIC-0011"])
    work_item.set_relation("implements", ["REQ-0007", "REQ-0008"])
    rendered = work_item.render()

    # `parent` is single-valued, so it is written as a scalar — the way an
    # author would write it, not as a one-element list.
    assert "  parent: EPIC-0011\n" in rendered
    assert "  implements:\n    - REQ-0007\n    - REQ-0008\n" in rendered


def test_clearing_the_last_relation_removes_the_block(project, write_entity):
    write_entity("work_item", 1, "title: T\nstatus: idea\nopened: 2026-08-16\n"
                                 "relations:\n  parent: EPIC-0011\n")
    work_item = one(project, "work_item", 1)
    work_item.set_relation("parent", None)
    assert "relations:" not in work_item.render()


# ---------------------------------------------------------------------------
# Frontmatter schema, composed per type
# ---------------------------------------------------------------------------


def test_intake_types_require_only_id_and_status(project):
    idea = frontmatter_schema(project, project.type_for("idea"))
    work_item = frontmatter_schema(project, project.type_for("work_item"))
    assert set(idea["required"]) == {"id", "status", "captured"}
    assert "title" not in idea["required"]
    assert "title" in work_item["required"]


def test_the_schema_allows_only_this_type_relations(project):
    schema = frontmatter_schema(project, project.type_for("requirement"))
    relations = schema["properties"]["relations"]["properties"]
    assert "parent" not in relations
    assert "supersedes" in relations


def test_a_clean_entity_produces_no_findings(project, write_entity):
    write_entity("work_item", 1, "title: T\nstatus: idea\nopened: 2026-08-16\n"
                                 "tags: [dns]\nrelations:\n  parent: EPIC-0011\n")
    assert schema_findings(project, one(project, "work_item", 1)) == []


def test_a_missing_required_field_is_a_finding(project, write_entity):
    write_entity("work_item", 1, "title: T\nstatus: idea\n")
    findings = schema_findings(project, one(project, "work_item", 1))
    assert any("opened" in f for f in findings)


def test_an_unknown_frontmatter_key_is_a_finding(project, write_entity):
    write_entity("work_item", 1,
                 "title: T\nstatus: idea\nopened: 2026-08-16\npriority: high\n")
    findings = schema_findings(project, one(project, "work_item", 1))
    # There is no priority field anywhere in the model: placement is a fact
    # about a set and lives on the roadmap.
    assert any("priority" in f for f in findings)


def test_a_relation_this_type_may_not_author_is_a_finding(project, write_entity):
    write_entity("requirement", 1, "title: T\nstatus: draft\nopened: 2026-08-16\n"
                                   "relations:\n  parent: EPIC-0011\n")
    findings = schema_findings(project, one(project, "requirement", 1))
    assert any("parent" in f for f in findings)


def test_findings_are_returned_never_raised(project, write_entity):
    """They describe the project, and sync has to render a project that has them."""
    write_entity("work_item", 1, "status: idea\n")
    entity = one(project, "work_item", 1)
    assert schema_findings(project, entity)  # does not raise
    assert entity.status == "idea"


# ---------------------------------------------------------------------------
# Invalidity is represented, not raised
# ---------------------------------------------------------------------------


def test_one_corrupt_file_does_not_stop_the_others(project, write_entity):
    for number in range(1, 6):
        write_entity("work_item", number,
                     "title: T\nstatus: idea\nopened: 2026-08-16\n", slug=f"w{number}")
    write_entity("work_item", 3, raw="---\ntitle: [unclosed\n---\nbody\n", slug="w3")

    store = load(project)
    assert len(store) == 4
    assert [u.ref for u in store.unparseable] == ["WI-0003"]
    assert "WI-0003" in str(store.unparseable[0])
    assert store.unparseable[0].path.exists()


def test_a_file_with_no_frontmatter_at_all_is_represented(project, write_entity):
    write_entity("work_item", 1, raw="Just prose, no frontmatter.\n")
    store = load(project)
    assert len(store) == 0
    assert isinstance(store.unparseable[0], UnparseableEntity)
    assert "must open with ---" in store.unparseable[0].error


def test_a_directory_entity_missing_its_entity_file_is_represented(project):
    (project.dir_for("work_item") / "WI-0001-empty").mkdir()
    store = load(project)
    assert "entity.md is missing" in store.unparseable[0].error
    assert store.unparseable[0].entity_id.text == "WI-0001"


def test_a_duplicated_id_is_reported_with_both_paths(project, write_entity):
    """Directory-scan allocation cannot prevent this, so it must surface it."""
    first = write_entity("work_item", 1, "title: A\nstatus: idea\nopened: 2026-08-16\n",
                         slug="first")
    write_entity("work_item", 1, "title: B\nstatus: idea\nopened: 2026-08-16\n",
                 slug="second")

    store = load(project)
    assert len(store) == 1
    assert store.by_text("WI-0001").path.parent == first
    assert [entity_id.text for entity_id, _ in store.duplicates] == ["WI-0001"]
    assert store.duplicates[0][1].name == "WI-0001-second"


def test_a_duplicate_of_a_broken_entity_still_counts_as_a_duplicate(project, write_entity):
    write_entity("work_item", 1, raw="broken\n", slug="first")
    write_entity("work_item", 1, "title: B\nstatus: idea\nopened: 2026-08-16\n",
                 slug="second")
    store = load(project)
    assert len(store.unparseable) == 1
    assert len(store.duplicates) == 1
    assert len(store) == 0


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------


def test_entities_load_in_deterministic_id_order(project, write_entity):
    for number in (9, 1, 10, 2):
        write_entity("work_item", number,
                     "title: T\nstatus: idea\nopened: 2026-08-16\n", slug=f"w{number}")
    ordered = [e.id.text for e in load(project)]
    # Numeric, not lexical: WI-0010 must not sort before WI-0002.
    assert ordered == ["WI-0001", "WI-0002", "WI-0009", "WI-0010"]


def test_the_store_selects_by_flag_rather_than_by_type_name(project, write_entity):
    write_entity("work_item", 1, "title: T\nstatus: idea\nopened: 2026-08-16\n")
    write_entity("spike", 1, "title: T\nstatus: open\nopened: 2026-08-16\n")
    write_entity("requirement", 1, "title: T\nstatus: draft\nopened: 2026-08-16\n")

    store = load(project)
    assert {e.id.text for e in store.with_flag("actionable")} == {"WI-0001", "SPK-0001"}
    assert {e.id.text for e in store.with_flag("persistent")} == {"REQ-0001"}
    assert [e.id.text for e in store.of_type("spike")] == ["SPK-0001"]


def test_an_empty_project_loads_to_an_empty_store(project):
    store = load(project)
    assert len(store) == 0 and not store.unparseable and not store.duplicates
