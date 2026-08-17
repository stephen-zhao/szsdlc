"""Argument parsing and dispatch.

Every command here is consumed by an agent, so both halves cost tokens: a
hard-to-produce input causes retry loops, and a verbose output pollutes context
for the rest of the session. The contract in docs/plan.md governs the whole
surface, and three of its rules are enforced right here rather than per command:

**C2/C7** — no traceback ever reaches stderr, argparse usage dumps are
suppressed, and every refusal renders as at most three lines ending in
something the caller can run.

**C5** — human output is the compact default; `--json` is opt-in, because JSON
is the more verbose encoding and must never be what an agent falls into.

**C6** — no listing is unbounded and no truncation is silent.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Callable

from . import __version__, config as config_module
from .errors import EXIT_ERROR, BadInput, InternalError, SzsdlcError
from .ids import IdSpace
from .model import Entity, EntityStore, create_entity, load_all

#: C6 — every listing is bounded, and the bound is the same everywhere.
DEFAULT_LIMIT = 20


# ---------------------------------------------------------------------------
# Parser plumbing
# ---------------------------------------------------------------------------


class Parser(argparse.ArgumentParser):
    """An argparse parser that never prints a usage dump.

    A 30-line usage block is the classic way a single typo costs a thousand
    tokens, so a parse error becomes an ordinary three-line refusal instead.
    """

    def error(self, message: str) -> Any:  # type: ignore[override]
        raise BadInput(f"{self.prog}: {message}.", fix=f"{self.prog} --help")

    def exit(self, status: int = 0, message: str | None = None) -> Any:  # type: ignore[override]
        if status:
            raise BadInput(f"{self.prog}: {(message or 'bad arguments').strip()}",
                           fix=f"{self.prog} --help")
        raise SystemExit(0)


COMMANDS: list[tuple[str, str, str]] = [
    ("capture", "[text]", "capture an idea from an argument, stdin or $EDITOR"),
    ("refine", "<ref> --into TYPE", "spawn a typed entity from an idea"),
    ("inbox", "", "unrefined ideas, oldest first"),
    ("drop", "<ref> --reason ...", "close an idea that went nowhere, with a reason"),
]


def compact_help() -> str:
    """C6/Task 9a — one line per command, deep help behind `<command> --help`."""
    width = max(len(f"{name} {usage}".strip()) for name, usage, _ in COMMANDS)
    lines = [f"szsdlc {__version__} — agentic SDLC framework", "",
             "usage: szsdlc <command> [options]", ""]
    for name, usage, blurb in COMMANDS:
        lines.append(f"  {f'{name} {usage}'.strip():<{width}}  {blurb}")
    lines += ["", "  -C, --project PATH   run against a project other than the cwd",
              "", "szsdlc <command> --help for details."]
    return "\n".join(lines)


def build_parser() -> Parser:
    parser = Parser(prog="szsdlc", add_help=False)
    parser.format_help = compact_help  # type: ignore[assignment]
    parser.add_argument("-h", "--help", action="store_true", dest="show_help")
    parser.add_argument("-V", "--version", action="store_true", dest="show_version")
    parser.add_argument("-C", "--project", metavar="PATH", default=None)

    subparsers = parser.add_subparsers(dest="command", parser_class=Parser)

    capture = subparsers.add_parser("capture", prog="szsdlc capture")
    capture.add_argument("text", nargs="?", default=None)
    capture.add_argument("--type", dest="type_name", default=None,
                         help="intake type, when a project declares more than one")
    capture.set_defaults(run=cmd_capture)

    refine = subparsers.add_parser("refine", prog="szsdlc refine")
    refine.add_argument("ref")
    refine.add_argument("--into", dest="into", required=True)
    refine.add_argument("--title", dest="title", default=None)
    refine.set_defaults(run=cmd_refine)

    inbox = subparsers.add_parser("inbox", prog="szsdlc inbox")
    inbox.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    inbox.add_argument("--json", action="store_true", dest="as_json")
    inbox.set_defaults(run=cmd_inbox)

    drop = subparsers.add_parser("drop", prog="szsdlc drop")
    drop.add_argument("ref")
    drop.add_argument("--reason", default=None)
    drop.set_defaults(run=cmd_drop)

    return parser


# ---------------------------------------------------------------------------
# Shared context
# ---------------------------------------------------------------------------


class Session:
    """Config, ids and entities, loaded once per invocation."""

    def __init__(self, start: str | None = None):
        self.config = config_module.load(start)
        self.ids = IdSpace(self.config)
        self._store: EntityStore | None = None

    @property
    def store(self) -> EntityStore:
        if self._store is None:
            self._store = load_all(self.config, self.ids)
        return self._store

    def entity(self, ref: str) -> Entity:
        entity_id = self.ids.resolve(ref)
        entity = self.store.get(entity_id)
        if entity is None:
            raise BadInput(
                f"{entity_id.text} exists on disk but could not be read.",
                fix="szsdlc validate",
            )
        return entity

    def intake_type(self, name: str | None = None):
        if name:
            entity_type = self.config.type_for(name)
            if not entity_type.intake:
                intake = ", ".join(t.name for t in self.config.types_with("intake"))
                raise BadInput(
                    f"{name}: not an intake type.",
                    fix=f"szsdlc capture --type {intake.split(', ')[0]}" if intake
                        else "set intake: true on a type in .szsdlc/config.yml",
                )
            return entity_type

        candidates = self.config.types_with("intake")
        if len(candidates) == 1:
            return candidates[0]
        if not candidates:
            raise BadInput(
                "this project declares no intake type, so there is nothing to capture into.",
                fix="set intake: true on a type in .szsdlc/config.yml",
            )
        names = ", ".join(t.name for t in candidates)
        raise BadInput(
            f"--type is required: {len(candidates)} intake types are declared.",
            fix=f"szsdlc capture --type {candidates[0].name}",
            see=f"intake types: {names}",
        )


def out(line: str = "") -> None:
    print(line)


def emit_json(payload: Any) -> None:
    print(json.dumps(payload, indent=None, separators=(",", ":"), default=str))


def truncate(rows: list[Any], limit: int) -> tuple[list[Any], str | None]:
    """C6 — bound the output and say so. A silently capped list reads as
    "nothing more to see", which is the one thing it must never mean."""
    if limit <= 0 or len(rows) <= limit:
        return rows, None
    return rows[:limit], f"showing {limit} of {len(rows)} — rerun with --limit 0"


# ---------------------------------------------------------------------------
# capture
# ---------------------------------------------------------------------------


def read_text_input(given: str | None) -> str:
    """An argument, or stdin, or `$EDITOR` — never a prompt.

    Capture cost is the whole point: a thought that has to be quoted, escaped
    or confirmed is a thought that does not get captured.
    """
    if given is not None:
        return given
    if not sys.stdin.isatty():
        return sys.stdin.read()
    return read_from_editor()


def read_from_editor() -> str:
    editor = os.environ.get("SZSDLC_EDITOR") or os.environ.get("EDITOR")
    if not editor:
        raise BadInput(
            "nothing to capture: no text argument, no stdin, and $EDITOR is unset.",
            fix='szsdlc capture "the thought"',
        )
    handle, name = tempfile.mkstemp(suffix=".md", prefix="szsdlc-capture-")
    os.close(handle)
    try:
        subprocess.run([*editor.split(), name], check=False)
        return Path(name).read_text(encoding="utf-8")
    finally:
        os.unlink(name)


def cmd_capture(args: argparse.Namespace) -> int:
    session = Session(args.project)
    entity_type = session.intake_type(args.type_name)

    body = read_text_input(args.text).strip()
    if not body:
        raise BadInput(
            "nothing to capture: the input was empty.",
            fix='szsdlc capture "the thought"',
        )

    entity = create_entity(session.config, session.ids, entity_type,
                           body=body + "\n")
    out(entity.id.text)
    return 0


# ---------------------------------------------------------------------------
# refine
# ---------------------------------------------------------------------------


def cmd_refine(args: argparse.Namespace) -> int:
    session = Session(args.project)
    idea = session.entity(args.ref)

    if not idea.type.intake:
        raise BadInput(
            f"{idea.id.text} is a {idea.type.name}, not an intake entity, so it "
            f"cannot be refined.",
            fix=f"szsdlc new {args.into} --title \"{idea.title}\"",
        )

    target = session.config.type_for(args.into)
    if target.intake:
        typed = next((t.name for t in session.config.entity_types.values()
                      if not t.intake), "work_item")
        raise BadInput(
            f"{target.name} is itself an intake type, so refining into it would "
            f"only make another idea.",
            fix=f"szsdlc refine {idea.id.text} --into {typed}",
        )

    workflow = idea.type.workflow
    if idea.status == workflow.abandoned_status:
        # Dropping is somebody deciding this goes nowhere. Reviving it is a
        # deliberate act, not a side effect of refining.
        raise BadInput(
            f"{idea.id.text} is {idea.status}; a dropped idea is not refined further.",
            fix=f"szsdlc set {idea.id.text} status={workflow.initial}",
        )

    relation = _refined_from_relation(session, idea, target)
    child = create_entity(
        session.config, session.ids, target,
        title=args.title or idea.title,
        relations={relation: idea.id.text} if relation else None,
    )

    # One idea routinely yields several entities, so this is deliberately not a
    # one-shot: refining again from an already-refined idea adds another child.
    # The idea itself is provenance and stays exactly where it is.
    before = idea.status
    after = workflow.completed_status
    if after and before != after:
        idea.set_status(after)
        idea.save()

    # C1 — the resulting state, so no confirming `show` is ever needed.
    out(f"{child.id.text}  ({idea.id.text}: {before} → {idea.status})")
    return 0


def _refined_from_relation(session: Session, idea: Entity, target) -> str | None:
    """The relation the child uses to point back at its idea.

    Found by shape rather than by name: exactly one relation may be authored by
    the target type and points at the intake type. Hardcoding `refined_from`
    would make provenance a framework concept rather than a configured one.
    """
    candidates = [
        relation for relation in session.config.relations_from(target.name)
        if idea.type.name in relation.target_types and relation.is_single
    ]
    if not candidates:
        raise BadInput(
            f"no relation lets a {target.name} point at a {idea.type.name}, so the "
            f"idea could not be recorded as its provenance.",
            fix="declare one under relations: in .szsdlc/config.yml",
        )
    return candidates[0].name


# ---------------------------------------------------------------------------
# inbox
# ---------------------------------------------------------------------------


def _age_days(entity: Entity) -> int | None:
    """Days since this entity's first declared date field.

    Read off the type's own fields rather than hunting the frontmatter for
    anything date-shaped, so a project whose intake type records `raised`
    rather than `captured` still gets an age.
    """
    for name, spec in entity.type.fields.items():
        if spec.type != "date":
            continue
        value = entity.data.get(name)
        if isinstance(value, dt.datetime):
            value = value.date()
        if isinstance(value, dt.date):
            return (dt.date.today() - value).days
    return None


def cmd_inbox(args: argparse.Namespace) -> int:
    session = Session(args.project)

    unrefined = [
        entity for entity in session.store.with_flag("intake")
        if not entity.is_terminal
    ]
    # Oldest first: an inbox sorted newest-first is an inbox whose bottom never
    # gets drained.
    unrefined.sort(key=lambda e: (-(_age_days(e) or 0), e.id.prefix, e.id.number))

    if args.as_json:
        emit_json([{"id": e.id.text, "status": e.status, "age_days": _age_days(e),
                    "title": e.title} for e in unrefined])
        return 0

    rows, note = truncate(unrefined, args.limit)
    for entity in rows:
        age = _age_days(entity)
        stamp = f"{age}d" if age is not None else "-"
        out(f"{entity.id.text}  {stamp:>5}  {entity.title}")
    if note:
        out(note)
    return 0


# ---------------------------------------------------------------------------
# drop
# ---------------------------------------------------------------------------


def cmd_drop(args: argparse.Namespace) -> int:
    session = Session(args.project)
    entity = session.entity(args.ref)

    reason = args.reason
    if reason is None and not sys.stdin.isatty():
        reason = sys.stdin.read()
    reason = (reason or "").strip()
    if not reason:
        raise BadInput(
            f"{entity.id.text}: dropping needs a reason, so the same thought "
            f"arriving twice is recognisable.",
            fix=f'szsdlc drop {entity.id.text} --reason "already covered by WI-0001"',
        )

    status = entity.type.workflow.abandoned_status
    if status is None:
        terminal = ", ".join(entity.type.workflow.terminal_states) or "none"
        raise BadInput(
            f"{entity.type.name} declares no abandoned status, so there is "
            f"nowhere to drop it to.",
            fix=f"mark one terminal state of {entity.type.name} `abandoned: true`",
            see=f"terminal statuses: {terminal}",
        )

    before = entity.status
    if before == status:
        raise BadInput(
            f"{entity.id.text} is already {status}.",
            fix=f"szsdlc show {entity.id.text}",
        )

    if "dropped_reason" in entity.type.fields:
        entity.set_field("dropped_reason", reason)
    entity.set_status(status)
    entity.save()

    out(f"{entity.id.text}: {before} → {status}")
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))

    if args.show_version:
        out(__version__)
        return 0
    if args.show_help:
        out(compact_help())
        return 0
    if not args.command:
        # Diagnostics go to stderr, never stdout, even when they are just help.
        print(compact_help(), file=sys.stderr)
        return 2

    runner: Callable[[argparse.Namespace], int] = args.run
    return runner(args)


def main(argv: Sequence[str] | None = None) -> int:
    """The single place an exception becomes an exit code.

    C7: exit codes are distinct per class so a caller can branch without
    parsing text, and no internal exception escapes as a traceback.
    """
    try:
        return run(argv)
    except SzsdlcError as exc:
        print(exc.render(), file=sys.stderr)
        return exc.exit_code
    except SystemExit as exc:  # --help
        return int(exc.code or 0)
    except InternalError as exc:
        # A broken model invariant is a szsdlc bug, never a project finding.
        print(f"internal error: {exc}", file=sys.stderr)
        print("Fix: report this with the command you ran", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:  # pragma: no cover
        return 130
    except Exception as exc:  # noqa: BLE001 - C7: nothing escapes as a traceback
        print(f"internal error: {type(exc).__name__}: {exc}", file=sys.stderr)
        print("Fix: report this with the command you ran", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
