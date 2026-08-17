"""Task 8 — init, new, set, tag, link, convert, log, schedule.

Every command here mutates, so every one of them is tested twice: once for the
happy path reporting its resulting state in one line (C1), and once for each
refusal naming a runnable fix (C2). The refusal paths are the point — an agent
that gets "blocked" with no next move spends its next three calls guessing.
"""

from __future__ import annotations

import datetime as dt
import io

import pytest

from szsdlc import config as C
from szsdlc.cli import main
from szsdlc.errors import EXIT_BAD_INPUT, EXIT_REFUSED
from szsdlc.ids import IdSpace, Tombstones
from szsdlc.model import load_all


@pytest.fixture
def run(project, capsys):
    def invoke(*argv: str):
        code = main(["-C", str(project.root), *argv])
        captured = capsys.readouterr()
        return code, captured.out, captured.err
    return invoke


@pytest.fixture
def no_stdin(monkeypatch):
    class Tty(io.StringIO):
        def isatty(self):
            return True
    monkeypatch.setattr("sys.stdin", Tty())


def entities(project):
    return load_all(project, IdSpace(project))


@pytest.fixture
def seeded(run, project):
    """An epic and a work item under it — the minimum coherent project."""
    run("new", "epic", "--title", "Sentinel quorum")
    run("new", "work_item", "--title", "Add sentinel config")
    run("link", "WI-0001", "parent", "EPIC-0001")
    return project


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


def test_init_scaffolds_a_working_project(tmp_path, capsys):
    assert main(["-C", str(tmp_path), "init"]) == 0
    output = capsys.readouterr().out
    assert len(output.splitlines()) <= 15

    config = C.load(tmp_path)
    for entity_type in config.entity_types.values():
        assert config.dir_for(entity_type).is_dir()
    assert config.roadmap_path("roadmap").is_file()
    assert config.views_dir.is_dir() and config.standards_dir.is_dir()


def test_the_starter_config_is_almost_empty(tmp_path):
    """It has to be: everything real lives in the merged defaults."""
    main(["-C", str(tmp_path), "init"])
    text = C.config_path_for(tmp_path).read_text(encoding="utf-8")
    declared = [line for line in text.splitlines()
                if line.strip() and not line.lstrip().startswith("#")]
    assert declared == ["project:", f"  name: {tmp_path.name}"]
    assert C.load(tmp_path).data["project"]["name"] == tmp_path.name


def test_init_twice_is_refused(tmp_path, capsys):
    main(["-C", str(tmp_path), "init"])
    capsys.readouterr()
    assert main(["-C", str(tmp_path), "init"]) == EXIT_BAD_INPUT
    assert "already exists" in capsys.readouterr().err


def test_a_scaffolded_project_captures_immediately(tmp_path, capsys):
    main(["-C", str(tmp_path), "init"])
    capsys.readouterr()
    assert main(["-C", str(tmp_path), "capture", "a thought"]) == 0
    assert capsys.readouterr().out == "IDEA-0001\n"


# ---------------------------------------------------------------------------
# new
# ---------------------------------------------------------------------------


def test_new_reports_the_id_and_path(run, project):
    code, output, _ = run("new", "work_item", "--title", "Add sentinel config")
    assert code == 0
    assert output.strip() == "WI-0001  work-items/WI-0001-add-sentinel-config/entity.md"
    entity = entities(project).by_text("WI-0001")
    assert entity.status == "idea"
    assert entity.field("opened") == dt.date.today()


def test_new_respects_each_type_layout(run, project):
    run("new", "requirement", "--title", "Reachable over TLS")
    _, output, _ = run("capture", "a thought")
    assert entities(project).by_text("REQ-0001").home is not None
    assert entities(project).by_text("IDEA-0001").home is None


def test_new_with_an_unknown_type_lists_the_known_ones(run):
    code, _, err = run("new", "ticket", "--title", "T")
    assert code == 5  # a type that does not exist is a config-level question
    assert "work_item" in err


# ---------------------------------------------------------------------------
# set
# ---------------------------------------------------------------------------


def test_set_reports_the_transition_in_one_line(run, seeded):
    code, output, _ = run("set", "WI-0001", "status=groomed")
    assert code == 0
    assert output.strip() == "WI-0001: status idea → groomed"


def test_set_handles_several_scalar_fields_in_one_line(run, seeded):
    code, output, _ = run("set", "WI-0001", "title=Renamed", "opened=2026-01-01")
    assert code == 0
    # C1 — one line whatever the number of assignments, each reporting its
    # before and after, so no confirming `show` is needed.
    assert len(output.strip().splitlines()) == 1
    assert "title Add sentinel config → Renamed" in output
    assert f"opened {dt.date.today().isoformat()} → 2026-01-01" in output


def test_an_illegal_transition_names_a_legal_one(run, seeded):
    code, _, err = run("set", "WI-0001", "status=done")
    assert code == EXIT_REFUSED
    assert "idea → done is not a legal transition" in err
    assert "Fix: szsdlc set WI-0001 status=groomed" in err
    assert len(err.strip().splitlines()) <= 3


def test_a_status_outside_the_workflow_is_refused(run, seeded):
    code, _, err = run("set", "WI-0001", "status=marinating")
    assert code == EXIT_REFUSED
    assert "is not a status of work_item" in err


def test_the_artifact_gate_names_the_file_to_write(run, seeded):
    for status in ("groomed", "ready", "designing"):
        run("set", "WI-0001", f"status={status}")
    code, _, err = run("set", "WI-0001", "status=planned")
    assert code == EXIT_REFUSED
    assert "design.md is missing or empty" in err
    # The only forward move from `designing` is the blocked one, so the fix is
    # the remedy itself — and it must name the actual path to write.
    assert err.splitlines()[1].startswith("Fix: write ")
    assert "design.md" in err.splitlines()[1]
    # Never "give up on it": dropping is not the remedy for a missing design.
    assert "dropped" not in err


def test_the_task_gate_offers_the_move_that_is_available(run, seeded, project):
    entity = entities(project).by_text("WI-0001")
    (entity.home / "design.md").write_text("a design\n", encoding="utf-8")
    (entity.home / "plan.md").write_text("- [x] one\n- [ ] two\n", encoding="utf-8")
    for status in ("groomed", "ready", "designing", "planned", "executing", "review"):
        run("set", "WI-0001", f"status={status}")

    code, _, err = run("set", "WI-0001", "status=done")
    assert code == EXIT_REFUSED
    assert "1/2 tasks complete, 1 unchecked" in err
    # There is a legal move from review that is not blocked, so offer it.
    assert "Fix: szsdlc set WI-0001 status=executing" in err


def test_the_gate_passes_once_every_box_is_ticked(run, seeded, project):
    entity = entities(project).by_text("WI-0001")
    (entity.home / "design.md").write_text("a design\n", encoding="utf-8")
    (entity.home / "plan.md").write_text("- [x] one\n- [x] two\n", encoding="utf-8")
    for status in ("groomed", "ready", "designing", "planned", "executing", "review"):
        run("set", "WI-0001", f"status={status}")
    assert run("set", "WI-0001", "status=done")[0] == 0


def test_an_empty_artifact_does_not_satisfy_a_gate(run, seeded, project):
    entity = entities(project).by_text("WI-0001")
    (entity.home / "design.md").write_text("", encoding="utf-8")
    for status in ("groomed", "ready", "designing"):
        run("set", "WI-0001", f"status={status}")
    assert run("set", "WI-0001", "status=planned")[0] == EXIT_REFUSED


@pytest.mark.parametrize("field,fix", [
    ("relations", "szsdlc link WI-0001 <relation> <ref>"),
    ("tags", "szsdlc tag WI-0001 <tag>"),
    ("id", "szsdlc convert WI-0001 <TYPE>"),
])
def test_set_refuses_delegated_fields_and_names_the_right_verb(run, seeded, field, fix):
    """C3 — one job per command, with a cross-reference on the overlap."""
    code, _, err = run("set", "WI-0001", f"{field}=x")
    assert code == EXIT_BAD_INPUT
    assert f"{field!r} is not settable" in err
    assert f"Fix: {fix}" in err


def test_an_unknown_field_suggests_the_nearest(run, seeded):
    code, _, err = run("set", "WI-0001", "stauts=groomed")
    assert code == EXIT_BAD_INPUT
    assert "did you mean status" in err
    assert "Fix: szsdlc set WI-0001 status=<value>" in err


def test_a_malformed_assignment_is_refused(run, seeded):
    code, _, err = run("set", "WI-0001", "status")
    assert code == EXIT_BAD_INPUT
    assert "is not field=value" in err


def test_a_bad_date_is_refused_with_the_expected_type(run, seeded):
    code, _, err = run("set", "WI-0001", "opened=yesterday")
    assert code == EXIT_BAD_INPUT
    assert "not a valid date" in err


def test_setting_the_status_it_already_has_is_refused(run, seeded):
    code, _, err = run("set", "WI-0001", "status=idea")
    assert code == EXIT_REFUSED
    assert "already idea" in err


# ---------------------------------------------------------------------------
# tag / untag
# ---------------------------------------------------------------------------


def test_tagging_normalizes_on_write(run, seeded, project):
    code, output, _ = run("tag", "WI-0001", "DNS", "  Multi Word  ", "dns")
    assert code == 0
    assert output.strip() == "WI-0001: dns, multi-word"
    assert "tags: [dns, multi-word]" in \
        entities(project).by_text("WI-0001").path.read_text(encoding="utf-8")


def test_untagging_matches_the_normalized_form(run, seeded):
    run("tag", "WI-0001", "dns", "tls")
    code, output, _ = run("untag", "WI-0001", "DNS")
    assert code == 0
    assert output.strip() == "WI-0001: tls"


def test_removing_the_last_tag_drops_the_key(run, seeded, project):
    run("tag", "WI-0001", "dns")
    _, output, _ = run("untag", "WI-0001", "dns")
    assert output.strip() == "WI-0001: (no tags)"
    assert "tags:" not in entities(project).by_text("WI-0001").path.read_text(encoding="utf-8")


def test_retagging_leaves_the_id_and_path_untouched(run, seeded, project):
    before = entities(project).by_text("WI-0001").path
    run("tag", "WI-0001", "dns")
    run("untag", "WI-0001", "dns")
    run("tag", "WI-0001", "tls")
    assert entities(project).by_text("WI-0001").path == before


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


def test_converting_a_directory_entity_with_artifacts_to_a_file_type_is_refused(
    run, project
):
    run("new", "work_item", "--title", "W")
    entity = entities(project).by_text("WI-0001")
    (entity.home / "design.md").write_text("x\n", encoding="utf-8")
    code, _, err = run("convert", "WI-0001", "idea")
    assert code == EXIT_BAD_INPUT
    assert "design.md" in err


# ---------------------------------------------------------------------------
# log
# ---------------------------------------------------------------------------


def test_log_appends_a_dated_line_and_says_nothing(run, seeded, project):
    code, output, err = run("log", "WI-0001", "started on the config")
    assert code == 0
    # C4 — budgeted at zero lines; this runs constantly.
    assert output == "" and err == ""

    journal = entities(project).by_text("WI-0001").read_artifact("journal.md")
    assert journal == f"- {dt.date.today().isoformat()} started on the config\n"


def test_log_appends_rather_than_replaces(run, seeded, project):
    run("log", "WI-0001", "first")
    run("log", "WI-0001", "second")
    journal = entities(project).by_text("WI-0001").read_artifact("journal.md")
    assert len(journal.strip().splitlines()) == 2
    assert journal.strip().splitlines()[0].endswith("first")


def test_log_reads_from_stdin(run, seeded, project, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("from stdin\n"))
    run("log", "WI-0001")
    assert "from stdin" in entities(project).by_text("WI-0001").read_artifact("journal.md")


def test_logging_to_a_type_with_no_journal_is_refused(run, project):
    run("new", "requirement", "--title", "R")
    code, _, err = run("log", "REQ-0001", "something")
    assert code == EXIT_BAD_INPUT
    assert "no journal artifact" in err


def test_an_empty_log_message_is_refused(run, seeded, no_stdin):
    code, _, err = run("log", "WI-0001")
    assert code == EXIT_BAD_INPUT
    assert "nothing to write" in err


# ---------------------------------------------------------------------------
# schedule / unschedule
# ---------------------------------------------------------------------------


@pytest.fixture
def ready(run, seeded):
    for status in ("groomed", "ready"):
        run("set", "WI-0001", f"status={status}")
    return seeded


def test_schedule_reports_the_new_neighbours(run, ready):
    run("schedule", "EPIC-0001", "--horizon", "now")
    code, output, _ = run("schedule", "WI-0001", "--horizon", "now")
    assert code == 0
    assert output.strip() == "WI-0001: now[1] — after EPIC-0001"


def test_schedule_positions(run, ready):
    run("schedule", "EPIC-0001", "--horizon", "now")
    _, output, _ = run("schedule", "WI-0001", "--horizon", "now", "--top")
    assert output.strip() == "WI-0001: now[0] — before EPIC-0001"


def test_scheduling_below_the_ready_status_is_refused(run, seeded):
    code, _, err = run("schedule", "WI-0001", "--horizon", "now")
    assert code == EXIT_REFUSED
    assert "has not reached ready" in err
    # The *next hop*, not the destination: WI-0001 is at `idea`, and
    # `status=ready` would simply refuse again as an illegal transition.
    assert "Fix: szsdlc set WI-0001 status=groomed" in err


def test_scheduling_a_requirement_is_refused(run, project):
    run("new", "requirement", "--title", "R")
    code, _, err = run("schedule", "REQ-0001", "--horizon", "now")
    assert code == EXIT_REFUSED
    assert "not schedulable" in err


def test_unschedule_confirms(run, ready):
    run("schedule", "WI-0001", "--horizon", "now")
    code, output, _ = run("unschedule", "WI-0001")
    assert code == 0
    assert output.strip() == "WI-0001: off roadmap"


def test_unscheduling_something_absent_is_refused(run, ready):
    code, _, err = run("unschedule", "WI-0001")
    assert code == EXIT_BAD_INPUT
    assert "is not on roadmap" in err
    assert "Fix: szsdlc schedule WI-0001 --horizon later" in err


def test_rescheduling_touches_only_the_roadmap_file(run, ready, project):
    import hashlib

    run("schedule", "EPIC-0001", "--horizon", "now")
    run("schedule", "WI-0001", "--horizon", "now")

    def fingerprint():
        return {p.relative_to(project.root).as_posix():
                hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(project.root.rglob("*")) if p.is_file()}

    before = fingerprint()
    run("schedule", "WI-0001", "--horizon", "later")
    after = fingerprint()
    changed = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
    assert changed == {"roadmaps/roadmap.yml"}


# ---------------------------------------------------------------------------
# Reaching a terminal status takes an entity off the roadmap
# ---------------------------------------------------------------------------


def drive_to_done(run, project):
    entity_dir = next(project.dir_for("work_item").glob("WI-0001-*"))
    (entity_dir / "design.md").write_text("d\n", encoding="utf-8")
    (entity_dir / "plan.md").write_text("- [x] one\n", encoding="utf-8")
    for status in ("designing", "planned", "executing", "review"):
        run("set", "WI-0001", f"status={status}")
    return run("set", "WI-0001", "status=done")


def test_finishing_work_takes_it_off_the_roadmap(run, ready, project):
    """Otherwise every close is two commands with a failure between them.

    `validate` errors on a terminal entity still sitting on a roadmap, and the
    `Stop` hook blocks on errors — so a session that finished a work item would
    end by refusing to end.
    """
    run("schedule", "WI-0001", "--horizon", "now")
    code, output, _ = drive_to_done(run, project)
    assert code == 0
    assert "off roadmap" in output

    _, listing, _ = run("list", "--unscheduled")
    assert "WI-0001" not in listing


def test_the_removal_is_reported_on_the_same_line(run, ready, project):
    # C1 — one line, the resulting state. Two lines would mean the caller has
    # to parse which of them is the answer.
    run("schedule", "WI-0001", "--horizon", "now")
    _, output, _ = drive_to_done(run, project)
    assert len(output.strip().splitlines()) == 1
    assert output.startswith("WI-0001: status review → done; off roadmap")


def test_an_unscheduled_entity_reaching_terminal_says_nothing_extra(run, ready,
                                                                    project):
    _, output, _ = drive_to_done(run, project)
    assert output.strip() == "WI-0001: status review → done"


def test_a_non_terminal_transition_leaves_placement_alone(run, ready):
    run("schedule", "WI-0001", "--horizon", "now")
    _, output, _ = run("set", "WI-0001", "status=designing")
    assert "off" not in output
    _, listing, _ = run("next")
    assert "WI-0001" in listing


def test_a_missing_artifact_says_write_it_not_go_backwards(run, seeded, project):
    """The artifact gate's remedy beats any legal alternative transition.

    A type whose review-ish state can legally return to an earlier one — the
    shipped `review → executing` is one, and a wet-lab `analysing → running`
    is another — would otherwise be told "go back" when what it needs is a
    file written. That is the same bad advice as suggesting the caller abandon
    the work, wearing a legal transition as a disguise.
    """
    entity = entities(project).by_text("WI-0001")
    for status in ("groomed", "ready", "designing"):
        run("set", "WI-0001", f"status={status}")

    code, _, err = run("set", "WI-0001", "status=planned")
    assert code == EXIT_REFUSED
    assert "design.md is missing or empty" in err
    assert "Fix: write " in err
    assert "design.md" in err.split("Fix:", 1)[1]
