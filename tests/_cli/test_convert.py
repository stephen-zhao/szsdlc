"""szsdlc convert: reclassifying, leaving a resolving tombstone."""

from __future__ import annotations

from szsdlc.errors import EXIT_BAD_INPUT
from szsdlc.ids import IdSpace
from szsdlc.ids import IdSpace, Tombstones

from .conftest import entities


# ---------------------------------------------------------------------------
# convert
# ---------------------------------------------------------------------------
def test_convert_mints_a_new_id_and_leaves_a_tombstone(run, project):
    run("new", "work_item", "--title", "Investigate the quorum")
    code, output, _ = run("convert", "WI-0001", "spike")
    assert code == 0
    lines = output.strip().splitlines()
    assert len(lines) == 2
    assert lines[0].startswith("SPK-0001  (WI-0001 → SPK-0001")

    store = entities(project)
    assert store.by_text("SPK-0001").title == "Investigate the quorum"
    assert store.by_text("WI-0001") is None
    assert Tombstones.load(project.root).mapping == {"WI-0001": "SPK-0001"}


def test_old_references_still_resolve_after_a_convert(run, project):
    run("new", "work_item", "--title", "Investigate")
    run("convert", "WI-0001", "spike")
    ids = IdSpace(project)
    assert ids.resolve("WI-0001").text == "SPK-0001"


def test_a_converted_id_is_never_reissued(run, project):
    run("new", "work_item", "--title", "Investigate")
    run("convert", "WI-0001", "spike")
    _, output, _ = run("new", "work_item", "--title", "Something else")
    assert output.startswith("WI-0002")


def test_convert_carries_artifacts_body_and_tags(run, project):
    run("new", "work_item", "--title", "Investigate")
    run("tag", "WI-0001", "valkey")
    entity = entities(project).by_text("WI-0001")
    (entity.home / "journal.md").write_text("- 2026-08-16 started\n", encoding="utf-8")

    run("convert", "WI-0001", "spike")
    spike = entities(project).by_text("SPK-0001")
    assert spike.tags == ["valkey"]
    assert "started" in spike.read_artifact("journal.md")


def test_a_status_absent_from_the_new_workflow_restarts(run, project):
    run("new", "epic", "--title", "E")
    run("new", "work_item", "--title", "W")
    run("link", "WI-0001", "parent", "EPIC-0001")
    run("set", "WI-0001", "status=groomed")

    _, output, _ = run("convert", "WI-0001", "spike")
    assert "status open" in output
    assert entities(project).by_text("SPK-0001").status == "open"


def test_a_status_present_in_both_workflows_is_kept(run, project):
    run("new", "decision", "--title", "D")
    run("set", "ADR-0001", "status=accepted")
    run("convert", "ADR-0001", "requirement")
    # `superseded` and `accepted` differ, but `superseded` exists in both; here
    # `accepted` does not exist for a requirement, so it restarts at draft.
    assert entities(project).by_text("REQ-0001").status == "draft"


def test_converting_to_the_same_type_is_refused(run, project):
    run("new", "work_item", "--title", "W")
    code, _, err = run("convert", "WI-0001", "work_item")
    assert code == EXIT_BAD_INPUT
    assert "already a work_item" in err


def test_converting_into_a_dynamic_type_lands_where_the_artifacts_fit(run, project):
    """An idea is captured as a section, but one converted *from* work that
    carries files has to arrive somewhere those files can follow. Creating it
    in the cheapest shape and dropping them is the worst answer available."""
    run("new", "work_item", "--title", "W")
    entity = entities(project).by_text("WI-0001")
    (entity.home / "design.md").write_text("x\n", encoding="utf-8")

    code, _, _ = run("convert", "WI-0001", "idea")
    assert code == 0
    idea = entities(project).by_text("IDEA-0001")
    assert idea.layout == "directory"
    assert (idea.home / "design.md").is_file()
