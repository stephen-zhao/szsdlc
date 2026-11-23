"""What a status change implies beyond the status, for every command that makes one."""

from __future__ import annotations

import argparse
import datetime as dt
import shlex
import sys
from typing import Any

from ..errors import BadInput, SzsdlcError
from ..model import Entity, load_all
from . import _journal, _queries, _session


def after_status_change(session: "Session", entity: Entity, was_terminal: bool) -> list[str]:
    """What a status change implies beyond the status, for every command that makes one.

    Reaching a terminal state retires the item from every roadmap; leaving one
    is reported. `set` and `drop` both come through here, so a command that
    changes status cannot skip it, which is how `drop` used to leave dropped
    items scheduled (WI-0053).
    """
    notes = retire_from_roadmaps(session, entity)
    if was_terminal and not entity.is_terminal:
        notes += reopen_notes(session, entity)
    return notes


def retire_from_roadmaps(session: "Session", entity: Entity) -> list[str]:
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

    from ..roadmap import load_all as load_roadmaps

    removed = []
    for roadmap in load_roadmaps(session.config).values():
        if roadmap.remove(entity.id.text):
            roadmap.save()
            removed.append(f"off {roadmap.name}")
    return removed


def force_reason(args: argparse.Namespace, entity: Entity) -> str | None:
    """The reason a `set --force` gives, or a refusal; None without `--force`.

    Read before anything is written, so a refusal never leaves half a change.
    `--force` is a person overriding the workflow, and the reason is what
    makes the override legible later — so it is required, read from stdin
    when not given (as `drop` does), and never defaulted.
    """
    ref = entity.id.text
    statuses = [a for a in args.assignments if a.partition("=")[0].strip() == "status"]
    if not args.force:
        if args.reason is not None:
            raise BadInput(
                f"set: --reason explains a forced move, and there is no --force.",
                fix=f"szsdlc set {ref} {' '.join(map(shlex.quote, args.assignments))}",
            )
        return None
    if not statuses:
        raise BadInput(
            f"set: --force applies only to a status= assignment, and there is none.",
            fix=f'szsdlc set {ref} status=<status> --force --reason "why"',
        )

    reason = args.reason
    if reason is None and not sys.stdin.isatty():
        reason = sys.stdin.read()
    reason = " ".join((reason or "").split())
    if not reason:
        raise BadInput(
            f"{ref}: a forced status change needs a reason, so the exception "
            f"is legible later.",
            fix=f"szsdlc set {ref} {' '.join(map(shlex.quote, args.assignments))} "
                f'--force --reason "why this move"',
        )
    return reason


def record_forced_move(entity: Entity, transition: Any, reason: str) -> None:
    """The latest forced move in `forced_reason`, the history in the journal.

    Both are opt-in by declaration, never by type name: a type that declares
    no `forced_reason` gets no field, and one with no journal — or a layout
    with nowhere to keep one — keeps no history. A forced move never relays
    an entity out to make room; that would be a side effect nobody asked for.
    """
    stamp = dt.date.today().isoformat()
    move = f"{transition.before} → {transition.after}: {reason}"
    if "forced_reason" in entity.type.fields:
        entity.set_field("forced_reason", f"{stamp} {move}")
    artifact = entity.type.journal_artifact
    if artifact and entity.artifact_path(artifact) is not None:
        _journal.append_journal(entity, artifact, f"forced {move}")


def reopen_notes(session: _session.Session, entity: Entity) -> list[str]:
    """What reaching a terminal status had done, now that it is undone.

    Only reported, never reversed. Scheduling is a judgment about the whole
    set, so a reopened item is not put back on a roadmap; dependents are
    blocked again by construction, since only a terminal dependency counts as
    met; a closed parent is pointed out, not reopened.
    """
    notes: list[str] = []
    ref = entity.id.text

    if entity.type.flag("schedulable") and session.config.roadmaps:
        spec = next(iter(session.config.roadmaps.values()))
        named = (f" --roadmap {spec.name}" if len(session.config.roadmaps) > 1 else "")
        notes.append(f"unscheduled — szsdlc schedule {ref} "
                     f"--horizon {spec.horizons[0]}{named}")

    graph = _queries.graph(session)
    blocked = [d.id.text for d in graph.sources(entity, _queries.DEPENDENCY_RELATION)
               if not d.is_terminal]
    if blocked:
        notes.append(f"blocks again: {', '.join(blocked)}")

    parent_ref = entity.parent()
    if parent_ref:
        try:
            parent = session.store.get(session.ids.resolve(parent_ref))
        except SzsdlcError:
            parent = None
        if parent is not None and parent.is_terminal:
            notes.append(f"parent {parent.id.text} is {parent.status}")
    return notes
