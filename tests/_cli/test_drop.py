"""szsdlc drop: closing an idea that went nowhere, with a reason."""

from __future__ import annotations

import pytest

from szsdlc.cli import main
from szsdlc.errors import EXIT_BAD_INPUT

from .conftest import entities


@pytest.fixture
def scheduled(make_project, capsys):
    """A ready work item placed on two roadmaps, and a CLI to drive it."""
    config = make_project({"roadmaps": {"release-1": {
        "title": "Release 1", "horizons": ["in", "out"],
        "requires_scheduling": {}}}})

    def invoke(*argv: str):
        code = main(["-C", str(config.root), *argv])
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    invoke("new", "work_item", "--title", "Doomed")
    for status in ("groomed", "ready"):
        invoke("set", "WI-0001", f"status={status}")
    assert invoke("schedule", "WI-0001", "--horizon", "now", "--roadmap", "roadmap")[0] == 0
    assert invoke("schedule", "WI-0001", "--horizon", "in", "--roadmap", "release-1")[0] == 0
    return invoke


# ---------------------------------------------------------------------------
# drop
# ---------------------------------------------------------------------------
def test_drop_records_the_reason_and_never_deletes(run, project):
    run("capture", "a duplicate thought")
    code, output, _ = run("drop", "IDEA-0001", "--reason", "already covered by WI-0001")
    assert code == 0
    assert output.strip() == "IDEA-0001: inbox → dropped"

    idea = entities(project).by_text("IDEA-0001")
    assert idea.status == "dropped"
    assert idea.field("dropped_reason") == "already covered by WI-0001"
    assert idea.path.exists()


def test_drop_reads_the_reason_from_stdin(run, project, stdin):
    run("capture", "a thought")
    stdin("a longer explanation\nover two lines\n")
    assert run("drop", "IDEA-0001")[0] == 0
    assert "over two lines" in entities(project).by_text("IDEA-0001").field("dropped_reason")


def test_dropping_without_a_reason_is_refused(run, no_stdin):
    run("capture", "a thought")
    code, _, err = run("drop", "IDEA-0001")
    assert code == EXIT_BAD_INPUT
    assert "needs a reason" in err
    assert 'Fix: szsdlc drop IDEA-0001 --reason "' in err


def test_dropping_twice_is_refused(run, no_stdin):
    run("capture", "a thought")
    run("drop", "IDEA-0001", "--reason", "no")
    code, _, err = run("drop", "IDEA-0001", "--reason", "still no")
    assert code == EXIT_BAD_INPUT
    assert "already dropped" in err


# ---------------------------------------------------------------------------
# drop changes status through the same path as set (WI-0053)
# ---------------------------------------------------------------------------


def test_dropping_a_scheduled_item_takes_it_off_every_roadmap(scheduled, no_stdin):
    code, output, err = scheduled("drop", "WI-0001", "--reason", "not needed after all")
    assert code == 0, err
    assert output == "WI-0001: ready → dropped; off roadmap; off release-1\n"
    # The validate error this used to leave behind, until a manual unschedule.
    code, output, _ = scheduled("validate")
    assert code == 0 and "still on roadmap" not in output


def test_dropping_an_unscheduled_item_reports_only_the_move(run, no_stdin):
    run("capture", "a thought")
    code, output, _ = run("drop", "IDEA-0001", "--reason", "no")
    assert code == 0 and output == "IDEA-0001: inbox → dropped\n"


def test_drop_follows_the_workflow_from_a_terminal_state(run, project, no_stdin):
    """A finished item has no edge to `dropped`. `drop` used to override the
    workflow there silently; that exception is `set --force` now."""
    run("capture", "a thought")
    run("refine", "IDEA-0001", "--into", "work_item", "--title", "W")
    code, _, err = run("drop", "IDEA-0001", "--reason", "changed my mind")
    assert code == 3, err
    assert "not a legal transition" in err
    assert entities(project).by_text("IDEA-0001").status == "refined"
