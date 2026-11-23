"""szsdlc capture: one command, no prompts, no required quoting of a multi-line body."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from szsdlc.errors import EXIT_BAD_INPUT

from .conftest import entities


# ---------------------------------------------------------------------------
# capture
# ---------------------------------------------------------------------------
def test_capture_from_an_argument_takes_one_command(run, project):
    code, output, err = run("capture", "valkey needs a sentinel quorum story")
    assert code == 0
    assert output == "IDEA-0001\n"
    assert err == ""

    idea = entities(project).by_text("IDEA-0001")
    assert idea.status == "inbox"
    assert idea.title == "valkey needs a sentinel quorum story"
    # Nothing was asserted about type at capture time.
    assert idea.type.intake


def test_capture_from_stdin_accepts_a_page_of_thoughts(run, project, stdin):
    stdin("Several related thoughts\n\n- one\n- two\n\nAnd a closing line.\n")
    code, output, _ = run("capture")
    assert code == 0 and output == "IDEA-0001\n"

    idea = entities(project).by_text("IDEA-0001")
    assert "- two" in idea.body
    assert idea.title == "Several related thoughts"


def test_capture_from_the_editor(run, project, monkeypatch, no_stdin):
    """The third input mode. Still one command, still no prompt."""
    monkeypatch.setenv("SZSDLC_EDITOR", "stand-in-editor")

    def fake_editor(cmd, check=False):
        assert cmd[0] == "stand-in-editor"
        Path(cmd[-1]).write_text("from the editor\n", encoding="utf-8")

    monkeypatch.setattr("subprocess.run", fake_editor)
    code, output, _ = run("capture")
    assert code == 0 and output == "IDEA-0001\n"
    assert entities(project).by_text("IDEA-0001").title == "from the editor"


def test_capture_stores_the_minimum_frontmatter(run, project):
    run("capture", "a thought")
    text = entities(project).by_text("IDEA-0001").path.read_text(encoding="utf-8")
    assert "id: IDEA-0001" in text
    assert "status: inbox" in text
    assert f"captured: {dt.date.today().isoformat()}" in text
    # No title is stored: naming a thought is a cost capture must not impose.
    assert "title:" not in text


def test_capture_allocates_sequentially(run, project):
    for expected in ("IDEA-0001", "IDEA-0002", "IDEA-0003"):
        _, output, _ = run("capture", "thought")
        assert output.strip() == expected


def test_empty_input_is_refused_with_a_runnable_fix(run, stdin):
    stdin("   \n")
    code, output, err = run("capture")
    assert code == EXIT_BAD_INPUT
    assert output == ""
    assert len(err.strip().splitlines()) <= 3
    assert "Fix: szsdlc capture" in err


def test_capture_needs_no_editor_when_stdin_has_content(run, monkeypatch, stdin):
    monkeypatch.delenv("EDITOR", raising=False)
    stdin("a thought\n")
    assert run("capture")[0] == 0


def test_no_editor_and_no_input_is_refused_not_hung(run, monkeypatch, no_stdin):
    monkeypatch.delenv("EDITOR", raising=False)
    monkeypatch.delenv("SZSDLC_EDITOR", raising=False)
    code, _, err = run("capture")
    assert code == EXIT_BAD_INPUT
    assert "$EDITOR is unset" in err


def test_capture_into_a_non_intake_type_is_refused(run):
    code, _, err = run("capture", "x", "--type", "work_item")
    assert code == EXIT_BAD_INPUT
    assert "not an intake type" in err
