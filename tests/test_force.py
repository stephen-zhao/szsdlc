"""WI-0051 — `set --force --reason`: any status change, gates intact.

Covered on the default types and on a config whose states share no name with
them, because `--force` must name no type and no state.
"""

from __future__ import annotations

import datetime as dt
import io
import textwrap

import pytest

from szsdlc import config as C
from szsdlc.cli import main
from szsdlc.errors import EXIT_BAD_INPUT, EXIT_REFUSED
from szsdlc.ids import IdSpace
from szsdlc.model import load_all

TODAY = dt.date.today().isoformat()


@pytest.fixture(autouse=True)
def tty_stdin(monkeypatch):
    """A terminal on stdin, so a missing --reason is refused, not read."""
    class Tty(io.StringIO):
        def isatty(self):
            return True
    monkeypatch.setattr("sys.stdin", Tty())


@pytest.fixture
def run(project, capsys):
    def invoke(*argv: str):
        code = main(["-C", str(project.root), *argv])
        captured = capsys.readouterr()
        return code, captured.out.strip(), captured.err
    return invoke


def entity(config, ref):
    return load_all(config, IdSpace(config)).by_text(ref)


def journal(config, ref):
    path = entity(config, ref).artifact_path("journal.md")
    return path.read_text(encoding="utf-8") if path.is_file() else ""


@pytest.fixture
def item(run, project):
    run("new", "epic", "--title", "Sentinel quorum")
    run("new", "work_item", "--title", "Add sentinel config")
    run("link", "WI-0001", "parent", "EPIC-0001")
    run("set", "WI-0001", "status=groomed")
    return project


def finish(project, run, ref="WI-0001"):
    """Write ticked artifacts and force straight to `done` — the gate is met."""
    home = entity(project, ref).home
    (home / "design.md").write_text("# Design\n", encoding="utf-8")
    (home / "plan.md").write_text("- [x] it\n", encoding="utf-8")
    code, _, err = run("set", ref, "status=done", "--force", "--reason", "finished")
    assert code == 0, err


# ---------------------------------------------------------------------------
# moves the workflow does not list
# ---------------------------------------------------------------------------


def test_moving_back_before_work_starts(run, item):
    code, output, _ = run("set", "WI-0001", "status=idea", "--force",
                          "--reason", "grooming revised")
    assert code == 0
    assert output == "WI-0001: status groomed → idea (forced: grooming revised)"
    assert entity(item, "WI-0001").status == "idea"
    assert journal(item, "WI-0001") == f"- {TODAY} forced groomed → idea: grooming revised\n"
    assert entity(item, "WI-0001").field("forced_reason") == \
        f"{TODAY} groomed → idea: grooming revised"


def test_skipping_ahead_past_an_edge(run, item):
    code, output, _ = run("set", "WI-0001", "status=designing", "--force",
                          "--reason", "already understood")
    assert code == 0 and "(forced: already understood)" in output
    assert entity(item, "WI-0001").status == "designing"


def test_recovering_from_an_undeclared_status(run, item):
    path = entity(item, "WI-0001").path
    path.write_text(path.read_text(encoding="utf-8").replace(
        "status: groomed", "status: triaged"), encoding="utf-8")

    code, _, err = run("set", "WI-0001", "status=ready")
    assert code == EXIT_REFUSED and "not in the work_item workflow" in err

    code, output, _ = run("set", "WI-0001", "status=ready", "--force",
                          "--reason", "renamed status")
    assert code == 0
    assert output == "WI-0001: status triaged → ready (forced: renamed status)"


def test_reason_can_arrive_on_stdin(run, item, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("not next\n"))
    code, output, _ = run("set", "WI-0001", "status=idea", "--force")
    assert code == 0 and output.endswith("(forced: not next)")


def test_forcing_a_legal_move_records_nothing(run, item):
    code, output, _ = run("set", "WI-0001", "status=ready", "--force",
                          "--reason", "unneeded")
    assert code == 0
    assert output == "WI-0001: status groomed → ready"
    assert journal(item, "WI-0001") == ""
    assert entity(item, "WI-0001").field("forced_reason") is None


# ---------------------------------------------------------------------------
# what --force does not lift
# ---------------------------------------------------------------------------


def test_a_forced_move_needs_a_reason(run, item):
    code, output, err = run("set", "WI-0001", "status=idea", "--force")
    assert code == EXIT_BAD_INPUT and output == ""
    assert 'Fix: szsdlc set WI-0001 status=idea --force --reason "why this move"' in err
    assert entity(item, "WI-0001").status == "groomed"


def test_blank_reason_is_no_reason(run, item):
    code, _, _ = run("set", "WI-0001", "status=idea", "--force", "--reason", "  ")
    assert code == EXIT_BAD_INPUT


def test_force_without_a_status_is_refused(run, item):
    code, _, err = run("set", "WI-0001", "title=Renamed", "--force", "--reason", "x")
    assert code == EXIT_BAD_INPUT and "status=" in err
    assert entity(item, "WI-0001").title == "Add sentinel config"


def test_a_reason_without_force_is_refused(run, item):
    code, _, err = run("set", "WI-0001", "status=ready", "--reason", "x")
    assert code == EXIT_BAD_INPUT and "Fix: szsdlc set WI-0001 status=ready" in err
    assert entity(item, "WI-0001").status == "groomed"


@pytest.mark.parametrize("target,missing", [
    ("planned", "design.md"),
    ("executing", "plan.md"),
])
def test_forcing_to_a_gated_state_still_needs_its_artifact(run, item, target, missing):
    code, _, err = run("set", "WI-0001", f"status={target}", "--force", "--reason", "x")
    assert code == EXIT_REFUSED and missing in err
    assert entity(item, "WI-0001").status == "groomed"
    assert journal(item, "WI-0001") == ""


def test_forcing_to_done_still_needs_every_task_ticked(run, item):
    home = entity(item, "WI-0001").home
    (home / "design.md").write_text("# Design\n", encoding="utf-8")
    (home / "plan.md").write_text("- [x] one\n- [ ] two\n", encoding="utf-8")
    code, _, err = run("set", "WI-0001", "status=done", "--force", "--reason", "x")
    assert code == EXIT_REFUSED and "1 unchecked" in err
    # The caller already chose where the work goes: the fix is the gate's
    # remedy, never "move somewhere else instead".
    assert "Fix: tick the remaining boxes in plan.md" in err


def test_an_unknown_target_and_the_current_status_are_still_refused(run, item):
    assert run("set", "WI-0001", "status=nope", "--force", "--reason", "x")[0] == EXIT_REFUSED
    assert run("set", "WI-0001", "status=groomed", "--force", "--reason", "x")[0] == EXIT_REFUSED


# ---------------------------------------------------------------------------
# forced_reason
# ---------------------------------------------------------------------------


def test_every_default_type_declares_forced_reason(project):
    for entity_type in project.entity_types.values():
        spec = entity_type.fields.get("forced_reason")
        assert spec is not None, entity_type.name
        assert spec.type == "text" and not spec.required


def test_forced_reason_holds_only_the_latest_forced_move(run, item):
    run("set", "WI-0001", "status=idea", "--force", "--reason", "first")
    run("set", "WI-0001", "status=groomed")  # ordinary: leaves it alone
    assert entity(item, "WI-0001").field("forced_reason") == f"{TODAY} groomed → idea: first"

    run("set", "WI-0001", "status=idea", "--force", "--reason", "second")
    assert entity(item, "WI-0001").field("forced_reason") == f"{TODAY} groomed → idea: second"
    assert journal(item, "WI-0001").count("forced") == 2


def test_a_type_without_a_journal_keeps_no_history(run, project):
    run("new", "decision", "--title", "Use TLS")
    code, output, _ = run("set", "ADR-0001", "status=rejected")
    assert code == 0
    code, output, _ = run("set", "ADR-0001", "status=proposed", "--force",
                          "--reason", "reconsidering")
    assert code == 0 and "(forced: reconsidering)" in output
    assert entity(project, "ADR-0001").field("forced_reason") == \
        f"{TODAY} rejected → proposed: reconsidering"


# ---------------------------------------------------------------------------
# reopening
# ---------------------------------------------------------------------------


def test_reopening_finished_work_reports_what_closing_had_done(run, item):
    run("new", "work_item", "--title", "Depends on it")
    run("link", "WI-0002", "depends_on", "WI-0001")
    finish(item, run)
    run("set", "EPIC-0001", "status=closed")

    code, output, _ = run("set", "WI-0001", "status=ready", "--force",
                          "--reason", "regressed")
    assert code == 0
    assert output == ("WI-0001: status done → ready (forced: regressed); "
                      "unscheduled — szsdlc schedule WI-0001 --horizon now; "
                      "blocks again: WI-0002; parent EPIC-0001 is closed")


def test_the_unschedule_fix_runs(run, item):
    finish(item, run)
    run("set", "WI-0001", "status=ready", "--force", "--reason", "regressed")
    code, _, err = run("schedule", "WI-0001", "--horizon", "now")
    assert code == 0, err


def test_reopening_dropped_work_keeps_the_drop_reason(run, project):
    """`dropped_reason` is kept so the earlier reason stays recognisable; it is
    overwritten only by dropping again. Ideas are the default type declaring it."""
    run("capture", "a thought")
    run("drop", "IDEA-0001", "--reason", "covered by WI-0009")
    code, output, _ = run("set", "IDEA-0001", "status=inbox", "--force",
                          "--reason", "not covered after all")
    assert code == 0 and output.startswith("IDEA-0001: status dropped → inbox")
    reopened = entity(project, "IDEA-0001")
    assert reopened.field("dropped_reason") == "covered by WI-0009"
    assert reopened.field("forced_reason") == \
        f"{TODAY} dropped → inbox: not covered after all"


def test_reopening_a_dropped_work_item(run, item):
    run("drop", "WI-0001", "--reason", "covered by WI-0009")
    code, output, _ = run("set", "WI-0001", "status=groomed", "--force",
                          "--reason", "not covered after all")
    assert code == 0 and output.startswith("WI-0001: status dropped → groomed")


def test_reopening_writes_no_validate_finding(run, item):
    finish(item, run)
    run("set", "WI-0001", "status=done")  # already done: refused, harmless
    run("set", "WI-0001", "status=review", "--force", "--reason", "regressed")
    code, output, _ = run("validate")
    assert "forced" not in output


# ---------------------------------------------------------------------------
# a workflow that shares no names with the defaults
# ---------------------------------------------------------------------------


LAB = textwrap.dedent(
    """
    entity_types:
      _replace: true
      specimen:
        prefix: SPC
        dir: specimens
        layout: directory
        actionable: true
        artifacts: [report.md, log.md]
        journal_artifact: log.md
        workflow:
          initial: received
          states:
            received: {to: [assayed, discarded]}
            assayed: {to: [archived, discarded]}
            archived: {terminal: true, requires_artifact: [report.md]}
            discarded: {terminal: true, abandoned: true}
      batch:
        prefix: BAT
        dir: batches
        layout: file
        can_parent: true
        workflow:
          initial: open
          states:
            open: {to: [sealed]}
            sealed: {terminal: true}
    relations:
      _replace: true
      parent:
        inverse: children
        source_types: [specimen]
        target_types: null
        cardinality: one
    roadmaps: {_replace: true}
    views: {_replace: true}
    """
)


@pytest.fixture
def lab(make_project, capsys):
    config = make_project(raw=LAB)

    def invoke(*argv: str):
        code = main(["-C", str(config.root), *argv])
        captured = capsys.readouterr()
        return code, captured.out.strip(), captured.err
    invoke("new", "specimen", "--title", "Sample A")
    return config, invoke


def test_force_works_on_a_workflow_with_unrelated_names(lab):
    config, run = lab
    run("set", "SPC-0001", "status=assayed")
    code, output, _ = run("set", "SPC-0001", "status=received", "--force",
                          "--reason", "contaminated assay")
    assert code == 0
    assert output == "SPC-0001: status assayed → received (forced: contaminated assay)"
    path = entity(config, "SPC-0001").artifact_path("log.md")
    assert path.read_text(encoding="utf-8") == \
        f"- {TODAY} forced assayed → received: contaminated assay\n"
    # It declares no forced_reason, so none is written.
    assert "forced_reason" not in entity(config, "SPC-0001").path.read_text(encoding="utf-8")


def test_gates_hold_on_a_workflow_with_unrelated_names(lab):
    config, run = lab
    code, _, err = run("set", "SPC-0001", "status=archived", "--force", "--reason", "x")
    assert code == EXIT_REFUSED and "report.md" in err

    (entity(config, "SPC-0001").home / "report.md").write_text("ok\n", encoding="utf-8")
    assert run("set", "SPC-0001", "status=archived", "--force", "--reason", "x")[0] == 0
    code, output, _ = run("set", "SPC-0001", "status=assayed", "--force",
                          "--reason", "re-run")
    assert code == 0 and output == "SPC-0001: status archived → assayed (forced: re-run)"


def test_reopening_an_abandoned_entry_with_unrelated_names(lab):
    config, run = lab
    run("drop", "SPC-0001", "--reason", "broken vial")
    code, output, _ = run("set", "SPC-0001", "status=received", "--force",
                          "--reason", "vial was fine")
    assert code == 0 and output.startswith("SPC-0001: status discarded → received")
