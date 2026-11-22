"""Claude Code hook handlers.

Hooks are what make the deterministic operations automatic and the gates
non-optional. The division of labour matters: `render` runs constantly and
tolerantly — on every write, and again as the turn ends — while `validate` runs
once, at the end, and strictly.

Two words, and one of them composes the other. **render** derives the
generated views and records from the entities: local, one-way, and no
business of a backend's. **sync** is render followed by whatever the project's
backend does to bring its store into step with wherever it keeps it —
nothing at all for `workdir` and `dir`, and for `git` a commit, a fetch, a
rebase and a push. The render is first because nothing should record or send
a view that is out of date.

The hooks are what make that automatic: a sync as the session starts, before
every prompt is answered, and as the turn ends. Nothing is sent in between. An
edit is not a unit of history — a turn that touches twelve files is one change
— so the turn is the boundary, and a turn of its own work is recorded once.
Work arriving from elsewhere costs a second pass for the redraw, because a view
cannot be drawn from an entity that has not arrived yet. A failed sync never
blocks anything; it is said, and tried again at the next hook.

Every handler is a **silent no-op outside a szsdlc project**, so the plugin is
harmless installed globally. That is not politeness — a hook that errors in a
repo which has never heard of szsdlc is a hook people uninstall.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from .errors import EXIT_OK

#: Claude Code reads exit 2 on a PreToolUse or Stop hook as "block, and show
#: the user what stderr said".
EXIT_BLOCK = 2

#: Claude Code documents the variable name but not how a boolean is spelled on
#: the way through, so every plausible spelling is accepted.
TRUTHY = {"1", "true", "yes", "on"}
FALSEY = {"0", "false", "no", "off"}

#: Options that have been renamed, and the older names still read for them, in
#: order. An installed plugin carries the settings someone chose against the
#: name it had then; dropping the old key would not restore the default, it
#: would silently ignore a choice already made. Read for one release.
RENAMED_OPTIONS = {"render_on_turn_end": ("sync_on_turn_end",)}


def option(key: str, default: bool) -> bool:
    """Read a plugin setting from the environment Claude Code provides.

    Settings are declared under `userConfig` in `.claude-plugin/plugin.json`,
    not in `.szsdlc/config.yml`, which is also read by the CLI and by CI.

    Args:
        key: The `userConfig` key. Uppercased to form the variable name
            `CLAUDE_PLUGIN_OPTION_<KEY>`.
        default: Returned when the variable is unset, empty, or holds a value
            matching neither TRUTHY nor FALSEY.

    Returns:
        The configured value, or `default`.
    """
    for name in (key, *RENAMED_OPTIONS.get(key, ())):
        raw = os.environ.get(f"CLAUDE_PLUGIN_OPTION_{name.upper()}")
        if raw is None:
            continue
        value = raw.strip().lower()
        if value in TRUTHY:
            return True
        if value in FALSEY:
            return False
        return default
    return default


def read_payload(stream: Any = None) -> dict[str, Any]:
    """The hook's JSON input. A malformed payload is treated as no payload."""
    stream = stream if stream is not None else sys.stdin
    try:
        raw = stream.read()
    except Exception:  # pragma: no cover - stdin closed
        return {}
    if not raw or not raw.strip():
        return {}
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def edited_path(payload: dict[str, Any]) -> Path | None:
    tool_input = payload.get("tool_input") or {}
    for key in ("file_path", "path", "notebook_path"):
        value = tool_input.get(key)
        if value:
            return Path(str(value))
    return None


def _session(start: Path | str | None):
    """Load the project, or None when there isn't one.

    Deliberately swallows everything: a hook must not be the thing that breaks
    a session, and whatever went wrong will be said properly the next time the
    user runs a command themselves.
    """
    return _load(start)[0]


def _load(start: Path | str | None):
    """(session, None), or (None, what went wrong as a short message)."""
    from .cli import Session
    from .errors import SzsdlcError

    try:
        return Session(str(start) if start else None), None
    except SzsdlcError as exc:
        return None, exc.render()
    except Exception as exc:  # pragma: no cover - defensive
        return None, f"{type(exc).__name__}: {exc}"


def _backend_failure(start: Path | str | None, error: str | None,
                     reason: str) -> str | None:
    """Why a project kept by a backend could not be loaded, if that is the case.

    No project at all is the ordinary state of most directories a hook runs in,
    and stays silent. But a selected backend means the user expects one, and a
    clone that failed or settings that are wrong are worth saying. The store is
    still synced when it exists: a broken config edit is recorded and sent
    like any other, and a repair someone else made arrives the same way.
    """
    from . import backend

    if error is None:
        return None
    try:
        selected = backend.select(start).type != backend.WORKDIR
    except Exception:  # settings unreadable: that is the error itself
        selected = True
    if not selected:
        return None
    lines = [f"szsdlc backend: {error}"]
    try:
        problem = backend.describe_problem(backend.sync_at(start, reason))
    except Exception as exc:
        problem = _unexpected(exc)
    if problem:
        lines.append(problem)
    return "\n".join(lines)


def _sync(session, reason: str, *, only_if_changed: bool = False,
          keep_hand_edited: bool = False,
          should_render: bool = True,
          on_render_error=None) -> tuple[str | None, bool]:
    """Render, then sync. Every hook that sends anything comes through here.

    Returns a problem to report, and whether anything arrived — in which case
    the caller's session describes the state from before it. Never raises, for
    the same reason `_session` does, but an unexpected failure is still
    reported rather than passed off as success.
    """
    from . import backend
    from . import sync as sync_module

    try:
        report = sync_module.run(session, reason, only_if_changed=only_if_changed,
                                 keep_hand_edited=keep_hand_edited,
                                 should_render=should_render,
                                 on_render_error=on_render_error)
    except Exception as exc:
        return _unexpected(exc), False
    return backend.describe_problem(report), bool(report and report.incoming)


def _unexpected(exc: Exception) -> str:
    detail = str(exc).strip().splitlines()
    return (f"szsdlc sync: {type(exc).__name__}"
            + (f": {detail[0]}" if detail else "")
            + "; changes are kept locally.\nFix: szsdlc sync")


def _backend_line(session) -> str | None:
    """Where the project's files are, when that is not where Claude is working."""
    from . import backend

    try:
        managed = backend.managed(session.config.root)
    except Exception:  # pragma: no cover - defensive
        return None
    if managed is None:
        return None
    return f"szsdlc project files: {session.config.root} ({managed.summary()})"


def _emit(event: str, context: str) -> None:
    json.dump({"hookSpecificOutput": {"hookEventName": event,
                                      "additionalContext": context}}, sys.stdout)
    sys.stdout.write("\n")


# ---------------------------------------------------------------------------
# SessionStart
# ---------------------------------------------------------------------------


def session_start(payload: dict[str, Any], cwd: str | None = None) -> int:
    """Stdout enters the model's context, so a session begins knowing the state.

    This is the counters block earning its keep: a handful of tokens that says
    what is in flight and what is wrong, instead of the model searching.
    """
    from . import context as context_module
    from .graph import Graph

    where = cwd or payload.get("cwd")
    session, error = _load(where)
    if session is None:
        failure = _backend_failure(where, error, "session start")
        if failure:
            print(failure)
        return EXIT_OK

    # Pull before the store is read, so the counters describe the remote's state;
    # a pull can change the config itself, so the session is loaded again.
    problem, incoming = _sync(session, "session start")
    if incoming:
        session, error = _load(where)
        if session is None:
            print("\n".join(line for line in (problem, f"szsdlc: after syncing, {error}")
                            if line))
            return EXIT_OK

    graph = Graph(session.config, session.store, session.ids)
    built = context_module.build(session.config, session.store, graph)
    rendered = context_module.render(built)
    lines = [line for line in (_backend_line(session), problem) if line]
    if rendered.strip():
        lines.append(rendered)
    if lines:
        print("\n".join(lines))
    return EXIT_OK


# ---------------------------------------------------------------------------
# UserPromptSubmit
# ---------------------------------------------------------------------------


def user_prompt_submit(payload: dict[str, Any], cwd: str | None = None) -> int:
    """Pull and push before Claude answers, so it works from the remote's state.

    Silent unless the sync failed; stdout here enters the model's context, and
    a problem is worth the tokens where a success is not.
    """
    where = cwd or payload.get("cwd")
    session, error = _load(where)
    if session is None:
        failure = _backend_failure(where, error, "before prompt")
        if failure:
            print(failure)
        return EXIT_OK
    problem, _ = _sync(session, "before prompt")
    if problem:
        print(problem)
    return EXIT_OK


# ---------------------------------------------------------------------------
# PreToolUse — Edit|Write
# ---------------------------------------------------------------------------


def pre_edit(payload: dict[str, Any], cwd: str | None = None) -> int:
    """Refuse edits to generated files, and inject the standards that apply.

    Both halves are the same idea: the convention governing a file arrives at
    the moment it is edited, and never before. A convention that depends on the
    agent remembering to look it up is not a convention.
    """
    from . import render as render_module
    from . import standards as standards_module

    path = edited_path(payload)
    if path is None:
        return EXIT_OK

    session = _session(cwd or payload.get("cwd") or path.parent)
    if session is None:
        return EXIT_OK

    if path.is_file():
        try:
            text = path.read_bytes().decode("utf-8", "replace")
        except OSError:  # pragma: no cover - unreadable target
            text = ""
        if render_module.is_generated(text):
            source = _source_hint(session, path)
            print(f"{path.name} is generated by szsdlc and must not be edited.",
                  file=sys.stderr)
            print(f"Fix: edit {source}, then run szsdlc render", file=sys.stderr)
            return EXIT_BLOCK

    matched = standards_module.matching(session.config, [path])
    if matched:
        # 200–500 tokens for the conventions that apply here, rather than every
        # convention in the project on every turn.
        blocks = [f"## {standard.name}\n{standard.body}" for standard in matched]
        _emit("PreToolUse", "szsdlc standards for this path:\n\n"
              + "\n\n".join(blocks))
    return EXIT_OK


def _source_hint(session, path: Path) -> str:
    """Which view or record produces this file — named, not guessed."""
    for name in session.config.views:
        if session.config.view_path(name) == path:
            return f"the entities behind the {name!r} view"
    for name in session.config.records:
        if session.config.record_path(name) == path:
            return session.config.records[name].data
    return "its source"


# ---------------------------------------------------------------------------
# PostToolUse — Edit|Write
# ---------------------------------------------------------------------------


def post_edit(payload: dict[str, Any], cwd: str | None = None) -> int:
    """Render when a source file changed. Tolerant, silent, every time.

    Renders, and reaches no backend. An edit is not a unit of history: a turn
    that touches twelve files is one change to the project, and sending each
    write made the record a transcript of Claude's tool calls instead. The
    turn-end hook is where the boundary actually is. Nothing is at risk in
    between — the edit is on disk in the store, and `has_local_changes` reads
    it, so the next sync from any entry point sweeps in what is sitting
    there.

    This is the one caller that renders without sending, and it still goes
    through the composite: one render, one place, one definition of tolerant.
    """
    from . import sync as sync_module

    path = edited_path(payload)
    if path is None:
        return EXIT_OK

    session = _session(cwd or payload.get("cwd") or path.parent)
    if session is None:
        # The edit may itself be what broke the project (its config, say).
        # The turn-end hook still syncs on a project that will not load.
        return EXIT_OK

    if _is_source(session, path):
        # Failures are swallowed here, deliberately: a broken template is
        # `validate`'s to report at the turn end, not something to put in
        # Claude's context after every write.
        sync_module.render(session)
    return EXIT_OK


def _is_source(session, path: Path) -> bool:
    candidate = path if path.is_absolute() else (session.config.root / path)
    try:
        candidate = candidate.resolve()
    except OSError:  # pragma: no cover
        return False

    watched = [session.config.dir_for(t) for t in session.config.entity_types.values()]
    watched += [session.config.roadmaps_dir, session.config.standards_dir]
    watched += [(session.config.root / spec.data).parent
                for spec in session.config.records.values()]

    for directory in watched:
        try:
            candidate.relative_to(directory)
        except ValueError:
            continue
        return True
    return False


# ---------------------------------------------------------------------------
# Stop
# ---------------------------------------------------------------------------


def stop(payload: dict[str, Any], cwd: str | None = None) -> int:
    """Sync, then validate, at the end of a turn.

    This is the turn's one boundary, and the only place the turn's work is
    recorded: the whole turn becomes one change here, rather than one per edit
    and one per command. The sequence is

        sync                       (render, then the backend's own work)
          → sync again, reloaded,  if anything arrived
          → validate

    and each step is where it is for a reason:

    One handler rather than two `Stop` entries, which would each load the
    project. The render is inside the sync, and therefore first, so that
    validate judges the state the turn leaves behind rather than naming a fix
    that was about to happen anyway — and so that nothing is recorded or sent
    stale.

    The second pass is not a retry. Views cannot be drawn from entities that
    have not arrived yet, so what came in is drawn, and sent, now rather than
    at the next sync — otherwise what is kept disagrees with itself until
    something else happens to run.

    **Validate runs last, and nothing is gated on it.** An unfinished turn is
    still work, and keeping it on one machine is how it gets lost, so the
    sync happens either way and `validate` reports on what was sent. It is
    last rather than merely late because what arrived is what it must judge:
    the merged state, not the one this machine had before.

    Args:
        payload: The hook's JSON input; `cwd` is read from it when not passed.
        cwd: Project directory to load, overriding the payload.

    Returns:
        EXIT_OK, or EXIT_BLOCK when validate found errors.
    """
    from . import validate as validate_module
    from .graph import Graph

    where = cwd or payload.get("cwd")
    session, error = _load(where)
    if session is None:
        failure = _backend_failure(where, error, "turn end")
        if failure:
            print(failure, file=sys.stderr)
        return EXIT_OK

    rendering = option("render_on_turn_end", default=True)

    # Sent whether or not validate passes: an unfinished turn is still work,
    # and keeping it only on this machine is how it gets lost. The render is
    # the composite's, not this hook's — `keep_hand_edited` because a
    # hand-edited generated file is a blocking error below and the evidence for
    # it is the file itself.
    problem, incoming = _sync(session, "turn end", keep_hand_edited=True,
                              should_render=rendering)
    if problem:
        print(problem, file=sys.stderr)
    if incoming:
        # Judge what arrived, not what was here before: reload, redraw from the
        # entities that arrived so they are not stale, and send that too —
        # now, not at the next sync, so what is kept matches.
        session, error = _load(where)
        if session is None:
            print(f"szsdlc: after syncing, {error}", file=sys.stderr)
            return EXIT_BLOCK
        if rendering:
            redraw_problem, _ = _sync(
                session, "redraw after sync", only_if_changed=True,
                keep_hand_edited=True, on_render_error=_say_redraw_failed)
            if redraw_problem:
                print(redraw_problem, file=sys.stderr)

    graph = Graph(session.config, session.store, session.ids)
    findings = validate_module.run(session.config, session.store, graph)
    errors = [f for f in findings if f.is_error]

    if not errors:
        return EXIT_OK

    print(f"szsdlc validate found {len(errors)} "
          f"error{'s' if len(errors) != 1 else ''}:", file=sys.stderr)
    for finding in errors[:10]:
        print(f"  {finding.ref}  {finding.message}"
              + (f" — {finding.fix}" if finding.fix else ""), file=sys.stderr)
    if len(errors) > 10:
        print(f"  … {len(errors) - 10} more — szsdlc validate", file=sys.stderr)
    return EXIT_BLOCK


def _say_redraw_failed(exc: Exception) -> None:
    """A draw that failed on what just arrived is worth a line.

    The sync tolerates it — what arrived is kept either way — but the
    views now disagree with the entities, and `validate` is about to report
    that without being able to say why.
    """
    print(f"szsdlc: redrawing after the sync failed: "
          f"{type(exc).__name__}: {exc}", file=sys.stderr)


HANDLERS = {
    "session-start": session_start,
    "user-prompt-submit": user_prompt_submit,
    "pre-edit": pre_edit,
    "post-edit": post_edit,
    "stop": stop,
}


def dispatch(event: str, stream: Any = None, cwd: str | None = None) -> int:
    handler = HANDLERS.get(event)
    if handler is None:  # pragma: no cover - argparse constrains the choices
        return EXIT_OK
    return handler(read_payload(stream), cwd)
