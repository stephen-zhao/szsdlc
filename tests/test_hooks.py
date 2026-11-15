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
import sys
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


def test_the_plugin_manifest_leaves_the_hooks_file_to_convention():
    """`hooks/hooks.json` is loaded by convention, so naming it is fatal.

    Declaring `manifest.hooks` registers the same file a second time and the
    loader refuses the whole plugin:

        Hook load failed: Duplicate hooks file detected: ./hooks/hooks.json

    The file must exist and the manifest must stay silent about it.
    """
    manifest = json.loads((REPO / ".claude-plugin" / "plugin.json").read_text("utf-8"))
    assert manifest["name"] == "szsdlc"
    assert "hooks" not in manifest
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
        # The placeholder is quoted: these are shell-form commands, and an
        # install path containing a space — `C:\\Users\\First Last\\...` is the
        # ordinary case on Windows — otherwise splits into several words and
        # the hook silently fails to run. `claude plugin validate` says so too.
        assert command.startswith('"${CLAUDE_PLUGIN_ROOT}"/bin/szsdlc hook ')
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


def launchers() -> dict[str, str]:
    return {name: (REPO / "bin" / name).read_text("utf-8")
            for name in ("szsdlc", "szsdlc.cmd")}


def test_the_launchers_agree_on_resolution_order():
    """PATH, then a cached venv, then build one. Same order on both platforms."""
    for text in launchers().values():
        assert "CLAUDE_PLUGIN_ROOT" in text
        assert "CLAUDE_PLUGIN_DATA" in text
        assert "SZSDLC_LAUNCHER" in text  # the recursion guard
        assert "-m szsdlc.cli" in text


def test_both_launchers_prefer_an_interpreter_somebody_already_chose():
    """An active virtualenv, then a `.venv` beside the project and above it.

    uv and the Unix `py` launcher both consult these ahead of PATH, for the
    same reason: if a developer has picked a Python for this tree, that is the
    one whose version and packages they actually maintain. The old launcher saw
    a venv only when szsdlc happened to be installed into it.
    """
    for name, text in launchers().items():
        assert "VIRTUAL_ENV" in text, name
        assert ".venv" in text, name
        assert "CLAUDE_PROJECT_DIR" in text, name


def test_both_launchers_consider_every_version_not_one_bare_name():
    """The bug this fixes: `python3` is 3.9 on a great many boxes that also
    ship python3.12, and asking three bare names for one answer each reported
    "no working Python 3.12+" with a usable one sitting right there on PATH.

    POSIX scans PATH for every `python3.N`, newest first, which is the Unix
    `py` launcher's own rule; Windows asks `py --list`, which already reports
    newest first.
    """
    posix, windows = launchers()["szsdlc"], launchers()["szsdlc.cmd"]
    assert '"$dir"/python3.*' in posix
    assert "sort -k1,1nr" in posix          # newest minor version first
    assert "py --list" in windows

    # The ambiguous bare names go last: whatever they point at, a versioned
    # name said it better, and on Windows `python` is frequently a Store stub.
    assert posix.index('"$dir"/python3.*') < posix.index("printf '%s\\n' python3 python py")
    assert windows.index("py --list") < windows.index("echo python>>")


def test_both_launchers_keep_trying_after_a_compatible_python_fails():
    """Passing the version probe does not mean `-m venv` will work: Debian and
    Ubuntu split `ensurepip` into python3-venv, so a perfectly good 3.12 can
    fail to make one. Giving up on the box at that point is what made this a
    loop rather than a single choice.
    """
    for name, text in launchers().items():
        assert "python3-venv" in text or "could not build a virtualenv" in text, name
    # And when no venv can be built anywhere, run from the checkout instead.
    for name, text in launchers().items():
        assert "import yaml, jinja2, jsonschema" in text, name
        assert "PYTHONPATH" in text, name


def test_both_launchers_ask_uv_before_giving_up():
    """Somebody who installs Python only through uv has nothing on PATH that
    matches. `uv python find` is a lookup and never downloads, so this stays a
    search rather than a hook that quietly reaches for the network.
    """
    for name, text in launchers().items():
        assert 'uv python find ">=3.12"' in text, name


def test_the_launchers_floor_is_the_one_the_package_declares():
    """`requires-python` is the source of truth and the launchers quote it.

    Three copies of "3.12" is two too many to leave unchecked: a floor raised
    in pyproject.toml and not here would have the launchers build a venv the
    package then refuses to install into, and the hook would fail with pip's
    error rather than the launcher's.
    """
    import re
    import tomllib

    pyproject = tomllib.loads((REPO / "pyproject.toml").read_text("utf-8"))
    declared = pyproject["project"]["requires-python"]
    floor = re.fullmatch(r">=\s*(\d+)\.(\d+)", declared.strip())
    assert floor, f"requires-python is {declared!r}; this test assumes `>=X.Y`"
    major, minor = floor.group(1), floor.group(2)

    for name, text in launchers().items():
        assert f"sys.version_info >= ({major}, {minor})" in text, name
        assert f"no working Python {major}.{minor}+" in text, name
        assert f'uv python find ">={major}.{minor}"' in text, name


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


@pytest.mark.skipif(os.name == "nt", reason="exercises the POSIX launcher")
def test_the_posix_launcher_picks_a_versioned_python_over_an_old_default(tmp_path):
    """The regression, end to end, on a PATH built to contain it.

    `python3` fails the floor however it is asked; `python3.99` passes and then
    refuses to build a venv or to import the dependencies. So the launcher gets
    as far as *choosing* it and reports that it could not install — which is a
    cheap way to observe which interpreter it chose without waiting for a real
    pip install. The old launcher never saw python3.99 at all and said there
    was no working Python on the box.
    """
    tools = ["sh", "sed", "awk", "sort", "cut", "head", "dirname", "rm", "mkdir", "cat"]
    sandbox = tmp_path / "bin"
    sandbox.mkdir()
    for tool in tools:
        found = shutil.which(tool)
        if not found:
            pytest.skip(f"{tool} is not on PATH")
        (sandbox / tool).symlink_to(found)

    (sandbox / "python3").write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    (sandbox / "python3.99").write_text(
        "#!/bin/sh\n"
        'for a in "$@"; do [ "$a" = venv ] && exit 1; done\n'
        'case "$*" in *jinja2*) exit 1 ;; esac\n'
        f'exec {sys.executable} "$@"\n', encoding="utf-8")
    for name in ("python3", "python3.99"):
        (sandbox / name).chmod(0o755)

    # cwd and CLAUDE_PROJECT_DIR are isolated on purpose: the `.venv` walk is
    # part of what is being tested, and this checkout has one.
    home = tmp_path / "home"
    home.mkdir()
    result = subprocess.run(
        ["sh", str(REPO / "bin" / "szsdlc"), "--version"],
        cwd=str(home),
        env={"PATH": str(sandbox), "HOME": str(home),
             "CLAUDE_PROJECT_DIR": str(home),
             "CLAUDE_PLUGIN_ROOT": str(REPO),
             "CLAUDE_PLUGIN_DATA": str(tmp_path / "data")},
        capture_output=True, text=True, timeout=180)

    assert "python3.99" in result.stderr, result.stderr
    assert "could not build a virtualenv" in result.stderr, result.stderr
    assert "no working Python" not in result.stderr, result.stderr


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
    write_entity("work_item", 9, "title: Dangles\nstatus: idea\nopened: 2026-08-01\n"
                                 "relations:\n  implements: [REQ-0420]\n",
                 slug="dangles")
    code = hooks.dispatch("stop", io.StringIO(""), cwd=str(seeded.root))
    err = capsys.readouterr().err

    assert code == hooks.EXIT_BLOCK
    assert "WI-0009" in err
    assert "szsdlc unlink WI-0009 implements REQ-0420" in err


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
                     "title: T\nstatus: idea\nopened: 2026-08-01\n"
                     "relations:\n  implements: [REQ-0420]\n", slug=f"o{number}")
    hooks.dispatch("stop", io.StringIO(""), cwd=str(seeded.root))
    lines = capsys.readouterr().err.strip().splitlines()
    assert len(lines) <= 12
    assert "more — szsdlc validate" in lines[-1]


# ---------------------------------------------------------------------------
# Stop — the turn-end sync, and the setting that governs it
# ---------------------------------------------------------------------------


def drift(seeded) -> Path:
    """Change a source file behind sync's back, as a turn full of edits does."""
    entity = next(seeded.dir_for("work_item").glob("WI-0001-*")) / "entity.md"
    entity.write_bytes(entity.read_bytes().replace(b"title: Add config",
                                                   b"title: Renamed in the turn"))
    return seeded.views_dir / "board.md"


def test_stop_syncs_before_it_validates(seeded, capsys):
    """The order is the whole point of doing both in one handler.

    Validating first finds every view the turn touched out of date and blocks
    the turn naming a fix — `szsdlc sync` — that was about to happen anyway.
    Syncing first means the check judges the state the turn actually leaves.
    """
    board = drift(seeded)
    assert "Renamed in the turn" not in board.read_text("utf-8")

    code = hooks.dispatch("stop", io.StringIO(""), cwd=str(seeded.root))
    captured = capsys.readouterr()

    assert code == 0, captured.err
    assert captured.out == "" and captured.err == ""
    assert "Renamed in the turn" in board.read_text("utf-8")


def test_the_turn_end_sync_can_be_switched_off(seeded, capsys, monkeypatch):
    """Off, the handler is exactly what it was before: validate, strictly.

    The setting is a plugin setting rather than anything in config.yml, so it
    arrives the way Claude Code passes those — an environment variable on the
    hook process.
    """
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_SYNC_ON_TURN_END", "false")
    board = drift(seeded)

    code = hooks.dispatch("stop", io.StringIO(""), cwd=str(seeded.root))
    err = capsys.readouterr().err

    assert code == hooks.EXIT_BLOCK
    assert "out of date" in err
    assert "Renamed in the turn" not in board.read_text("utf-8")


def test_the_turn_end_sync_refuses_to_overwrite_a_hand_edited_view(seeded, capsys):
    """An unprompted sync must not be how somebody's text disappears.

    A hand-edited generated file is a blocking error, and the evidence for it
    *is* the file: overwrite it here and the error goes quiet along with
    whatever was typed. So the turn-end sync regenerates around it and leaves
    both the text and the complaint — while `szsdlc sync`, which is a person
    saying overwrite it, still does.
    """
    board = seeded.views_dir / "board.md"
    board.write_text(board.read_text("utf-8") + "\nA note somebody typed here.\n",
                     encoding="utf-8")
    before = board.read_text("utf-8")
    drift(seeded)

    code = hooks.dispatch("stop", io.StringIO(""), cwd=str(seeded.root))
    err = capsys.readouterr().err

    assert code == hooks.EXIT_BLOCK
    assert "edited by hand" in err
    assert board.read_text("utf-8") == before, "the turn-end sync clobbered a hand edit"


def test_an_explicit_sync_still_overwrites_a_hand_edited_view(seeded, capsys):
    """The counterpart: typing the command means what it has always meant."""
    board = seeded.views_dir / "board.md"
    board.write_text(board.read_text("utf-8") + "\nA note somebody typed here.\n",
                     encoding="utf-8")

    main(["-C", str(seeded.root), "sync"])
    capsys.readouterr()
    assert "A note somebody typed here." not in board.read_text("utf-8")


@pytest.mark.parametrize("raw, expected", [
    ("true", True), ("TRUE", True), ("1", True), ("yes", True), ("on", True),
    ("false", False), ("False", False), ("0", False), ("no", False), ("off", False),
    (" true ", True),
])
def test_a_plugin_setting_is_read_in_every_spelling_it_could_arrive_in(
        monkeypatch, raw, expected):
    """Claude Code documents the variable name but not how a boolean is spelled
    on the way through, so guessing one spelling would mean silently taking the
    default forever on a version that picked another."""
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_SYNC_ON_TURN_END", raw)
    assert hooks.option("sync_on_turn_end", default=not expected) is expected


@pytest.mark.parametrize("raw", ["", "   ", "perhaps"])
def test_an_unset_or_unrecognised_setting_is_the_declared_default(monkeypatch, raw):
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_SYNC_ON_TURN_END", raw)
    assert hooks.option("sync_on_turn_end", default=True) is True
    assert hooks.option("sync_on_turn_end", default=False) is False


def test_the_setting_is_declared_in_the_manifest_so_config_can_show_it():
    """An undeclared setting is one nobody can find: `/config` lists what the
    manifest declares, and a hook reading an env var nothing writes is a flag
    with no switch. Declared `boolean` with a default, and no `options` field —
    one of those would pin the whole plugin to Claude Code v2.1.271 or later.
    """
    manifest = json.loads((REPO / ".claude-plugin" / "plugin.json").read_text("utf-8"))
    declared = manifest["userConfig"]["sync_on_turn_end"]
    assert declared["type"] == "boolean"
    assert declared["default"] is True
    assert declared["title"] and declared["description"]
    for option in manifest["userConfig"].values():
        assert "options" not in option


def test_nothing_about_this_setting_leaks_into_the_project_config():
    """It is a fact about Claude Code, not about the project.

    config.yml is read by the CLI, by CI and by whatever front end comes next;
    a Claude-Code-only flag parked in it is one they all have to know to
    ignore. The plugin layer owns it, and that boundary is worth a test.
    """
    schema = (REPO / "src" / "szsdlc" / "schemas" / "config.schema.json").read_text("utf-8")
    defaults = (REPO / "src" / "szsdlc" / "defaults" / "config.yml").read_text("utf-8")
    for text in (schema, defaults):
        assert "sync_on_turn_end" not in text


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
def test_both_launchers_refresh_a_venv_left_from_an_earlier_release():
    """A launcher that execs any venv it finds runs the version it built first.

    Claude Code installs each release into its own cache directory and leaves
    the old ones in place, so without a staleness check `/plugin update` writes
    new code to disk that nothing ever runs — and the remedy is an `rm -rf` of
    a data directory nobody knows exists. The stamp records which release the
    venv holds; a mismatch reinstalls over it.
    """
    for name, stamp in (("szsdlc", '"$stamp"'), ("szsdlc.cmd", '"%STAMP%"')):
        text = (REPO / "bin" / name).read_text("utf-8")
        assert "installed-from" in text, name
        assert "--upgrade" in text, name
        # Stamped on both paths: after refreshing a stale venv, and after
        # building a fresh one. Miss the second and every new install
        # reinstalls itself on its very next invocation.
        assert text.count(stamp) >= 3, name
