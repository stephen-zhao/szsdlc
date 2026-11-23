"""szsdlc next: actionable work, in roadmap order, unblocked."""

from __future__ import annotations

import argparse

from ..ops import queries
from ..text import clip
from . import _session
from ._output import bound_note, emit_json, id_width, out
from ._parser import NEXT_LIMIT


def add_parser(subparsers) -> None:
    nxt = subparsers.add_parser("next", prog="szsdlc next")
    nxt.add_argument("--horizon", default=None)
    nxt.add_argument("--parent", default=None)
    nxt.add_argument("--roadmap", default=None)
    nxt.add_argument("--limit", type=int, default=NEXT_LIMIT)
    nxt.add_argument("--json", action="store_true", dest="as_json")
    nxt.set_defaults(run=run, op="next", to_params=to_params)


def to_params(args: argparse.Namespace) -> queries.NextParams:
    return queries.NextParams(horizon=args.horizon, parent=args.parent, roadmap=args.roadmap,
                              limit=0 if args.as_json else args.limit)


def run(args: argparse.Namespace) -> int:
    params = to_params(args)
    found = queries.next_(params, _session.Session(args.project))
    if args.as_json:
        emit_json([{"id": r.id, "status": r.status, "title": r.title,
                    "scheduled": r.scheduled} for r in found.rows])
        return 0
    width = id_width(found.rows)
    for row in found.rows:
        out(f"{row.id:<{width}}  {row.status:<10}  {clip(row.title)}")
    note = bound_note(found.total, params.limit)
    if note:
        out(note)
    return 0
