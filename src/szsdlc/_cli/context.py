"""szsdlc context: the orientation pack an agent reads at session start."""

from __future__ import annotations

import argparse

from ..ops import queries
from . import _session
from ._output import emit_json, out


def add_parser(subparsers) -> None:
    ctx = subparsers.add_parser("context", prog="szsdlc context")
    ctx.add_argument("--json", action="store_true", dest="as_json")
    ctx.set_defaults(run=run, op="context", to_params=to_params)


def to_params(args: argparse.Namespace) -> queries.ContextParams:
    return queries.ContextParams()


def run(args: argparse.Namespace) -> int:
    built = queries.context(to_params(args), _session.Session(args.project))
    if args.as_json:
        emit_json({
            "counters": built.counters,
            "in_flight": [{"id": r.id, "status": r.status, "title": r.title}
                          for r in built.in_flight],
            "current_entity": built.current_entity,
            "current_task": built.current_task,
        })
        return 0
    if built.text:
        out(built.text)
    return 0
