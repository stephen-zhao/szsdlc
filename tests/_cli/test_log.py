"""szsdlc log: dated lines in the journal artifact."""

from __future__ import annotations

import datetime as dt
import io

from szsdlc.errors import EXIT_BAD_INPUT

from .conftest import entities
import pytest
from szsdlc.cli import main
from szsdlc.ids import IdSpace
from szsdlc.model import create_entity, load_all
from szsdlc.model import load_all


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


def capture(project, body: str = "a loose thought"):
    return create_entity(project, IdSpace(project), project.type_for("idea"),
                         body=body + "\n")


def reload(project, id_text: str):
    return load_all(project).by_text(id_text)


@pytest.fixture
def journalled(make_project):
    """A dynamic type that keeps a journal — the shipped `idea` does not."""
    return make_project({"entity_types": {"idea": {
        "artifacts": ["journal.md"], "journal_artifact": "journal.md"}}})


# ---------------------------------------------------------------------------
# Wanting an artifact is how an entry earns a directory
# ---------------------------------------------------------------------------


def test_logging_promotes_an_entry_rather_than_refusing(journalled, capsys):
    entity = capture(journalled, "drafts are lost on refresh")
    assert entity.layout == "section"

    assert main(["-C", str(journalled.root), "log", "IDEA-0001", "still happening"]) == 0

    promoted = reload(journalled, "IDEA-0001")
    assert promoted.layout == "directory"
    assert "still happening" in promoted.read_artifact("journal.md")
    assert "IDEA-0001" not in journalled.section_path("idea").read_text(encoding="utf-8")
