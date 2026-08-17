"""Task 7 — capture, refine, inbox and drop.

The inbox must accept anything from a single bullet to a page of related
thoughts at a cost low enough that capturing is never a decision. That is the
acceptance criterion these tests actually enforce: one command, no prompts, no
required quoting of a multi-line body.
"""

from __future__ import annotations

import datetime as dt
import io
import json
from pathlib import Path

import pytest

from szsdlc.cli import main
from szsdlc.errors import EXIT_BAD_INPUT
from szsdlc.ids import IdSpace
from szsdlc.model import load_all


@pytest.fixture
def run(project, capsys):
    """Invoke the CLI against the fixture project and return (code, out, err)."""

    def invoke(*argv: str):
        code = main(["-C", str(project.root), *argv])
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    return invoke


@pytest.fixture
def stdin(monkeypatch):
    def feed(text: str):
        monkeypatch.setattr("sys.stdin", io.StringIO(text))
    return feed


@pytest.fixture
def no_stdin(monkeypatch):
    """A terminal: stdin must not be read, so $EDITOR is the fallback."""
    class Tty(io.StringIO):
        def isatty(self):
            return True
    monkeypatch.setattr("sys.stdin", Tty())


def entities(project):
    return load_all(project, IdSpace(project))


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


# ---------------------------------------------------------------------------
# refine
# ---------------------------------------------------------------------------


def test_refine_spawns_a_typed_entity_and_reports_both(run, project):
    run("capture", "valkey needs a sentinel quorum story")
    code, output, _ = run("refine", "IDEA-0001", "--into", "spike",
                          "--title", "Sentinel quorum under partition")
    assert code == 0
    # C1 — the resulting state, so no confirming `show` is needed.
    assert output.strip() == "SPK-0001  (IDEA-0001: inbox → refined)"

    spike = entities(project).by_text("SPK-0001")
    assert spike.title == "Sentinel quorum under partition"
    # Provenance is written on the child, the single authored side.
    assert spike.targets("refined_from") == ["IDEA-0001"]


def test_one_idea_yields_several_entities(run, project):
    """The normal case — "a set of related thoughts" requires it."""
    run("capture", "a page of related thoughts")
    run("refine", "IDEA-0001", "--into", "spike", "--title", "Investigate")
    run("refine", "IDEA-0001", "--into", "epic", "--title", "The outcome")
    code, _, _ = run("refine", "IDEA-0001", "--into", "work_item", "--title", "Do it")
    assert code == 0

    store = entities(project)
    spawned = {e.id.text: e.targets("refined_from") for e in store
               if e.targets("refined_from")}
    assert spawned == {"SPK-0001": ["IDEA-0001"], "EPIC-0001": ["IDEA-0001"],
                       "WI-0001": ["IDEA-0001"]}
    # Nothing was renamed and no tombstone was needed; the idea is provenance.
    assert store.by_text("IDEA-0001") is not None


def test_the_idea_is_never_written_to_by_the_child(run, project):
    run("capture", "a thought")
    idea_path = entities(project).by_text("IDEA-0001").path
    run("refine", "IDEA-0001", "--into", "spike", "--title", "S")
    after_first = idea_path.read_bytes()

    run("refine", "IDEA-0001", "--into", "work_item", "--title", "W")
    # The second spawn changes nothing about the idea: `refined_into` is
    # generated from the children's edges, never stored here.
    assert idea_path.read_bytes() == after_first
    assert b"refined_into" not in after_first


def test_the_title_defaults_to_the_ideas_derived_title(run, project):
    run("capture", "valkey needs a sentinel quorum story")
    run("refine", "IDEA-0001", "--into", "work_item")
    assert entities(project).by_text("WI-0001").title == \
        "valkey needs a sentinel quorum story"


def test_refining_a_dropped_idea_is_refused(run):
    run("capture", "a thought")
    run("drop", "IDEA-0001", "--reason", "already covered")
    code, _, err = run("refine", "IDEA-0001", "--into", "work_item")
    assert code == EXIT_BAD_INPUT
    assert "dropped idea is not refined further" in err
    assert "Fix: szsdlc set IDEA-0001 status=inbox" in err


def test_refining_a_typed_entity_is_refused(run):
    run("capture", "a thought")
    run("refine", "IDEA-0001", "--into", "work_item", "--title", "W")
    code, _, err = run("refine", "WI-0001", "--into", "spike")
    assert code == EXIT_BAD_INPUT
    assert "not an intake entity" in err


def test_refining_into_an_intake_type_is_refused(run):
    run("capture", "a thought")
    code, _, err = run("refine", "IDEA-0001", "--into", "idea")
    assert code == EXIT_BAD_INPUT
    assert "only make another idea" in err


def test_an_unknown_reference_suggests_the_nearest(run):
    run("capture", "a thought")
    code, _, err = run("refine", "IDEA-0010", "--into", "work_item")
    assert code == EXIT_BAD_INPUT
    assert "did you mean IDEA-0001" in err


# ---------------------------------------------------------------------------
# inbox
# ---------------------------------------------------------------------------


def test_inbox_lists_only_unrefined_ideas(run, project):
    run("capture", "first thought")
    run("capture", "second thought")
    run("capture", "third thought")
    run("refine", "IDEA-0002", "--into", "work_item", "--title", "W")
    run("drop", "IDEA-0003", "--reason", "duplicate")

    code, output, _ = run("inbox")
    assert code == 0
    assert "IDEA-0001" in output
    assert "IDEA-0002" not in output
    assert "IDEA-0003" not in output


def test_inbox_shows_id_age_and_first_line(run, project):
    run("capture", "# valkey needs a sentinel quorum story\n\nmore detail\n")
    _, output, _ = run("inbox")
    row = output.strip()
    assert row.startswith("IDEA-0001")
    assert "0d" in row
    assert row.endswith("valkey needs a sentinel quorum story")
    assert len(output.strip().splitlines()) == 1


def test_inbox_is_bounded_and_says_so(run):
    for _ in range(25):
        run("capture", "thought")
    _, output, _ = run("inbox")
    lines = output.strip().splitlines()
    # C6 — bounded by default, and the truncation is visible.
    assert len(lines) == 21
    assert lines[-1] == "showing 20 of 25 — rerun with --limit 0"


def test_inbox_json_is_opt_in_and_unbounded(run):
    for _ in range(25):
        run("capture", "thought")
    _, output, _ = run("inbox", "--json")
    payload = json.loads(output)
    assert len(payload) == 25
    assert set(payload[0]) == {"id", "status", "age_days", "title"}


def test_an_empty_inbox_prints_nothing(run):
    code, output, err = run("inbox")
    assert code == 0 and output == "" and err == ""


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
# The surface itself
# ---------------------------------------------------------------------------


def test_help_is_compact(capsys):
    """The audit budgets --help at 25 lines, and this is the block a mistyped
    command would otherwise print."""
    assert main(["--help"]) == 0
    output = capsys.readouterr().out
    assert len(output.splitlines()) <= 25
    assert "capture" in output and "refine" in output
    # Every command reachable from the parser has a line here, except the
    # hook entry point, which is invoked by hooks.json and never typed.
    from szsdlc.cli import COMMANDS, HIDDEN_COMMANDS, build_parser

    documented = {verb for invocation, _ in COMMANDS
                  for verb in invocation.split()[0].split("|")}
    registered = set(build_parser()._subparsers._group_actions[0].choices)
    assert registered - HIDDEN_COMMANDS <= documented


def test_no_arguments_prints_help_to_stderr_not_stdout(capsys):
    assert main([]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "usage:" in captured.err


def test_an_argparse_error_never_dumps_usage(capsys):
    assert main(["refine", "IDEA-0001"]) == EXIT_BAD_INPUT
    err = capsys.readouterr().err
    assert len(err.strip().splitlines()) <= 3
    assert "Fix: szsdlc refine --help" in err


def test_running_outside_a_project_names_init(tmp_path, capsys):
    from szsdlc.errors import EXIT_CONFIG

    assert main(["-C", str(tmp_path), "inbox"]) == EXIT_CONFIG
    assert "Fix: szsdlc init" in capsys.readouterr().err


def test_no_traceback_ever_reaches_stderr(run, monkeypatch, capsys):
    def explode(*_args, **_kwargs):
        raise RuntimeError("something internal went wrong")

    monkeypatch.setattr("szsdlc.cli.Session", explode)
    code, _, err = run("inbox")
    assert code == 1
    assert "Traceback" not in err
    assert "Fix: " in err
