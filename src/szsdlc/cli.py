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

from . import __version__, config as config_module, hooks as hooks_module
from .errors import EXIT_ERROR, EXIT_INVALID, BadInput, InternalError, SzsdlcError
from .ids import IdSpace
from .model import Entity, EntityStore, create_entity, load_all
from .text import add_tags, clip, nearest, remove_tags

#: C6 — every listing is bounded, and the bound is the same everywhere.
DEFAULT_LIMIT = 20
#: `next` is tighter still: it answers "what now", and an answer of twenty
#: things is not an answer.
NEXT_LIMIT = 10
STANDARDS_LIMIT = 10


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


#: (invocation, one-line blurb). Paired verbs share a row, as the plan's own
#: audit table does — `tag`/`untag` are one idea, and spending two lines of a
#: 25-line budget saying so twice is exactly the waste this budget exists for.
COMMANDS: list[tuple[str, str]] = [
    ("init", "scaffold a project: config, directories, an empty roadmap"),
    ("capture [text]", "capture an idea from an argument, stdin or $EDITOR"),
    ("refine <ref> --into T", "spawn a typed entity from an idea"),
    ("inbox", "unrefined ideas, oldest first"),
    ("drop <ref> --reason ..", "close an idea that went nowhere, with a reason"),
    ("new <TYPE> --title ..", "create a typed entity directly"),
    ("set <ref> field=value", "set scalar fields; enforces the workflow"),
    ("tag|untag <ref> <tag>", "add or remove tags, normalized on write"),
    ("link|unlink a <r> b", "author or remove one edge; inverses are generated"),
    ("convert <ref> <TYPE>", "reclassify, leaving a resolving tombstone"),
    ("log <ref> [msg]", "append a dated line to the journal artifact"),
    ("schedule <ref> -H h", "place on a roadmap; --after/--before/--top"),
    ("unschedule <ref>", "take off the roadmap"),
    ("next", "actionable work, in roadmap order, unblocked"),
    ("show <ref> [--context]", "one entity, or a budgeted context bundle"),
    ("context", "in-flight work and the counters block"),
    ("list [filters]", "by type, status, tag, parent, coverage, placement"),
    ("trace <ref> [--depth]", "relations both ways, back to the idea"),
    ("standards match <path>", "conventions governing these paths"),
    ("sync", "regenerate every view and record; silent, never validates"),
    ("validate", "every consistency rule; silent when clean"),
]

#: Registered but not listed: invoked by hooks.json, never typed by anyone.
#: Spending a line of a 25-line budget on it would cost tokens in every
#: session to document something no reader can use.
HIDDEN_COMMANDS = {"hook"}


def compact_help() -> str:
    """One line per command; deep help lives behind `<command> --help`.

    A 30-line usage block is the classic way a single typo costs a thousand
    tokens, and this is the block a mistyped command would otherwise print.
    """
    width = max(len(invocation) for invocation, _ in COMMANDS)
    lines = ["usage: szsdlc [-C PATH] <command> [options]", ""]
    lines += [f"  {invocation:<{width}}  {blurb}" for invocation, blurb in COMMANDS]
    lines += ["", "szsdlc <command> --help for details."]
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

    init = subparsers.add_parser("init", prog="szsdlc init")
    init.add_argument("--name", default=None)
    init.set_defaults(run=cmd_init)

    new = subparsers.add_parser("new", prog="szsdlc new")
    new.add_argument("type_name", metavar="TYPE")
    new.add_argument("--title", required=True)
    new.set_defaults(run=cmd_new)

    setter = subparsers.add_parser("set", prog="szsdlc set")
    setter.add_argument("ref")
    setter.add_argument("assignments", nargs="+", metavar="field=value")
    setter.set_defaults(run=cmd_set)

    for verb, runner in (("tag", cmd_tag), ("untag", cmd_untag)):
        tagger = subparsers.add_parser(verb, prog=f"szsdlc {verb}")
        tagger.add_argument("ref")
        tagger.add_argument("tags", nargs="+", metavar="tag")
        tagger.set_defaults(run=runner, adding=(verb == "tag"))

    for verb, runner in (("link", cmd_link), ("unlink", cmd_unlink)):
        linker = subparsers.add_parser(verb, prog=f"szsdlc {verb}")
        linker.add_argument("ref")
        linker.add_argument("relation")
        linker.add_argument("target")
        linker.set_defaults(run=runner)

    convert = subparsers.add_parser("convert", prog="szsdlc convert")
    convert.add_argument("ref")
    convert.add_argument("type_name", metavar="TYPE")
    convert.set_defaults(run=cmd_convert)

    log = subparsers.add_parser("log", prog="szsdlc log")
    log.add_argument("ref")
    log.add_argument("message", nargs="?", default=None)
    log.set_defaults(run=cmd_log)

    schedule = subparsers.add_parser("schedule", prog="szsdlc schedule")
    schedule.add_argument("ref")
    schedule.add_argument("--horizon", required=True)
    schedule.add_argument("--after", default=None)
    schedule.add_argument("--before", default=None)
    schedule.add_argument("--top", action="store_true")
    schedule.add_argument("--roadmap", default=None)
    schedule.set_defaults(run=cmd_schedule)

    unschedule = subparsers.add_parser("unschedule", prog="szsdlc unschedule")
    unschedule.add_argument("ref")
    unschedule.add_argument("--roadmap", default=None)
    unschedule.set_defaults(run=cmd_unschedule)

    nxt = subparsers.add_parser("next", prog="szsdlc next")
    nxt.add_argument("--horizon", default=None)
    nxt.add_argument("--parent", default=None)
    nxt.add_argument("--roadmap", default=None)
    nxt.add_argument("--limit", type=int, default=NEXT_LIMIT)
    nxt.add_argument("--json", action="store_true", dest="as_json")
    nxt.set_defaults(run=cmd_next)

    show = subparsers.add_parser("show", prog="szsdlc show")
    show.add_argument("ref")
    show.add_argument("--context", action="store_true", dest="with_context")
    show.add_argument("--json", action="store_true", dest="as_json")
    show.set_defaults(run=cmd_show)

    ctx = subparsers.add_parser("context", prog="szsdlc context")
    ctx.add_argument("--json", action="store_true", dest="as_json")
    ctx.set_defaults(run=cmd_context)

    listing = subparsers.add_parser("list", prog="szsdlc list")
    listing.add_argument("--type", dest="type_name", default=None)
    listing.add_argument("--status", default=None)
    listing.add_argument("--tag", action="append", default=None)
    listing.add_argument("--parent", default=None)
    listing.add_argument("--uncovered", action="store_true")
    listing.add_argument("--unscheduled", action="store_true")
    listing.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    listing.add_argument("--json", action="store_true", dest="as_json")
    listing.set_defaults(run=cmd_list)

    trace = subparsers.add_parser("trace", prog="szsdlc trace")
    trace.add_argument("ref")
    trace.add_argument("--depth", type=int, default=2)
    trace.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    trace.add_argument("--json", action="store_true", dest="as_json")
    trace.set_defaults(run=cmd_trace)

    standards = subparsers.add_parser("standards", prog="szsdlc standards")
    standards.add_argument("subcommand", choices=["match", "list"])
    standards.add_argument("paths", nargs="*")
    standards.add_argument("--limit", type=int, default=STANDARDS_LIMIT)
    standards.add_argument("--json", action="store_true", dest="as_json")
    standards.set_defaults(run=cmd_standards)

    sync = subparsers.add_parser("sync", prog="szsdlc sync")
    sync.add_argument("--verbose", action="store_true")
    sync.set_defaults(run=cmd_sync)

    validate = subparsers.add_parser("validate", prog="szsdlc validate")
    validate.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    validate.add_argument("--verbose", action="store_true")
    validate.add_argument("--json", action="store_true", dest="as_json")
    validate.set_defaults(run=cmd_validate)

    # Hidden: invoked by hooks.json, never typed. It lives on this parser so
    # there is exactly one interpreter-resolution path to get wrong on Windows.
    hook = subparsers.add_parser("hook", prog="szsdlc hook")
    hook.add_argument("event", choices=sorted(hooks_module.HANDLERS))
    hook.set_defaults(run=cmd_hook)

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


def id_width(rows: Sequence[Entity]) -> int:
    """Ids are `<PREFIX>-<NNNN>` and prefixes differ in length, so a listing
    that mixes types has a ragged left edge unless the column is measured.
    Padded per call rather than to a constant, so a single-type listing does
    not pay for the longest prefix the project happens to declare."""
    return max((len(entity.id.text) for entity in rows), default=0)


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
            # Clipped: the title may be a whole captured paragraph, and a
            # refusal that runs to 200 characters has stopped being a fix.
            fix=f"szsdlc new {args.into} --title \"{clip(idea.title)}\"",
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


def cmd_inbox(args: argparse.Namespace) -> int:
    from .context import age_days as _age_days

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
    width = id_width(rows)
    for entity in rows:
        age = _age_days(entity)
        stamp = f"{age}d" if age is not None else "-"
        out(f"{entity.id.text:<{width}}  {stamp:>5}  {clip(entity.title)}")
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
# init
# ---------------------------------------------------------------------------


STARTER_CONFIG = """\
# szsdlc project configuration.
#
# This file is deep-merged over the built-in defaults, so everything below is
# optional and an empty file is already a valid project. Adjust one flag:
#
#   entity_types: {{spike: {{persistent: true}}}}
#
# drop a type you do not want (`entity_types: {{spike: null}}`), or replace a
# whole section outright (`entity_types: {{_replace: true, ...}}`).

project:
  name: {name}
"""


def cmd_init(args: argparse.Namespace) -> int:
    root = Path(args.project or Path.cwd()).resolve()
    config_path = config_module.config_path_for(root)
    if config_path.exists():
        raise BadInput(
            f"{config_path} already exists.",
            fix="szsdlc context",
        )

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_bytes(
        STARTER_CONFIG.format(name=args.name or root.name).encode("utf-8"))

    config = config_module.load(root)
    created = [config_path]

    for entity_type in config.entity_types.values():
        directory = config.dir_for(entity_type)
        directory.mkdir(parents=True, exist_ok=True)
        created.append(directory)

    for directory in (config.roadmaps_dir, config.views_dir, config.records_dir,
                      config.standards_dir, config.templates_dir):
        directory.mkdir(parents=True, exist_ok=True)
        created.append(directory)

    from .roadmap import Roadmap

    for name in config.roadmaps:
        roadmap = Roadmap.load(config, name)
        roadmap.save()
        created.append(roadmap.path)

    for path in created:
        out(str(path.relative_to(root)).replace("\\", "/"))
    return 0


# ---------------------------------------------------------------------------
# new
# ---------------------------------------------------------------------------


def cmd_new(args: argparse.Namespace) -> int:
    session = Session(args.project)
    entity_type = session.config.type_for(args.type_name)
    entity = create_entity(session.config, session.ids, entity_type, title=args.title)
    out(f"{entity.id.text}  {_relative(session, entity.path)}")
    return 0


def _relative(session: Session, path: Path) -> str:
    try:
        return str(path.relative_to(session.config.root)).replace("\\", "/")
    except ValueError:  # pragma: no cover - paths are always under the root
        return str(path)


# ---------------------------------------------------------------------------
# set
# ---------------------------------------------------------------------------


#: C3 — one job per command. These have their own verbs, and composing them on
#: a command line is exactly the input that takes an agent three attempts.
DELEGATED_FIELDS = {
    "relations": "szsdlc link {ref} <relation> <ref>",
    "tags": "szsdlc tag {ref} <tag>",
    "id": "szsdlc convert {ref} <TYPE>",
}


def _coerce(session: Session, entity: Entity, field: str, raw: str) -> Any:
    spec = entity.type.fields.get(field)
    if spec is None:
        return raw
    try:
        if spec.type == "date":
            return dt.date.fromisoformat(raw)
        if spec.type == "integer":
            return int(raw)
        if spec.type == "number":
            return float(raw)
        if spec.type == "boolean":
            if raw.lower() not in {"true", "false"}:
                raise ValueError(raw)
            return raw.lower() == "true"
    except ValueError:
        raise BadInput(
            f"{entity.id.text}: {field}={raw!r} is not a valid {spec.type}.",
            fix=f"szsdlc set {entity.id.text} {field}=<{spec.type}>",
        ) from None
    if spec.type == "array":
        raise BadInput(
            f"set: {field!r} is list-valued and is never edited through `set`.",
            fix=f"edit {field} in the entity's frontmatter directly",
        )
    if spec.enum and raw not in [str(v) for v in spec.enum]:
        raise BadInput(
            f"{entity.id.text}: {field}={raw!r} is not one of "
            f"{', '.join(str(v) for v in spec.enum)}.",
            fix=f"szsdlc set {entity.id.text} {field}={spec.enum[0]}",
        )
    return raw


def _retire_from_roadmaps(session: "Session", entity: Entity) -> list[str]:
    """Take a newly-terminal entity off every roadmap it sits on.

    A roadmap states what is *going to be done*, so a finished entity on one
    is not a plan, it is a leftover — and `validate` says so, as an error.
    Left to the caller that error lands between the two halves of every single
    close: `set … status=done` fails validation until `unschedule` follows it.
    The `Stop` hook blocks on errors, so finishing a work item would end each
    session by refusing to end it.

    Scheduling stays manual in the other direction on purpose. Deciding *when*
    something is worked is a judgment about the whole set; noticing that a
    finished thing is finished is bookkeeping, and bookkeeping is what this
    framework exists to stop charging people for.
    """
    if not entity.is_terminal:
        return []

    from .roadmap import load_all as load_roadmaps

    removed = []
    for roadmap in load_roadmaps(session.config).values():
        if roadmap.remove(entity.id.text):
            roadmap.save()
            removed.append(f"off {roadmap.name}")
    return removed


def cmd_set(args: argparse.Namespace) -> int:
    from . import workflow as workflow_module

    session = Session(args.project)
    entity = session.entity(args.ref)
    settable = ["title", "status", *entity.type.fields]
    reported: list[str] = []

    for assignment in args.assignments:
        field, separator, raw = assignment.partition("=")
        if not separator:
            raise BadInput(
                f"set: {assignment!r} is not field=value.",
                fix=f"szsdlc set {entity.id.text} "
                    f"status={workflow_module.suggested_next(entity)}",
            )

        field = field.strip()
        if field in DELEGATED_FIELDS:
            raise BadInput(
                f"set: {field!r} is not settable.",
                fix=DELEGATED_FIELDS[field].format(ref=entity.id.text),
            )
        if field not in settable:
            suggestion = nearest(field, settable)
            raise BadInput(
                f"set: {entity.type.name} has no field {field!r}"
                + (f"; did you mean {suggestion}?" if suggestion else "."),
                fix=f"szsdlc set {entity.id.text} {suggestion or settable[0]}=<value>",
            )

        if field == "status":
            transition = workflow_module.move(entity, raw)
            reported.append(f"status {transition.before} → {transition.after}")
            reported += _retire_from_roadmaps(session, entity)
        else:
            before = entity.field(field)
            entity.set_field(field, _coerce(session, entity, field, raw))
            reported.append(f"{field} {before} → {entity.field(field)}")

    entity.save()
    # C1 — the resulting state, one line, so no confirming `show` is needed.
    out(f"{entity.id.text}: " + "; ".join(reported))
    return 0


# ---------------------------------------------------------------------------
# tag / untag
# ---------------------------------------------------------------------------


def _apply_tags(args: argparse.Namespace, adding: bool) -> int:
    session = Session(args.project)
    entity = session.entity(args.ref)
    before = entity.tags
    entity.set_tags(add_tags(before, args.tags) if adding
                    else remove_tags(before, args.tags))
    entity.save()
    out(f"{entity.id.text}: {', '.join(entity.tags) or '(no tags)'}")
    return 0


def cmd_tag(args: argparse.Namespace) -> int:
    return _apply_tags(args, adding=True)


def cmd_untag(args: argparse.Namespace) -> int:
    return _apply_tags(args, adding=False)


# ---------------------------------------------------------------------------
# link / unlink
# ---------------------------------------------------------------------------


def _relation_for(session: Session, entity: Entity, name: str):
    allowed = [r.name for r in session.config.relations_from(entity.type.name)]
    if name in allowed:
        return session.config.relations[name]

    inverse = session.config.relation_for_inverse(name)
    if inverse is not None:
        raise BadInput(
            f"link: {name!r} is a generated inverse and is never authored.",
            fix=f"szsdlc link <ref> {inverse.name} {entity.id.text}",
        )

    suggestion = nearest(name, allowed)
    raise BadInput(
        f"link: a {entity.type.name} cannot author {name!r}"
        + (f"; did you mean {suggestion}?" if suggestion else "."),
        fix=f"szsdlc link {entity.id.text} {suggestion or (allowed or ['<relation>'])[0]} <ref>",
    )


def cmd_link(args: argparse.Namespace) -> int:
    session = Session(args.project)
    entity = session.entity(args.ref)
    relation = _relation_for(session, entity, args.relation)
    target = session.entity(args.target)

    if relation.target_types and target.type.name not in relation.target_types:
        raise BadInput(
            f"link: {relation.name} may not point at a {target.type.name}.",
            fix=f"szsdlc list --type {sorted(relation.target_types)[0]}",
            see=f"allowed: {', '.join(sorted(relation.target_types))}",
        )
    if target.id == entity.id:
        raise BadInput(
            f"link: {entity.id.text} cannot {relation.name} itself.",
            fix=f"szsdlc show {entity.id.text}",
        )

    existing = entity.targets(relation.name)
    if target.id.text in existing:
        raise BadInput(
            f"link: {entity.id.text} already {relation.name} {target.id.text}.",
            fix=f"szsdlc show {entity.id.text}",
        )

    updated = [target.id.text] if relation.is_single else [*existing, target.id.text]
    entity.set_relation(relation.name, updated)
    entity.save()

    # C1 — and the generated inverse, so the back-link never has to be looked up.
    out(f"{entity.id.text} {relation.name} {target.id.text}  "
        f"({target.id.text} {relation.inverse} {entity.id.text})")
    return 0


def cmd_unlink(args: argparse.Namespace) -> int:
    session = Session(args.project)
    entity = session.entity(args.ref)
    relation = _relation_for(session, entity, args.relation)

    existing = entity.targets(relation.name)
    # Resolve tolerantly, but fall back to the literal text: unlinking a
    # dangling edge is exactly when the target cannot be resolved.
    try:
        wanted = session.ids.parse(args.target).text
    except BadInput:
        wanted = args.target

    if wanted not in existing:
        raise BadInput(
            f"unlink: {entity.id.text} does not {relation.name} {wanted}.",
            fix=f"szsdlc show {entity.id.text}",
            see=f"{relation.name}: {', '.join(existing) or '(none)'}",
        )

    entity.set_relation(relation.name, [t for t in existing if t != wanted])
    entity.save()
    remaining = entity.targets(relation.name)
    out(f"{entity.id.text} {relation.name}: {', '.join(remaining) or '(none)'}")
    return 0


# ---------------------------------------------------------------------------
# convert
# ---------------------------------------------------------------------------


def cmd_convert(args: argparse.Namespace) -> int:
    import shutil

    session = Session(args.project)
    entity = session.entity(args.ref)
    target_type = session.config.type_for(args.type_name)

    if target_type.name == entity.type.name:
        raise BadInput(
            f"convert: {entity.id.text} is already a {target_type.name}.",
            fix=f"szsdlc show {entity.id.text}",
        )
    if entity.home is not None and not target_type.carries_artifacts:
        extra = [p.name for p in entity.artifact_files()]
        if extra:
            raise BadInput(
                f"convert: a {target_type.name} has nowhere to put an artifact, "
                f"but {entity.id.text} carries {', '.join(extra)}.",
                fix=f"remove those artifacts, then rerun",
            )

    new_id = session.ids.next_id(target_type)
    # A status that exists in both workflows is kept; otherwise the entity
    # restarts, because a status from another workflow means nothing here.
    status = (entity.status if entity.status in target_type.workflow.states
              else target_type.workflow.initial)

    child = create_entity(
        session.config, session.ids, target_type,
        title=entity.title, body=entity.body, status=status,
        tags=entity.tags,
        relations={k: (v[0] if session.config.relations[k].is_single else v)
                   for k, v in entity.relations.items()
                   if k in {r.name for r in session.config.relations_from(target_type.name)}},
    )
    assert child.id == new_id

    if entity.home is not None and child.home is not None:
        for artifact in entity.artifact_files():
            shutil.copy2(artifact, child.home / artifact.name)

    entity.delete()

    tombstones = session.ids.tombstones
    tombstones.record(entity.id.text, child.id.text)
    tombstones.save()

    out(f"{child.id.text}  ({entity.id.text} → {child.id.text}, status {status})")
    out(f"{entity.id.text} is tombstoned; existing references still resolve.")
    return 0


# ---------------------------------------------------------------------------
# log
# ---------------------------------------------------------------------------


def cmd_log(args: argparse.Namespace) -> int:
    session = Session(args.project)
    entity = session.entity(args.ref)

    artifact = entity.type.journal_artifact
    if not artifact or entity.home is None:
        raise BadInput(
            f"{entity.type.name} declares no journal artifact, so there is "
            f"nowhere to log to.",
            fix=f"set entity_types.{entity.type.name}.journal_artifact",
        )

    message = args.message
    if message is None and not sys.stdin.isatty():
        message = sys.stdin.read()
    message = (message or "").strip()
    if not message:
        raise BadInput(
            f"log: nothing to write.",
            fix=f'szsdlc log {entity.id.text} "what happened"',
        )

    path = entity.artifact_path(artifact)
    assert path is not None
    stamp = dt.date.today().isoformat()
    body = "".join(f"- {stamp} {line}\n" for line in message.splitlines() if line.strip())

    existing = path.read_bytes().decode("utf-8") if path.is_file() else ""
    if existing and not existing.endswith("\n"):
        existing += "\n"
    path.write_bytes((existing + body).encode("utf-8"))
    # C4 — the audit budgets this at zero lines. It runs constantly.
    return 0


# ---------------------------------------------------------------------------
# schedule / unschedule
# ---------------------------------------------------------------------------


def cmd_schedule(args: argparse.Namespace) -> int:
    from .roadmap import Scheduler

    session = Session(args.project)
    scheduler = Scheduler(session.config, session.store, args.roadmap, session.ids)
    placement = scheduler.schedule(args.ref, args.horizon, after=args.after,
                                   before=args.before, top=args.top)
    out(str(placement))
    return 0


def cmd_unschedule(args: argparse.Namespace) -> int:
    from .roadmap import Scheduler

    session = Session(args.project)
    scheduler = Scheduler(session.config, session.store, args.roadmap, session.ids)
    entity_id = session.ids.resolve(args.ref)
    if not scheduler.unschedule(entity_id):
        raise BadInput(
            f"{entity_id.text} is not on roadmap {scheduler.roadmap.name!r}.",
            fix=f"szsdlc schedule {entity_id.text} --horizon "
                f"{scheduler.roadmap.spec.horizons[-1]}",
        )
    out(f"{entity_id.text}: off {scheduler.roadmap.name}")
    return 0


# ---------------------------------------------------------------------------
# Queries
#
# Design rule — counters in context, details on demand. Every listing here is
# bounded by default and every truncation is printed, because a silently capped
# list reads as "nothing more to see".
# ---------------------------------------------------------------------------


def _graph(session: Session):
    from .graph import Graph

    return Graph(session.config, session.store, session.ids)


def _roadmaps(session: Session, name: str | None = None):
    from .roadmap import Roadmap, load_all as load_roadmaps

    if name:
        if name not in session.config.roadmaps:
            raise BadInput(
                f"{name!r} is not a configured roadmap.",
                fix=f"use one of: {', '.join(sorted(session.config.roadmaps))}",
            )
        return {name: Roadmap.load(session.config, name)}
    return load_roadmaps(session.config)


def _roadmap_order(roadmaps: dict, horizon: str | None) -> dict[str, int]:
    """id -> sort key, following horizon order then position within it.

    Ordering by the roadmap rather than by a per-entity field is the whole
    reason there is no `priority`: sequence is a property of the list.
    """
    order: dict[str, int] = {}
    position = 0
    for roadmap in roadmaps.values():
        for bucket in roadmap.spec.horizons:
            if horizon and bucket != horizon:
                continue
            for id_text in roadmap.entries(bucket):
                order.setdefault(id_text, position)
                position += 1
    return order


def _blocked(session: Session, graph, entity: Entity) -> bool:
    """True while any dependency has not reached a terminal status.

    Saying so here is cheaper than letting somebody start the work and find
    out. An unresolvable dependency does not block — it is a validate finding,
    and treating a typo as a blocker would hide the queue rather than the typo.
    """
    for target in graph.targets(entity.id, "depends_on"):
        if not target.resolved:
            continue
        dependency = session.store.get(target.entity_id)
        if dependency is not None and not dependency.is_terminal:
            return True
    return False


def cmd_next(args: argparse.Namespace) -> int:
    session = Session(args.project)
    graph = _graph(session)
    roadmaps = _roadmaps(session, args.roadmap)
    order = _roadmap_order(roadmaps, args.horizon)

    parent_id = session.ids.resolve(args.parent).text if args.parent else None
    parent_relation = session.config.data["parent_relation"]

    candidates = []
    for entity in session.store.with_flag("actionable"):
        if entity.is_terminal:
            continue
        if args.horizon and entity.id.text not in order:
            continue
        if parent_id and parent_id not in entity.targets(parent_relation):
            continue
        if _blocked(session, graph, entity):
            continue
        candidates.append(entity)

    # Scheduled work first, in roadmap sequence; everything else after, by id.
    candidates.sort(key=lambda e: (order.get(e.id.text, len(order)),
                                   e.id.prefix, e.id.number))

    if args.as_json:
        emit_json([{"id": e.id.text, "status": e.status, "title": e.title,
                    "scheduled": e.id.text in order} for e in candidates])
        return 0

    rows, note = truncate(candidates, args.limit)
    width = id_width(rows)
    for entity in rows:
        out(f"{entity.id.text:<{width}}  {entity.status:<10}  {clip(entity.title)}")
    if note:
        out(note)
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    from . import context as context_module

    session = Session(args.project)
    entity = session.entity(args.ref)

    if args.with_context:
        out(context_module.bundle(session.config, _graph(session), entity))
        return 0

    if args.as_json:
        graph = _graph(session)
        emit_json({
            "id": entity.id.text,
            "type": entity.type.name,
            "status": entity.status,
            "title": entity.title,
            "tags": entity.tags,
            "relations": entity.relations,
            "derived": {k: str(v) for k, v in graph.derived(entity).items()},
            "path": _relative(session, entity.path),
        })
        return 0

    out(entity.render().rstrip("\n"))
    return 0


def cmd_context(args: argparse.Namespace) -> int:
    from . import context as context_module

    session = Session(args.project)
    built = context_module.build(session.config, session.store, _graph(session),
                                 _roadmaps(session))
    if args.as_json:
        emit_json({
            "counters": {label: value for label, value, _ in built.counters.rows()},
            "in_flight": [{"id": e.id.text, "status": e.status, "title": e.title}
                          for e in built.in_flight],
            "current_entity": built.current_entity,
            "current_task": built.current_task,
        })
        return 0

    rendered = context_module.render(built)
    if rendered:
        out(rendered)
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    session = Session(args.project)
    graph = _graph(session)

    entities = list(session.store)

    if args.type_name:
        session.config.type_for(args.type_name)  # refuses with the known types
        entities = [e for e in entities if e.type.name == args.type_name]
    if args.status:
        entities = [e for e in entities if e.status == args.status]
    for tag in args.tag or []:
        normalized = add_tags([], [tag])
        entities = [e for e in entities if set(normalized) <= set(e.tags)]
    if args.parent:
        parent_id = session.ids.resolve(args.parent).text
        relation = session.config.data["parent_relation"]
        entities = [e for e in entities if parent_id in e.targets(relation)]
    if args.uncovered:
        entities = [e for e in entities if _is_uncovered(graph, e)]
    if args.unscheduled:
        scheduled = {i for r in _roadmaps(session).values() for i in r.all_ids()}
        entities = [e for e in entities
                    if e.type.schedulable and not e.is_terminal
                    and e.id.text not in scheduled]

    entities.sort(key=lambda e: (e.id.prefix, e.id.number))

    if args.as_json:
        # --json is exempt from the limit: it has a programmatic consumer that
        # is not paying context for it.
        emit_json([{"id": e.id.text, "type": e.type.name, "status": e.status,
                    "tags": e.tags, "title": e.title} for e in entities])
        return 0

    rows, note = truncate(entities, args.limit)
    width = id_width(rows)
    for entity in rows:
        out(f"{entity.id.text:<{width}}  {entity.status:<10}  {clip(entity.title)}")
    if note:
        out(note)
    return 0


def _is_uncovered(graph, entity: Entity) -> bool:
    """True when a `has_incoming` derived attribute of this entity is false.

    Expressed through the declared derivation rather than by naming
    requirements, so a project with a differently named definitional type gets
    `--uncovered` for free.
    """
    for name, declaration in entity.type.derived.items():
        if declaration.kind == "has_incoming" and not graph.derive(entity, name):
            return True
    return False


def cmd_trace(args: argparse.Namespace) -> int:
    session = Session(args.project)
    graph = _graph(session)
    entity_id = session.ids.resolve(args.ref)

    edges = graph.trace(entity_id, depth=args.depth)

    if args.as_json:
        emit_json([{"source": e.source.text, "relation": e.kind,
                    "target": e.target.label} for e in edges])
        return 0

    rows, note = truncate(edges, args.limit)
    for edge in rows:
        out(f"{edge.source.text} {edge.kind} {edge.target.label}")
    if note:
        out(note)
    return 0


def cmd_standards(args: argparse.Namespace) -> int:
    from . import standards as standards_module

    session = Session(args.project)
    loaded = standards_module.load(session.config)

    if args.subcommand == "list":
        found = loaded
    else:
        if not args.paths:
            raise BadInput(
                "standards match: no paths given.",
                fix="szsdlc standards match <path>",
            )
        found = standards_module.matching(session.config, args.paths, loaded)

    if args.as_json:
        emit_json([{"name": s.name, "path": _relative(session, s.path),
                    "applies_to": list(s.applies_to), "body": s.body}
                   for s in found])
        return 0

    rows, note = truncate(found, args.limit)
    for standard in rows:
        out(standard.name)
    if note:
        out(note)
    return 0


# ---------------------------------------------------------------------------
# sync
# ---------------------------------------------------------------------------


def cmd_sync(args: argparse.Namespace) -> int:
    """Regenerate every view and record. Never validates.

    C4 — silent on the happy path. This runs after every file write and at
    every turn end; output there has no consumer and would reach context.
    """
    from . import render as render_module

    session = Session(args.project)
    changed = render_module.sync(session.config, session.store, _graph(session),
                                 _roadmaps(session))
    if args.verbose:
        for item in changed:
            out(_relative(session, item.path))
    return 0


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


def cmd_validate(args: argparse.Namespace) -> int:
    """Every consistency rule. Silent when clean; findings when not.

    The single enforcement point: `sync` runs constantly and tolerantly during
    a turn, this runs once at the end and strictly.
    """
    from . import validate as validate_module

    session = Session(args.project)
    findings = validate_module.run(session.config, session.store, _graph(session),
                                   _roadmaps(session))
    errors, warnings = validate_module.summary(findings)

    if args.as_json:
        emit_json([{"level": f.level, "kind": f.kind, "ref": f.ref,
                    "message": f.message, "fix": f.fix} for f in findings])
        return EXIT_INVALID if errors else 0

    if not findings:
        # C4 — nothing on the happy path. This runs at every turn end.
        if args.verbose:
            out("clean")
        return 0

    rows, note = truncate(findings, args.limit)
    for finding in rows:
        out(f"{finding.level:<7} {finding.ref}  {finding.message}"
            + (f" — {finding.fix}" if finding.fix else ""))
    if note:
        out(note)
    out(f"{errors} error{'s' if errors != 1 else ''}, "
        f"{warnings} warning{'s' if warnings != 1 else ''}")

    # Exit 4, not 1: C7 gives validation failure its own code so a caller can
    # branch on it without parsing text.
    return EXIT_INVALID if errors else 0


# ---------------------------------------------------------------------------
# hook (hidden)
# ---------------------------------------------------------------------------


def cmd_hook(args: argparse.Namespace) -> int:
    return hooks_module.dispatch(args.event, cwd=args.project)


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


def _force_utf8_streams() -> None:
    """Make output encodable everywhere before anything tries to write.

    On Windows the console encoding is the ANSI codepage, not UTF-8, so a
    single `->` arrow in a transition line raises UnicodeEncodeError from
    inside `print` -- after the mutation has already been written to disk.
    That reports failure for work that succeeded, which is the worst shape a
    failure can take. Python 3.15 defaults to UTF-8 mode; until then, ask.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:  # a pipe someone replaced; nothing to do
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):  # pragma: no cover - already detached
            pass


def main(argv: Sequence[str] | None = None) -> int:
    """The single place an exception becomes an exit code.

    C7: exit codes are distinct per class so a caller can branch without
    parsing text, and no internal exception escapes as a traceback.
    """
    _force_utf8_streams()
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
