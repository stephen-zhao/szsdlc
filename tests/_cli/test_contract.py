"""Task 9a — the command surface contract, enforced mechanically.

Every command here is consumed by an agent, so both halves cost tokens: a
hard-to-produce input causes retry loops, and a verbose output pollutes context
for the rest of the session. The audit table in REQ-0012 (szsdlc-szsdlc) *is* the
specification, and this module is what makes it one rather than an aspiration.

The two-shot property is the sharp end. For every error class: stderr is at
most three lines, it contains a `Fix:`, and that fix line is either a complete
command that **actually succeeds against the same fixture**, or a template
whose placeholders the caller must fill — in which case it must still name a
real command. A fix hint that does not work fails CI.
"""

from __future__ import annotations

import io
import json
import re
import shlex
import sys

import pytest
import yaml

from szsdlc import config as C
from szsdlc.cli import COMMANDS, HIDDEN_COMMANDS, build_parser, main
from szsdlc.errors import (
    EXIT_BAD_INPUT,
    EXIT_CONFIG,
    EXIT_REFUSED,
)
from szsdlc.ids import IdSpace
from szsdlc.model import create_entity

PLACEHOLDER = re.compile(r"<[^>]+>")


def registered_commands() -> set[str]:
    """Commands a person can type.

    Hidden commands are excluded deliberately: `hook` is reachable from the
    parser but is invoked by hooks.json, so it is neither documented in
    `--help` nor ever legitimate to name in a `Fix:` line.
    """
    choices = set(build_parser()._subparsers._group_actions[0].choices)
    return choices - HIDDEN_COMMANDS


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def build_project(root, *, entities: int = 0):
    """A project every contract case can run against, sized on demand."""
    cfg_dir = root / C.CONFIG_DIRNAME
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / C.CONFIG_FILENAME).write_text("project:\n  name: contract\n",
                                             encoding="utf-8")
    config = C.load(root)
    for entity_type in config.entity_types.values():
        config.dir_for(entity_type).mkdir(parents=True, exist_ok=True)

    ids = IdSpace(config)
    epic = create_entity(config, ids, config.type_for("epic"), title="An epic")
    create_entity(config, ids, config.type_for("requirement"), title="A requirement")
    create_entity(config, ids, config.type_for("idea"), body="a captured thought\n")
    create_entity(config, ids, config.type_for("work_item"), title="Work item one",
                  relations={"parent": epic.id.text})
    create_entity(config, ids, config.type_for("work_item"), title="Work item two",
                  status="ready", relations={"parent": epic.id.text})

    for number in range(entities):
        create_entity(config, ids, config.type_for("work_item"),
                      title=f"Bulk work item {number}", status="ready",
                      tags=["bulk"], relations={"parent": epic.id.text})
    return config


@pytest.fixture
def contract(tmp_path):
    return build_project(tmp_path)


@pytest.fixture
def invoke(capsys):
    def call(root, *argv: str):
        code = main(["-C", str(root), *argv])
        captured = capsys.readouterr()
        return code, captured.out, captured.err
    return call


@pytest.fixture(autouse=True)
def never_a_tty(monkeypatch):
    """Reading stdin must never hang a contract case."""
    monkeypatch.setattr("sys.stdin", io.StringIO(""))


# ---------------------------------------------------------------------------
# C2 / C7 / Step 2a — two-shot recovery
# ---------------------------------------------------------------------------


def gate_ready(config, invoke):
    """Drive WI-0002 to `review` with one box unticked — the tasks gate."""
    root = config.root
    entity_dir = next(config.dir_for("work_item").glob("WI-0002-*"))
    (entity_dir / "design.md").write_text("a design\n", encoding="utf-8")
    (entity_dir / "plan.md").write_text("- [x] one\n- [ ] two\n", encoding="utf-8")
    for status in ("designing", "designed", "planning", "planned", "executing", "review"):
        invoke(root, "set", "WI-0002", f"status={status}")


def gate_designing(config, invoke):
    invoke(config.root, "set", "WI-0002", "status=designing")


def schedule_it(config, invoke):
    invoke(config.root, "schedule", "WI-0002", "--horizon", "now")


#: (name, setup, failing argv, expected exit code)
ERROR_CASES = [
    ("unknown-ref-near", None, ("show", "WI-0020"), EXIT_BAD_INPUT),
    ("unknown-ref-far", None, ("show", "WI-9999"), EXIT_BAD_INPUT),
    ("unknown-prefix", None, ("show", "TASK-0001"), EXIT_BAD_INPUT),
    ("malformed-ref", None, ("show", "not an id"), EXIT_BAD_INPUT),
    ("illegal-transition", None, ("set", "WI-0001", "status=done"), EXIT_REFUSED),
    ("unknown-status", None, ("set", "WI-0001", "status=marinating"), EXIT_REFUSED),
    ("already-in-status", None, ("set", "WI-0001", "status=idea"), EXIT_REFUSED),
    ("gate-artifact", gate_designing, ("set", "WI-0002", "status=designed"), EXIT_REFUSED),
    ("gate-tasks", gate_ready, ("set", "WI-0002", "status=done"), EXIT_REFUSED),
    ("set-relations", None, ("set", "WI-0001", "relations=x"), EXIT_BAD_INPUT),
    ("set-tags", None, ("set", "WI-0001", "tags=x"), EXIT_BAD_INPUT),
    ("set-id", None, ("set", "WI-0001", "id=x"), EXIT_BAD_INPUT),
    ("set-unknown-field", None, ("set", "WI-0001", "stauts=groomed"), EXIT_BAD_INPUT),
    ("set-malformed", None, ("set", "WI-0001", "status"), EXIT_BAD_INPUT),
    ("set-bad-date", None, ("set", "WI-0001", "opened=yesterday"), EXIT_BAD_INPUT),
    ("link-bad-target-type", None, ("link", "WI-0001", "parent", "WI-0002"),
     EXIT_BAD_INPUT),
    ("link-inverse", None, ("link", "EPIC-0001", "children", "WI-0001"), EXIT_BAD_INPUT),
    ("link-unknown-relation", None, ("link", "WI-0001", "implement", "REQ-0001"),
     EXIT_BAD_INPUT),
    ("link-duplicate", None, ("link", "WI-0001", "parent", "EPIC-0001"), EXIT_BAD_INPUT),
    ("link-self", None, ("link", "EPIC-0001", "parent", "EPIC-0001"), EXIT_BAD_INPUT),
    ("unlink-absent", None, ("unlink", "WI-0001", "implements", "REQ-0001"),
     EXIT_BAD_INPUT),
    ("schedule-not-schedulable", None, ("schedule", "REQ-0001", "--horizon", "now"),
     EXIT_REFUSED),
    ("schedule-not-ready", None, ("schedule", "WI-0001", "--horizon", "now"),
     EXIT_REFUSED),
    ("schedule-bad-horizon", None, ("schedule", "WI-0002", "--horizon", "someday"),
     EXIT_BAD_INPUT),
    ("schedule-absent-anchor", None,
     ("schedule", "WI-0002", "--horizon", "now", "--after", "EPIC-0001"),
     EXIT_BAD_INPUT),
    ("unschedule-absent", None, ("unschedule", "WI-0002"), EXIT_BAD_INPUT),
    ("convert-same-type", None, ("convert", "WI-0001", "work_item"), EXIT_BAD_INPUT),
    ("drop-no-reason", None, ("drop", "IDEA-0001"), EXIT_BAD_INPUT),
    ("log-no-journal", None, ("log", "REQ-0001", "x"), EXIT_BAD_INPUT),
    ("capture-empty", None, ("capture",), EXIT_BAD_INPUT),
    ("refine-typed", None, ("refine", "WI-0001", "--into", "spike"), EXIT_BAD_INPUT),
    ("refine-into-intake", None, ("refine", "IDEA-0001", "--into", "idea"),
     EXIT_BAD_INPUT),
    ("standards-no-paths", None, ("standards", "match"), EXIT_BAD_INPUT),
    ("unknown-type", None, ("new", "ticket", "--title", "T"), EXIT_CONFIG),
    ("new-unknown-status", None,
     ("new", "decision", "--title", "Use TLS", "--status", "nope"), EXIT_BAD_INPUT),
    ("new-intake-status", None,
     ("new", "idea", "--title", "a thought", "--status", "refined"), EXIT_BAD_INPUT),
    ("set-force-no-reason", None, ("set", "WI-0002", "status=groomed", "--force"),
     EXIT_BAD_INPUT),
    ("set-force-no-status", None,
     ("set", "WI-0002", "title=T", "--force", "--reason", "x"), EXIT_BAD_INPUT),
    ("set-reason-no-force", None,
     ("set", "WI-0002", "status=designing", "--reason", "x"), EXIT_BAD_INPUT),
    ("argparse", None, ("refine", "IDEA-0001"), EXIT_BAD_INPUT),
    ("init-twice", None, ("init",), EXIT_BAD_INPUT),
]


@pytest.mark.parametrize("name,setup,argv,expected",
                         ERROR_CASES, ids=[c[0] for c in ERROR_CASES])
def test_every_refusal_is_bounded_and_names_a_fix(tmp_path, invoke, name, setup,
                                                  argv, expected):
    """C2/C7 — at most three lines, a Fix line, no traceback, right exit code."""
    config = build_project(tmp_path)
    if setup:
        setup(config, invoke)

    code, output, err = invoke(config.root, *argv)

    assert code == expected, err
    assert output == "", "a refusal must write nothing to stdout"
    lines = err.strip().splitlines()
    assert 1 <= len(lines) <= 3, err
    assert lines[1].startswith("Fix: "), err
    assert "Traceback" not in err
    assert "usage:" not in err, "an argparse usage dump is how one typo costs 1000 tokens"


@pytest.mark.parametrize("name,setup,argv,expected",
                         ERROR_CASES, ids=[c[0] for c in ERROR_CASES])
def test_the_suggested_fix_actually_works(tmp_path, invoke, name, setup, argv, expected):
    """Step 2a — the second call *is* the suggested fix.

    A fix with no placeholders must run and succeed against the same fixture.
    A fix containing `<...>` is a form the caller must fill in — those are held
    to naming a real command instead, since we cannot invent the value.
    """
    config = build_project(tmp_path)
    if setup:
        setup(config, invoke)

    _, _, err = invoke(config.root, *argv)
    fix = err.strip().splitlines()[1][len("Fix: "):].strip()

    if not fix.startswith("szsdlc "):
        # A remedy rather than a command — writing a file, editing a config
        # key. It must still be specific enough to act on without re-reading
        # docs, which means naming the actual path or key.
        assert any(marker in fix for marker in (".yml", ".md", "entity_types.")), fix
        return

    # shlex, not split(): a fix that quotes an argument is still one argument.
    words = shlex.split(fix)
    assert words[1] in registered_commands(), fix

    if PLACEHOLDER.search(fix):
        # A form the caller fills in. We cannot invent the value, so the
        # obligation is only that it names a real command.
        return

    code = main(["-C", str(config.root), *words[1:]])
    assert code == 0, f"suggested fix failed: {fix}"


def test_an_internal_exception_is_converted_not_raised(tmp_path, invoke, monkeypatch):
    config = build_project(tmp_path)
    monkeypatch.setattr("szsdlc._cli._session.Session",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    code, output, err = invoke(config.root, "list")
    assert code == 1
    assert output == ""
    assert "Traceback" not in err and "Fix: " in err


def test_a_model_invariant_breach_is_a_bug_not_a_finding(tmp_path, invoke, monkeypatch):
    """The two must never be conflated in output."""
    from szsdlc.errors import InternalError

    config = build_project(tmp_path)
    monkeypatch.setattr("szsdlc._cli._session.Session",
                        lambda *a, **k: (_ for _ in ()).throw(InternalError("bad state")))
    code, _, err = invoke(config.root, "list")
    assert code == 1
    assert err.startswith("internal error:")


# ---------------------------------------------------------------------------
# C1 — mutations report the resulting state
# ---------------------------------------------------------------------------


MUTATIONS = [
    (("capture", "a thought"), 1),
    (("new", "spike", "--title", "Investigate"), 1),
    (("new", "work_item", "--title", "Imported", "--status", "done"), 1),
    (("set", "WI-0001", "status=groomed"), 1),
    (("set", "WI-0002", "status=groomed", "--force", "--reason", "not next"), 1),
    (("tag", "WI-0001", "dns"), 1),
    (("untag", "WI-0001", "dns"), 1),
    (("link", "WI-0001", "implements", "REQ-0001"), 1),
    (("unlink", "WI-0001", "parent", "EPIC-0001"), 1),
    (("schedule", "WI-0002", "--horizon", "now"), 1),
    (("unschedule", "WI-0002"), 1),
    (("convert", "WI-0001", "spike"), 2),
    (("refine", "IDEA-0001", "--into", "work_item", "--title", "W"), 1),
    (("drop", "IDEA-0001", "--reason", "covered elsewhere"), 1),
    (("log", "WI-0001", "a note"), 0),
]


@pytest.mark.parametrize("argv,budget", MUTATIONS, ids=[a[0] for a, _ in MUTATIONS])
def test_a_mutation_reports_its_result_within_budget(tmp_path, invoke, argv, budget):
    """C1 — the post-state, so no confirming `show` is ever needed."""
    config = build_project(tmp_path)
    if argv[0] == "unschedule":
        invoke(config.root, "schedule", "WI-0002", "--horizon", "now")

    code, output, err = invoke(config.root, *argv)
    assert code == 0, err
    lines = output.strip().splitlines() if output.strip() else []
    assert len(lines) == budget, output
    if budget:
        # Every reported line names an entity, so it reads as a result rather
        # than an acknowledgement — that is what removes the confirming `show`.
        assert re.search(r"[A-Z]+-\d{4}", output), output


# ---------------------------------------------------------------------------
# Step 7 — golden output budgets, from the audit table
# ---------------------------------------------------------------------------


#: One line per command, plus the header and the footer. Raised from 25 to 28
#: when `move` landed, because 25 was exactly what the help already used and a
#: ceiling with nothing under it cannot be tested against. Raised to 30 when
#: `sync` was split into `render`, `commit` and `sync`: three operations that
#: differ in whether they touch the network cannot share one line, and two
#: lines of help are cheaper than an agent guessing which one reaches a remote.
#: Back to 29 when `commit` was withdrawn. Raised to 30 for `refresh` (WI-0047):
#: the one operation that takes the latest without sending anything is the
#: opposite of `sync` in the way that matters, so it cannot share its line.
HELP_BUDGET = 30

#: (argv, max lines). The audit table in REQ-0012 (szsdlc-szsdlc) is the specification;
#: a command that outgrows its budget fails here.
BUDGETS = [
    (("inbox",), 21),
    (("next",), 11),
    (("list",), 21),
    (("list", "--type", "work_item"), 21),
    (("trace", "EPIC-0001"), 21),
    (("show", "WI-0001"), 40),
    (("show", "WI-0001", "--context"), 20),
    (("context",), 12),
    (("standards", "match", "docs/plan.md"), 10),
    (("--help",), HELP_BUDGET),
]


@pytest.mark.parametrize("size", [20, 200], ids=["small", "large"])
@pytest.mark.parametrize("argv,budget", BUDGETS, ids=[" ".join(a) for a, _ in BUDGETS])
def test_output_stays_within_its_budget_at_both_sizes(tmp_path, invoke, size,
                                                      argv, budget):
    config = build_project(tmp_path, entities=size)
    code, output, err = invoke(config.root, *argv)
    assert code == 0, err
    assert len(output.splitlines()) <= budget, output


@pytest.mark.parametrize("size", [20, 200], ids=["small", "large"])
def test_context_is_capped_in_characters_not_just_lines(tmp_path, invoke, size):
    """A session's first tokens must cost the same whatever the project size."""
    config = build_project(tmp_path, entities=size)
    _, output, _ = invoke(config.root, "context")
    assert len(output) <= 1800


@pytest.mark.parametrize("argv", [("list",), ("next",), ("inbox",), ("trace", "EPIC-0001")],
                         ids=["list", "next", "inbox", "trace"])
def test_truncation_is_always_visible(tmp_path, invoke, argv):
    """A silently capped list reads as "nothing more to see"."""
    config = build_project(tmp_path, entities=200)
    if argv[0] == "inbox":
        for _ in range(25):
            invoke(config.root, "capture", "a thought")

    _, output, _ = invoke(config.root, *argv)
    lines = output.strip().splitlines()
    assert lines[-1].startswith("showing ")
    assert "rerun with --limit 0" in lines[-1]


@pytest.mark.parametrize("argv", [("list",), ("next",), ("inbox",)],
                         ids=["list", "next", "inbox"])
def test_json_is_exempt_from_the_limit(tmp_path, invoke, argv):
    import json

    config = build_project(tmp_path, entities=200)
    _, output, _ = invoke(config.root, *argv, "--json")
    json.loads(output)  # and it is valid, single-line JSON
    assert len(output.splitlines()) == 1


# ---------------------------------------------------------------------------
# C5 — human output is the default
# ---------------------------------------------------------------------------


def test_json_is_never_what_a_caller_falls_into(tmp_path, invoke):
    config = build_project(tmp_path, entities=5)
    _, human, _ = invoke(config.root, "list")
    _, machine, _ = invoke(config.root, "list", "--json")
    assert not human.startswith("[")
    assert len(machine) > len(human), "JSON is the more verbose encoding"


# ---------------------------------------------------------------------------
# C6 — --help documents everything reachable
# ---------------------------------------------------------------------------


def test_every_registered_command_is_documented(capsys):
    main(["--help"])
    output = capsys.readouterr().out
    for command in registered_commands():
        assert re.search(rf"(?m)^\s+{re.escape(command)}\b|\|{re.escape(command)}\b",
                         output), command


def test_help_fits_the_budget_with_room_to_grow():
    """A budget with no headroom is a budget that breaks on the next command.

    This asserted `<= 25` while the help was exactly 25 lines long, so it could
    not detect the condition its own name describes — the completion audit
    found that and left it as a decision. Adding `move` forced the decision:
    the budget rose to 28, and the headroom is now asserted separately, so the
    next command added is a deliberate raise of this number rather than a
    silent slide into the wall. Splitting `sync` into `render`, `commit` and
    `sync` was the next such raise, to 30 — and withdrawing `commit` from the
    language gave that line straight back, to 29. A ratchet that only turns one
    way is not a ratchet, it is a slide with a slower gradient.
    """
    from szsdlc.cli import compact_help

    used = len(compact_help().splitlines())
    assert used <= HELP_BUDGET, f"{used} lines"
    assert HELP_BUDGET - used >= 1, "raise HELP_BUDGET deliberately, in this commit"


def test_the_audit_table_and_the_parser_agree():
    documented = set()
    for invocation, _ in COMMANDS:
        documented |= set(invocation.split()[0].split("|"))
    assert registered_commands() == documented


# ---------------------------------------------------------------------------
# C3 — one job per command
# ---------------------------------------------------------------------------


def test_set_never_edits_a_list_valued_field(tmp_path, invoke):
    config = build_project(tmp_path)
    cfg = C.config_path_for(config.root)
    cfg.write_text(yaml.safe_dump({
        "entity_types": {"work_item": {"fields": {"reviewers": {"type": "array"}}}}
    }), encoding="utf-8")

    code, _, err = invoke(config.root, "set", "WI-0001", "reviewers=a,b")
    assert code == EXIT_BAD_INPUT
    assert "list-valued" in err


# ---------------------------------------------------------------------------
# C6 — a bounded listing is bounded in both directions
# ---------------------------------------------------------------------------


PARAGRAPH = (
    "People keep pasting the same three-line disclaimer into every export. It "
    "should be a template we own, so legal can change it once instead of "
    "chasing twelve teams, which is what happens today and is why the wording "
    "in the PDF writer has been wrong since March."
)


def test_no_listing_row_is_unbounded(tmp_path, invoke):
    """Row *count* was capped from the start; row *width* was not.

    Capture costs one command precisely so a paragraph can be captured
    without ceremony, and a captured paragraph becomes the entity's derived
    title. Unclipped, that single entity costs more context than the twenty
    rows around it and turns every markdown table it appears in into one
    unreadable line — the same failure C6 forbids, along the other axis.
    """
    config = build_project(tmp_path)
    invoke(config.root, "capture", PARAGRAPH)

    for argv in (["inbox"], ["list"], ["next"], ["context"]):
        _, output, _ = invoke(config.root, *argv)
        for line in output.splitlines():
            assert len(line) <= 100, f"{argv[0]}: {len(line)} chars"


def test_json_output_is_never_clipped(tmp_path, invoke):
    """`--json` has a consumer that wants the value, not a rendering of it."""
    config = build_project(tmp_path)
    invoke(config.root, "capture", PARAGRAPH)
    _, output, _ = invoke(config.root, "inbox", "--json")
    assert PARAGRAPH.split(". ")[0] in json.dumps(json.loads(output))


# ---------------------------------------------------------------------------
# C1 — a mutation that succeeded must never report failure
# ---------------------------------------------------------------------------


def test_output_survives_a_non_utf8_console(tmp_path, monkeypatch):
    """A Windows console is cp1252, and transitions are reported with an arrow.

    Writing happens before printing, so an unencodable character does not lose
    the edit -- it loses the *report* of the edit, exits non-zero, and tells a
    caller their mutation failed when it is already on disk. That inverts C1,
    so the streams are made encodable before any command runs.
    """
    config = build_project(tmp_path)
    stdout, stderr = io.BytesIO(), io.BytesIO()
    monkeypatch.setattr(
        "sys.stdout", io.TextIOWrapper(stdout, encoding="cp1252", newline=""))
    monkeypatch.setattr(
        "sys.stderr", io.TextIOWrapper(stderr, encoding="cp1252", newline=""))

    code = main(["-C", str(config.root), "set", "WI-0002", "status=designing"])
    sys.stdout.flush()
    sys.stderr.flush()

    assert code == 0, stderr.getvalue().decode("utf-8", "replace")
    assert "→" in stdout.getvalue().decode("utf-8")


# ---------------------------------------------------------------------------
# The surface itself
# ---------------------------------------------------------------------------
def test_help_is_compact(capsys):
    """The audit budgets --help, and this is the block a mistyped command
    would otherwise print. The number itself lives in test_contract.py, next
    to the rest of the audit table it comes from."""

    assert main(["--help"]) == 0
    output = capsys.readouterr().out
    assert len(output.splitlines()) <= HELP_BUDGET
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

    monkeypatch.setattr("szsdlc._cli._session.Session", explode)
    code, _, err = run("inbox")
    assert code == 1
    assert "Traceback" not in err
    assert "Fix: " in err
