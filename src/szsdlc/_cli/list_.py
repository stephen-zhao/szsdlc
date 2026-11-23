"""szsdlc list: by type, status, tag, parent, coverage, placement."""

from __future__ import annotations

import argparse

from ..frontmatter import jsonify
from ..text import add_tags, clip
from . import _queries, _session
from ._output import emit_json, id_width, out, truncate
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
    listing.set_defaults(run=run)


def run(args: argparse.Namespace) -> int:
    session = _session.Session(args.project)
    graph = _queries.graph(session)

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
        entities = [e for e in entities if _queries.is_uncovered(graph, e)]
    if args.unscheduled:
        scheduled = {i for r in _queries.roadmaps(session).values() for i in r.all_ids()}
        entities = [e for e in entities
                    if e.type.schedulable and not e.is_terminal
                    and e.id.text not in scheduled]

    entities.sort(key=lambda e: (e.id.prefix, e.id.number))

    if args.as_json:
        # --json is exempt from the limit: it has a programmatic consumer that
        # is not paying context for it.
        emit_json([{"id": e.id.text, "type": e.type.name, "status": e.status,
                    "tags": e.tags, "title": e.title,
                    "fields": jsonify({name: e.field(name)
                                       for name in e.type.fields})}
                   for e in entities])
        return 0

    rows, note = truncate(entities, args.limit)
    width = id_width(rows)
    for entity in rows:
        out(f"{entity.id.text:<{width}}  {entity.status:<10}  {clip(entity.title)}")
    if note:
        out(note)
    return 0
