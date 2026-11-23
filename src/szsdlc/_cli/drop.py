"""szsdlc drop: close an idea that went nowhere, with a reason."""

from __future__ import annotations

import argparse
import sys

from ..errors import BadInput
from . import _session, _status
from ._output import out


def add_parser(subparsers) -> None:
    drop = subparsers.add_parser("drop", prog="szsdlc drop")
    drop.add_argument("ref")
    drop.add_argument("--reason", default=None)
    drop.set_defaults(run=run)


def run(args: argparse.Namespace) -> int:
    session = _session.Session(args.project)
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

    from .. import workflow as workflow_module

    # Through the same checked move as `set`: the workflow's edges and the
    # target's gates apply, and so do the effects of reaching a terminal state.
    was_terminal = entity.is_terminal
    workflow_module.move(entity, status)
    if "dropped_reason" in entity.type.fields:
        entity.set_field("dropped_reason", reason)
    reported = [f"{before} → {status}", *_status.after_status_change(session, entity, was_terminal)]
    entity.save()

    out(f"{entity.id.text}: " + "; ".join(reported))
    return 0
