"""szsdlc inbox: unrefined captures, oldest first."""

from __future__ import annotations

import argparse

from ..ops import Project, queries
from ..text import clip
from ._output import bound_note, emit_json, id_width, out
from ._parser import DEFAULT_LIMIT


def add_parser(subparsers) -> None:
    inbox = subparsers.add_parser("inbox", prog="szsdlc inbox")
    inbox.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    inbox.add_argument("--json", action="store_true", dest="as_json")
    inbox.set_defaults(run=run, op="inbox", to_params=to_params)


def to_params(args: argparse.Namespace) -> queries.InboxParams:
    return queries.InboxParams(limit=0 if args.as_json else args.limit)


def run(args: argparse.Namespace) -> int:
    params = to_params(args)
    found = queries.inbox(params, Project(args.project))
    if args.as_json:
        emit_json([{"id": r.id, "status": r.status, "age_days": r.age_days,
                    "title": r.title} for r in found.rows])
        return 0
    width = id_width(found.rows)
    for row in found.rows:
        stamp = f"{row.age_days}d" if row.age_days is not None else "-"
        out(f"{row.id:<{width}}  {stamp:>5}  {clip(row.title)}")
    note = bound_note(found.total, params.limit)
    if note:
        out(note)
    return 0
