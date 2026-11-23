"""szsdlc trace: the relation graph around one entity."""

from __future__ import annotations

import argparse

from ..ops import queries
from . import _session
from ._output import bound_note, emit_json, out
from ._parser import DEFAULT_LIMIT


def add_parser(subparsers) -> None:
    trace = subparsers.add_parser("trace", prog="szsdlc trace")
    trace.add_argument("ref")
    trace.add_argument("--depth", type=int, default=2)
    trace.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    trace.add_argument("--json", action="store_true", dest="as_json")
    trace.set_defaults(run=run, op="trace", to_params=to_params)


def to_params(args: argparse.Namespace) -> queries.TraceParams:
    return queries.TraceParams(ref=args.ref, depth=args.depth,
                               limit=0 if args.as_json else args.limit)


def run(args: argparse.Namespace) -> int:
    params = to_params(args)
    traced = queries.trace(params, _session.Session(args.project))
    if args.as_json:
        emit_json([{"source": r.source, "relation": r.relation, "target": r.target}
                   for r in traced.rows])
        return 0
    for row in traced.rows:
        out(f"{row.source} {row.relation} {row.target}")
    note = bound_note(traced.total, params.limit)
    if note:
        out(note)
    return 0
