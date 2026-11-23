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
    run("render")
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
    assert set(document["hooks"]) == {"SessionStart", "UserPromptSubmit", "PreToolUse",
                                      "PostToolUse", "Stop"}


def test_every_event_is_wired_through_one_launcher():
    events = wiring()["hooks"]
    commands = [hook["command"]
                for entries in events.values()
                for entry in entries
                for hook in entry["hooks"]]
    assert len(commands) == 5
    handlers = {event: [hook["command"].rsplit(" ", 1)[-1]
                        for entry in entries for hook in entry["hooks"]]
                for event, entries in events.items()}
    assert handlers == {"SessionStart": ["session-start"],
                        "UserPromptSubmit": ["user-prompt-submit"],
                        "PreToolUse": ["pre-edit"],
                        "PostToolUse": ["post-edit"],
                        "Stop": ["stop"]}
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


#: Each source the launchers are documented to consult, and the marker that
#: proves it. Shared so neither launcher can quietly drop one.
CANDIDATE_SOURCES = [
    ("an active virtualenv", "VIRTUAL_ENV", "VIRTUAL_ENV"),
    ("a .venv up the tree", ".venv", ".venv"),
    ("the project directory", "CLAUDE_PROJECT_DIR", "CLAUDE_PROJECT_DIR"),
    ("every version, not one bare name", '"$path_dir"/python3.*', "py --list"),
    ("newest version first", "sort -k1,1nr", "py --list"),
    ("a uv-managed interpreter",
     'uv python find ">=$minimum_python_version"',
     'uv python find ">=%MINIMUM_PYTHON_VERSION%"'),
    ("the checkout, when no venv can be built",
     "import yaml, jinja2, jsonschema", "import yaml, jinja2, jsonschema"),
]


@pytest.mark.parametrize("source, posix_marker, windows_marker", CANDIDATE_SOURCES,
                         ids=[source for source, _, _ in CANDIDATE_SOURCES])
def test_both_launchers_consult_every_documented_source(source, posix_marker,
                                                        windows_marker):
    """The bug this fixes: `python3` is 3.9 on many boxes that also ship
    python3.12, and three bare names never saw it."""
    assert posix_marker in launchers()["szsdlc"], source
    assert windows_marker in launchers()["szsdlc.cmd"], source


def test_both_launchers_leave_the_ambiguous_names_for_last():
    """Whatever a bare name points at, a versioned one said it better — and on
    Windows `python` is frequently a Store stub."""
    posix, windows = launchers()["szsdlc"], launchers()["szsdlc.cmd"]
    assert posix.index('"$path_dir"/python3.*') < posix.index("printf '%s\\n' python3 python py")
    assert windows.index("py --list") < windows.index("echo python>>")


def test_both_launchers_keep_trying_after_a_compatible_python_fails():
    """Passing the probe does not mean `-m venv` works: Debian packages
    `ensurepip` separately."""
    for name, text in launchers().items():
        assert "could not build a virtualenv" in text, name


def test_the_launchers_floor_is_the_one_the_package_declares():
    """A floor raised in pyproject.toml and not here would have the launchers
    build a venv the package then refuses to install into, and the hook would
    fail with pip's error rather than the launcher's."""
    import re
    import tomllib

    pyproject = tomllib.loads((REPO / "pyproject.toml").read_text("utf-8"))
    declared = pyproject["project"]["requires-python"]
    floor = re.fullmatch(r">=\s*(\d+\.\d+)", declared.strip())
    assert floor, f"requires-python is {declared!r}; this test assumes `>=X.Y`"

    patterns = {"szsdlc": r'minimum_python_version="([^"]+)"',
                "szsdlc.cmd": r'MINIMUM_PYTHON_VERSION=([^"]+)"'}
    for name, text in launchers().items():
        stated = re.search(patterns[name], text)
        assert stated, f"{name} states no minimum version"
        assert stated.group(1) == floor.group(1), name


def test_both_launchers_probe_interpreters_rather_than_merely_finding_them():
    """Under Git Bash on Windows, `python3` resolves to the Microsoft Store
    stub: on PATH, at the front of it, and unable to run anything. Taking the
    first name that exists picks the stub every time and the hook silently
    does nothing — which is what the first version of this launcher did.
    """
    posix, windows = launchers()["szsdlc"], launchers()["szsdlc.cmd"]
    assert 'sys.version_info >=' in posix and '-c "$version_probe"' in posix
    assert 'sys.version_info >=' in windows and '-c "%VERSION_PROBE%"' in windows
    for name, text in launchers().items():
        assert "no working Python" in text, name


@pytest.mark.skipif(os.name == "nt", reason="exercises the POSIX launcher")
def test_the_posix_launcher_picks_a_versioned_python_over_an_old_default(tmp_path):
    """The regression, end to end, on a PATH built to contain it.

    `python3` fails the floor; `python3.99` passes, then refuses to build a
    venv or import the dependencies. Naming it in the failure is a cheap way
    to observe which interpreter was chosen without a real pip install.
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
    assert "szsdlc render" in captured.err


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
    main(["-C", str(seeded.root), "render"])
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
    """Validating first would block the turn naming a fix that was about to
    happen anyway."""
    board = drift(seeded)
    assert "Renamed in the turn" not in board.read_text("utf-8")

    code = hooks.dispatch("stop", io.StringIO(""), cwd=str(seeded.root))
    captured = capsys.readouterr()

    assert code == 0, captured.err
    assert captured.out == "" and captured.err == ""
    assert "Renamed in the turn" in board.read_text("utf-8")


def test_the_turn_end_sync_can_be_switched_off(seeded, capsys, monkeypatch):
    """Off, the handler is what it was before: validate, strictly."""
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_RENDER_ON_TURN_END", "false")
    board = drift(seeded)

    code = hooks.dispatch("stop", io.StringIO(""), cwd=str(seeded.root))
    err = capsys.readouterr().err

    assert code == hooks.EXIT_BLOCK
    assert "out of date" in err
    assert "Renamed in the turn" not in board.read_text("utf-8")


def test_the_turn_end_sync_refuses_to_overwrite_a_hand_edited_view(seeded, capsys):
    """A hand-edited generated file is a blocking error whose evidence is the
    file: overwriting it here takes the error quiet with it."""
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


@pytest.mark.parametrize("raw, expected", [
    ("true", True), ("TRUE", True), ("1", True), ("yes", True), ("on", True),
    ("false", False), ("False", False), ("0", False), ("no", False), ("off", False),
    (" true ", True),
])
def test_a_plugin_setting_is_read_in_every_spelling_it_could_arrive_in(
        monkeypatch, raw, expected):
    """Claude Code documents the variable name but not the spelling of a
    boolean, so guessing one would silently take the default forever."""
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_RENDER_ON_TURN_END", raw)
    assert hooks.option("render_on_turn_end", default=not expected) is expected


@pytest.mark.parametrize("raw", ["", "   ", "perhaps"])
def test_an_unset_or_unrecognised_setting_is_the_declared_default(monkeypatch, raw):
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_RENDER_ON_TURN_END", raw)
    assert hooks.option("render_on_turn_end", default=True) is True
    assert hooks.option("render_on_turn_end", default=False) is False


def test_the_setting_is_still_read_under_the_name_it_used_to_have(monkeypatch):
    """`sync_on_turn_end` named the wrong operation — it governed rendering and
    never the backend — but an installed plugin carries the choice someone made
    against the old name. Dropping the key would not restore the default; it
    would silently ignore a decision already taken."""
    monkeypatch.delenv("CLAUDE_PLUGIN_OPTION_RENDER_ON_TURN_END", raising=False)
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_SYNC_ON_TURN_END", "false")
    assert hooks.option("render_on_turn_end", default=True) is False


def test_the_new_name_wins_over_the_old_one(monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_SYNC_ON_TURN_END", "false")
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_RENDER_ON_TURN_END", "true")
    assert hooks.option("render_on_turn_end", default=False) is True


def test_the_setting_is_declared_in_the_manifest_so_config_can_show_it():
    """`/config` lists what the manifest declares, so an undeclared setting is
    a flag with no switch. No `options` field: that would pin the plugin to
    Claude Code v2.1.271 or later."""
    manifest = json.loads((REPO / ".claude-plugin" / "plugin.json").read_text("utf-8"))
    declared = manifest["userConfig"]["render_on_turn_end"]
    assert declared["type"] == "boolean"
    assert declared["default"] is True
    assert declared["title"] and declared["description"]
    for option in manifest["userConfig"].values():
        assert "options" not in option


def test_nothing_about_this_setting_leaks_into_the_project_config():
    """config.yml is read by the CLI and by CI, which have no turns to sync
    at. The plugin layer owns this setting."""
    schema = (REPO / "src" / "szsdlc" / "schemas" / "config.schema.json").read_text("utf-8")
    defaults = (REPO / "src" / "szsdlc" / "defaults" / "config.yml").read_text("utf-8")
    for text in (schema, defaults):
        assert "render_on_turn_end" not in text and "sync_on_turn_end" not in text


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
    for name, stamp in (("szsdlc", '"$stamp_file_path"'),
                        ("szsdlc.cmd", '"%STAMP_FILE_PATH%"')):
        text = (REPO / "bin" / name).read_text("utf-8")
        assert "installed-from" in text, name
        assert "--upgrade" in text, name
        # Stamped on both paths: after refreshing a stale venv, and after
        # building a fresh one. Miss the second and every new install
        # reinstalls itself on its very next invocation.
        assert text.count(stamp) >= 3, name
