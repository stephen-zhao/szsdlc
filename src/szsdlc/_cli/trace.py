"""szsdlc trace: relations both ways, back to the idea."""

from __future__ import annotations

import argparse

from . import _queries, _session
from ._output import emit_json, out, truncate
from ._parser import DEFAULT_LIMIT


def add_parser(subparsers) -> None:
    trace = subparsers.add_parser("trace", prog="szsdlc trace")
    trace.add_argument("ref")
    trace.add_argument("--depth", type=int, default=2)
    trace.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    trace.add_argument("--json", action="store_true", dest="as_json")
    trace.set_defaults(run=run)


def run(args: argparse.Namespace) -> int:
    session = _session.Session(args.project)
    graph = _queries.graph(session)
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
