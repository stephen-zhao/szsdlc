"""szsdlc new: creating a typed entity directly."""

from __future__ import annotations

import datetime as dt
import pytest

from szsdlc.errors import EXIT_BAD_INPUT

from .conftest import entities


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
    """One of each shipped layout, created through the CLI.

    A work item earns a directory because it carries a design, a plan and a
    journal. A requirement carries nothing — its coverage is read off the
    graph — so it is one file. An idea starts as a section of the shared one.
    """
    run("new", "work_item", "--title", "Reachable over TLS")
    run("new", "requirement", "--title", "Reachable over TLS")
    run("capture", "a thought")

    store = entities(project)
    assert store.by_text("WI-0001").layout == "directory"
    assert store.by_text("REQ-0001").layout == "file"
    assert store.by_text("REQ-0001").path.name.startswith("REQ-0001")
    assert store.by_text("IDEA-0001").layout == "section"


def test_new_with_an_unknown_type_lists_the_known_ones(run):
    code, _, err = run("new", "ticket", "--title", "T")
    assert code == 5  # a type that does not exist is a config-level question
    assert "work_item" in err


def test_new_with_a_status_imports_finished_history(run, project):
    """WI-0033 — no walk through every transition, and no hand-written status."""
    code, output, _ = run("new", "requirement", "--title", "Reachable over TLS",
                          "--status", "approved")
    assert code == 0
    assert output.strip() == ("REQ-0001  requirements/REQ-0001-reachable-over-tls.md"
                              "  approved")
    assert entities(project).by_text("REQ-0001").status == "approved"


def test_new_with_a_gated_status_creates_and_names_what_is_owed(run, project):
    """Gates are not checked at creation — a new entity has no artifacts — but
    the line says what is missing, and validate holds it to it until written."""
    code, output, _ = run("new", "work_item", "--title", "Old work", "--status", "done")
    assert code == 0
    assert output.strip() == (
        "WI-0001  work-items/WI-0001-old-work/entity.md  done; owes: plan.md is missing")
    assert entities(project).by_text("WI-0001").status == "done"

    code, output, _ = run("validate")
    assert code == 4 and "WI-0001" in output and "is done but" in output

    plan = project.root / "work-items" / "WI-0001-old-work" / "plan.md"
    plan.write_text("- [x] **Step 1:** it was done\n", encoding="utf-8")
    code, output, _ = run("validate")
    assert "WI-0001" not in output


def test_new_with_an_unknown_status_lists_the_workflow(run, project):
    code, _, err = run("new", "decision", "--title", "Use TLS", "--status", "done")
    assert code == EXIT_BAD_INPUT
    assert "proposed, accepted, rejected, superseded" in err
    assert not list((project.root / "decisions").glob("ADR-*"))


def test_new_refuses_a_status_on_an_intake_type(run, project):
    code, _, err = run("new", "idea", "--title", "a thought", "--status", "refined")
    assert code == EXIT_BAD_INPUT
    assert "szsdlc capture --type idea -- 'a thought'" in err
    assert not entities(project).by_text("IDEA-0001")


@pytest.mark.parametrize("argv", [
    ("new", "idea", "--title=-starts with a dash", "--status", "refined"),
    ("new", "decision", "--title=-starts with a dash", "--status", "nope"),
])
def test_a_refusal_fix_runs_even_for_a_dash_prefixed_title(run, argv):
    import shlex

    _, _, err = run(*argv)
    fix = err.splitlines()[1][len("Fix: "):]
    code, _, err = run(*shlex.split(fix)[1:])
    assert code == 0, err
