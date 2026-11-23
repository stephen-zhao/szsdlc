"""szsdlc log: append a dated line to the journal artifact."""

from __future__ import annotations

import argparse
import sys

from ..errors import BadInput
from ..model import relayout
from . import _journal, _session


def add_parser(subparsers) -> None:
    log = subparsers.add_parser("log", prog="szsdlc log")
    log.add_argument("ref")
    log.add_argument("message", nargs="?", default=None)
    log.set_defaults(run=run)


def run(args: argparse.Namespace) -> int:
    session = _session.Session(args.project)
    entity = session.entity(args.ref)

    artifact = entity.type.journal_artifact
    if not artifact:
        raise BadInput(
            f"{entity.type.name} declares no journal artifact, so there is "
            f"nowhere to log to.",
            fix=f"set entity_types.{entity.type.name}.journal_artifact",
        )
    if entity.home is None and entity.type.is_dynamic_layout:
        # The only fix for the refusal would have been `layout … directory`,
        # and a refusal whose fix is one unconditional command is a step nobody
        # should have to type. Wanting a journal *is* earning the directory.
        entity = relayout(session.config, session.ids, entity, "directory")
    if entity.home is None:
        raise BadInput(
            f"a {entity.type.name} is stored as a {entity.layout}, which has "
            f"nowhere to keep {artifact}.",
            fix=f"set entity_types.{entity.type.name}.layout to directory",
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

    _journal.append_journal(entity, artifact, message)
    # C4 — the audit budgets this at zero lines. It runs constantly.
    return 0
