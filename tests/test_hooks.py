"""Tasks 12 and 13 — the plugin manifest, the launchers, and the hooks.

The load-bearing property is the boring one: **every hook is a silent no-op in
a project that has no `.szsdlc/config.yml`**, so the plugin is harmless
installed globally. A hook that errors in a repo which has never heard of
szsdlc is a hook people uninstall.

The division of labour is the other: `sync` runs constantly and tolerantly
during a turn; `validate` runs once at the end and strictly.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from szsdlc import hooks
from szsdlc.cli import main

REPO = Path(__file__).resolve().parents[1]


def payload(**tool_input) -> io.StringIO:
    return io.StringIO(json.dumps({"tool_input": tool_input}))


@pytest.fixture
def seeded(project, capsys):
    def run(*argv):
        main(["-C", str(project.root), *argv])
        capsys.readouterr()

    run("new", "epic", "--title", "Sentinel quorum")
    run("new", "work_item", "--title", "Add config")
    run("link", "WI-0001", "parent", "EPIC-0001")
    # An epic is schedulable from `open`, so leaving it off the roadmap is an
    # error — this fixture has to actually validate clean to be a baseline.
    run("schedule", "EPIC-0001", "--horizon", "next")
    run("sync")
    return project


# ---------------------------------------------------------------------------
# The manifest and hook wiring
# ---------------------------------------------------------------------------


def test_the_plugin_manifest_is_valid_and_points_at_the_hooks():
    manifest = json.loads((REPO / ".claude-plugin" / "plugin.json").read_text("utf-8"))
    assert manifest["name"] == "szsdlc"
    assert manifest["hooks"] == "./hooks/hooks.json"
    assert (REPO / "hooks" / "hooks.json").is_file()


def test_the_manifest_version_tracks_the_package():
    import szsdlc

    manifest = json.loads((REPO / ".claude-plugin" / "plugin.json").read_text("utf-8"))
    assert manifest["version"] == szsdlc.__version__


def wiring() -> dict:
    return json.loads((REPO / "hooks" / "hooks.json").read_text("utf-8"))


def test_the_events_sit_under_a_hooks_key():
    """Claude Code expects `{"hooks": {<event>: [...]}}`, not bare events.

    Shipped without the wrapper the whole *plugin* fails `claude plugin
    validate`, and a plugin that fails validation is dropped from its
    marketplace listing silently — it does not appear, and nothing says why.
    Every handler underneath can be perfectly correct and none of it runs.
    """
    document = wiring()
    assert set(document) == {"hooks"}, "events must be nested under `hooks`"
    assert set(document["hooks"]) == {"SessionStart", "PreToolUse",
                                      "PostToolUse", "Stop"}


def test_all_four_events_are_wired_through_one_launcher():
    events = wiring()["hooks"]
    commands = [hook["command"]
                for entries in events.values()
                for entry in entries
                for hook in entry["hooks"]]
    assert len(commands) == 4
    for command in commands:
        # One resolution path, so there is one thing to get wrong on Windows.
        assert command.startswith("${CLAUDE_PLUGIN_ROOT}/bin/szsdlc hook ")
        assert command.rsplit(" ", 1)[-1] in hooks.HANDLERS


def test_the_edit_hooks_match_the_editing_tools():
    events = wiring()["hooks"]
    for event in ("PreToolUse", "PostToolUse"):
        assert events[event][0]["matcher"] == "Edit|Write|NotebookEdit"


def test_both_launchers_exist_and_the_posix_one_is_executable():
    posix = REPO / "bin" / "szsdlc"
    assert posix.is_file() and (REPO / "bin" / "szsdlc.cmd").is_file()
    if os.name != "nt":
        assert posix.stat().st_mode & stat.S_IXUSR


def test_the_launchers_agree_on_resolution_order():
    """PATH, then a cached venv, then build one. Same order on both platforms."""
    posix = (REPO / "bin" / "szsdlc").read_text("utf-8")
    windows = (REPO / "bin" / "szsdlc.cmd").read_text("utf-8")
    for text in (posix, windows):
        assert "CLAUDE_PLUGIN_ROOT" in text
        assert "CLAUDE_PLUGIN_DATA" in text
        assert "SZSDLC_LAUNCHER" in text  # the recursion guard
        assert "-m szsdlc.cli" in text
    # `py -3` before `python`: on Windows `python` is often a Store stub.
    assert windows.index("py -3") < windows.index("python -c")


def test_both_launchers_probe_interpreters_rather_than_merely_finding_them():
    """Under Git Bash on Windows, `python3` resolves to the Microsoft Store
    stub: on PATH, at the front of it, and unable to run anything. Taking the
    first name that exists picks the stub every time and the hook silently
    does nothing — which is what the first version of this launcher did.

    Running each candidate also enforces the 3.12 floor for free.
    """
    for name in ("szsdlc", "szsdlc.cmd"):
        text = (REPO / "bin" / name).read_text("utf-8")
        assert "sys.version_info >= (3, 12)" in text, name
        assert "no working Python 3.12+" in text, name


# ---------------------------------------------------------------------------
# Harmless outside a project
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("event", sorted(hooks.HANDLERS))
def test_every_hook_is_a_silent_no_op_outside_a_project(tmp_path, capsys, event):
    target = tmp_path / "notes.md"
    target.write_text("just a file\n", encoding="utf-8")

    code = hooks.dispatch(event, payload(file_path=str(target)), cwd=str(tmp_path))
    captured = capsys.readouterr()
    assert code == 0
    assert captured.out == "" and captured.err == ""


@pytest.mark.parametrize("event", sorted(hooks.HANDLERS))
def test_a_malformed_payload_is_treated_as_no_payload(seeded, capsys, event):
    code = hooks.dispatch(event, io.StringIO("not json at all"),
                          cwd=str(seeded.root))
    capsys.readouterr()
    assert code == 0


# ---------------------------------------------------------------------------
# SessionStart
# ---------------------------------------------------------------------------


def test_session_start_puts_the_counters_into_context(seeded, capsys):
    code = hooks.dispatch("session-start", io.StringIO(""), cwd=str(seeded.root))
    output = capsys.readouterr().out
    assert code == 0
    assert "entities 2" in output
    assert len(output) <= 1800


def test_session_start_on_an_empty_project_still_says_something_small(project, capsys):
    hooks.dispatch("session-start", io.StringIO(""), cwd=str(project.root))
    output = capsys.readouterr().out
    assert "entities 0" in output
    assert len(output.splitlines()) <= 2


# ---------------------------------------------------------------------------
# PreToolUse
# ---------------------------------------------------------------------------


def test_editing_a_generated_file_is_blocked_with_the_source_named(seeded, capsys):
    board = seeded.views_dir / "board.md"
    code = hooks.dispatch("pre-edit", payload(file_path=str(board)),
                          cwd=str(seeded.root))
    captured = capsys.readouterr()
    assert code == hooks.EXIT_BLOCK
    assert "generated by szsdlc" in captured.err
    assert "'board' view" in captured.err
    assert "szsdlc sync" in captured.err


def test_editing_a_source_file_is_allowed(seeded, capsys):
    entity = next(seeded.dir_for("work_item").glob("WI-0001-*")) / "entity.md"
    code = hooks.dispatch("pre-edit", payload(file_path=str(entity)),
                          cwd=str(seeded.root))
    assert code == 0
    assert capsys.readouterr().err == ""


def test_a_file_that_does_not_exist_yet_is_allowed(seeded, capsys):
    target = seeded.root / "brand-new.md"
    assert hooks.dispatch("pre-edit", payload(file_path=str(target)),
                          cwd=str(seeded.root)) == 0


def test_standards_arrive_as_context_at_the_moment_of_editing(seeded, capsys):
    """The measured problem this solves is re-injecting every convention into
    every invocation; here 200-odd tokens arrive only where they apply."""
    seeded.standards_dir.mkdir(parents=True, exist_ok=True)
    (seeded.standards_dir / "work-items.md").write_text(
        '---\napplies_to: ["work-items/**"]\n---\n'
        "Never hardcode a version; reference the pinned digest.\n", encoding="utf-8")

    entity = next(seeded.dir_for("work_item").glob("WI-0001-*")) / "entity.md"
    code = hooks.dispatch("pre-edit", payload(file_path=str(entity)),
                          cwd=str(seeded.root))
    emitted = json.loads(capsys.readouterr().out)

    assert code == 0
    assert emitted["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    assert "pinned digest" in emitted["hookSpecificOutput"]["additionalContext"]


def test_nothing_is_emitted_when_no_standard_matches(seeded, capsys):
    seeded.standards_dir.mkdir(parents=True, exist_ok=True)
    (seeded.standards_dir / "templates.md").write_text(
        '---\napplies_to: ["templates/**"]\n---\nSomething else.\n', encoding="utf-8")

    entity = next(seeded.dir_for("work_item").glob("WI-0001-*")) / "entity.md"
    hooks.dispatch("pre-edit", payload(file_path=str(entity)), cwd=str(seeded.root))
    assert capsys.readouterr().out == ""


# ---------------------------------------------------------------------------
# PostToolUse
# ---------------------------------------------------------------------------


def test_editing_an_entity_regenerates_the_views(seeded, capsys):
    entity = next(seeded.dir_for("work_item").glob("WI-0001-*")) / "entity.md"
    entity.write_bytes(entity.read_bytes().replace(b"title: Add config",
                                                   b"title: Renamed entirely"))
    board_before = (seeded.views_dir / "board.md").read_text("utf-8")

    code = hooks.dispatch("post-edit", payload(file_path=str(entity)),
                          cwd=str(seeded.root))
    captured = capsys.readouterr()

    assert code == 0
    assert captured.out == "" and captured.err == ""
    board_after = (seeded.views_dir / "board.md").read_text("utf-8")
    assert board_after != board_before
    assert "Renamed entirely" in board_after


def test_editing_the_roadmap_regenerates_too(seeded, capsys):
    path = seeded.roadmap_path("roadmap")
    before = (seeded.views_dir / "roadmap.md").read_text("utf-8")
    assert "### now\n\n_empty_" in before

    path.write_text("now:\n  - EPIC-0001\nnext: []\nlater: []\n", encoding="utf-8")
    hooks.dispatch("post-edit", payload(file_path=str(path)), cwd=str(seeded.root))
    capsys.readouterr()

    after = (seeded.views_dir / "roadmap.md").read_text("utf-8")
    assert after.index("EPIC-0001") < after.index("### next")


def test_editing_an_unrelated_file_regenerates_nothing(seeded, capsys):
    unrelated = seeded.root / "README.md"
    unrelated.write_text("hello\n", encoding="utf-8")
    before = (seeded.views_dir / "board.md").read_bytes()

    hooks.dispatch("post-edit", payload(file_path=str(unrelated)),
                   cwd=str(seeded.root))
    assert (seeded.views_dir / "board.md").read_bytes() == before


def test_post_edit_stays_silent_against_a_broken_project(seeded, capsys, write_entity):
    """sync is tolerant during a turn; the redraw must never break the edit."""
    write_entity("work_item", 9, raw="---\nbroken: [\n---\n", slug="corrupt")
    entity = next(seeded.dir_for("work_item").glob("WI-0001-*")) / "entity.md"
    code = hooks.dispatch("post-edit", payload(file_path=str(entity)),
                          cwd=str(seeded.root))
    captured = capsys.readouterr()
    assert code == 0 and captured.out == "" and captured.err == ""


# ---------------------------------------------------------------------------
# Stop
# ---------------------------------------------------------------------------


def test_stop_passes_a_clean_project_silently(seeded, capsys):
    code = hooks.dispatch("stop", io.StringIO(""), cwd=str(seeded.root))
    captured = capsys.readouterr()
    assert code == 0
    assert captured.out == "" and captured.err == ""


def test_stop_blocks_on_errors_and_names_each_fix(seeded, capsys, write_entity):
    write_entity("work_item", 9, "title: No parent\nstatus: idea\nopened: 2026-08-01\n",
                 slug="orphan")
    code = hooks.dispatch("stop", io.StringIO(""), cwd=str(seeded.root))
    err = capsys.readouterr().err

    assert code == hooks.EXIT_BLOCK
    assert "WI-0009" in err
    assert "szsdlc link WI-0009 parent" in err


def test_stop_does_not_block_on_warnings_alone(seeded, capsys, write_entity):
    write_entity("idea", 1, "status: inbox\ncaptured: 2020-01-01\n", "an old thought\n")
    main(["-C", str(seeded.root), "sync"])
    capsys.readouterr()

    code = hooks.dispatch("stop", io.StringIO(""), cwd=str(seeded.root))
    captured = capsys.readouterr()
    assert code == 0 and captured.err == ""


def test_the_blocked_listing_is_bounded(seeded, capsys, write_entity):
    for number in range(20):
        write_entity("work_item", 100 + number,
                     "title: T\nstatus: idea\nopened: 2026-08-01\n", slug=f"o{number}")
    hooks.dispatch("stop", io.StringIO(""), cwd=str(seeded.root))
    lines = capsys.readouterr().err.strip().splitlines()
    assert len(lines) <= 12
    assert "more — szsdlc validate" in lines[-1]


# ---------------------------------------------------------------------------
# The hidden subcommand
# ---------------------------------------------------------------------------


def test_the_hook_command_is_registered_but_undocumented(capsys):
    from szsdlc.cli import COMMANDS, HIDDEN_COMMANDS, build_parser

    registered = set(build_parser()._subparsers._group_actions[0].choices)
    documented = set()
    for invocation, _ in COMMANDS:
        documented |= set(invocation.split()[0].split("|"))

    assert HIDDEN_COMMANDS <= registered
    assert registered - documented == HIDDEN_COMMANDS

    main(["--help"])
    assert "hook" not in capsys.readouterr().out


def test_the_hook_command_runs_end_to_end(seeded, capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert main(["-C", str(seeded.root), "hook", "session-start"]) == 0
    assert "entities 2" in capsys.readouterr().out


def test_an_unknown_hook_event_is_refused_compactly(capsys):
    assert main(["hook", "nonsense"]) == 2
    err = capsys.readouterr().err
    assert len(err.strip().splitlines()) <= 3
    assert "usage:" not in err


def test_the_real_validator_accepts_this_plugin():
    """The only assertion here that could have caught the missing wrapper.

    Every hand-written check in this file passed while `hooks.json` was
    malformed, because each one asserted the shape the file already had.
    `claude plugin validate` is the actual contract, so when the binary is
    available, defer to it rather than to our own reading of the format.
    """
    claude = shutil.which("claude")
    if not claude:
        pytest.skip("the claude CLI is not on PATH")

    result = subprocess.run([claude, "plugin", "validate", str(REPO)],
                            capture_output=True, text=True, timeout=180)
    output = result.stdout + result.stderr
    # Older binaries reject `$schema`/`description` at a marketplace root; that
    # is their bug, not ours, and it cannot arise for a plugin manifest.
    assert "hooks" not in output.lower() or "error" not in output.lower(), output
    assert "Validation failed" not in output, output
