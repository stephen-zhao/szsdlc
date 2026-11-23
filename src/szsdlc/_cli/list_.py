"""szsdlc list: entities, filtered; bounded unless --json."""

from __future__ import annotations

import argparse

from ..frontmatter import jsonify
from ..ops import Project, queries
from ..text import clip
from ._output import bound_note, emit_json, id_width, out
from ._parser import DEFAULT_LIMIT


def add_parser(subparsers) -> None:
    listing = subparsers.add_parser("list", prog="szsdlc list")
    listing.add_argument("--type", dest="type_name", default=None)
    listing.add_argument("--status", default=None)
    listing.add_argument("--tag", action="append", default=None)
    listing.add_argument("--parent", default=None)
    listing.add_argument("--uncovered", action="store_true")
    listing.add_argument("--unscheduled", action="store_true")
    listing.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    listing.add_argument("--json", action="store_true", dest="as_json")
    listing.set_defaults(run=run, op="list", to_params=to_params)


def to_params(args: argparse.Namespace) -> queries.ListParams:
    # --json is exempt from the limit: it has a programmatic consumer that is
    # not paying context for it.
    return queries.ListParams(
        type=args.type_name, status=args.status, tags=tuple(args.tag or ()),
        parent=args.parent, uncovered=args.uncovered, unscheduled=args.unscheduled,
        limit=0 if args.as_json else args.limit,
    )


def run(args: argparse.Namespace) -> int:
    params = to_params(args)
    listed = queries.list_(params, Project(args.project))
    if args.as_json:
        emit_json([{"id": r.id, "type": r.type, "status": r.status, "tags": list(r.tags),
                    "title": r.title, "fields": jsonify(r.fields)} for r in listed.rows])
        return 0
    width = id_width(listed.rows)
    for row in listed.rows:
        out(f"{row.id:<{width}}  {row.status:<10}  {clip(row.title)}")
    note = bound_note(listed.total, params.limit)
    if note:
        out(note)
    return 0
