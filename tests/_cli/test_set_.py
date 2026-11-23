"""szsdlc set: scalar fields, through the workflow."""

from __future__ import annotations

import datetime as dt
import pytest

from szsdlc.errors import EXIT_BAD_INPUT
from szsdlc.errors import EXIT_BAD_INPUT, EXIT_REFUSED

from .conftest import entities


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


def test_a_proposed_decision_can_be_rejected(run, project):
    """A proposal that loses is an outcome, not a gap in the workflow: the
    options and the reason this one lost are the reusable part."""
    run("new", "decision", "--title", "Sentinel not cluster")
    code, output, _ = run("set", "ADR-0001", "status=rejected")
    assert code == 0
    assert output.strip() == "ADR-0001: status proposed → rejected"


def test_a_rejected_decision_is_terminal(run, project):
    run("new", "decision", "--title", "Sentinel not cluster")
    run("set", "ADR-0001", "status=rejected")
    code, _, err = run("set", "ADR-0001", "status=accepted")
    assert code == EXIT_REFUSED
    assert "rejected is terminal" in err


def test_an_accepted_decision_is_superseded_rather_than_rejected(run, project):
    """Rejection is the proposal losing, so it is reachable only from
    `proposed`. Undoing a decision already taken is what `superseded` is for —
    the refusal has to say so rather than leaving the caller to guess."""
    run("new", "decision", "--title", "Sentinel not cluster")
    run("set", "ADR-0001", "status=accepted")
    code, _, err = run("set", "ADR-0001", "status=rejected")
    assert code == EXIT_REFUSED
    assert "accepted → rejected is not a legal transition" in err
    assert "Fix: szsdlc set ADR-0001 status=superseded" in err


def test_the_artifact_gate_names_the_file_to_write(run, seeded):
    for status in ("groomed", "ready", "designing"):
        run("set", "WI-0001", f"status={status}")
    code, _, err = run("set", "WI-0001", "status=designed")
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
    for status in ("groomed", "ready", "designing", "designed", "planning", "planned", "executing", "review"):
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
    for status in ("groomed", "ready", "designing", "designed", "planning", "planned", "executing", "review"):
        run("set", "WI-0001", f"status={status}")
    assert run("set", "WI-0001", "status=done")[0] == 0


def test_an_empty_artifact_does_not_satisfy_a_gate(run, seeded, project):
    entity = entities(project).by_text("WI-0001")
    (entity.home / "design.md").write_text("", encoding="utf-8")
    for status in ("groomed", "ready", "designing"):
        run("set", "WI-0001", f"status={status}")
    assert run("set", "WI-0001", "status=designed")[0] == EXIT_REFUSED


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
