"""szsdlc next: actionable work, in roadmap order, unblocked."""

from __future__ import annotations

import argparse

from ..text import clip
from . import _queries, _session
from ._output import emit_json, id_width, out, truncate
from ._parser import NEXT_LIMIT


def add_parser(subparsers) -> None:
    nxt = subparsers.add_parser("next", prog="szsdlc next")
    nxt.add_argument("--horizon", default=None)
    nxt.add_argument("--parent", default=None)
    nxt.add_argument("--roadmap", default=None)
    nxt.add_argument("--limit", type=int, default=NEXT_LIMIT)
    nxt.add_argument("--json", action="store_true", dest="as_json")
    nxt.set_defaults(run=run)


def run(args: argparse.Namespace) -> int:
    session = _session.Session(args.project)
    graph = _queries.graph(session)
    roadmaps = _queries.roadmaps(session, args.roadmap)
    order = _queries.roadmap_order(roadmaps, args.horizon)

    parent_id = session.ids.resolve(args.parent).text if args.parent else None
    parent_relation = session.config.data["parent_relation"]

    candidates = []
    for entity in session.store.with_flag("actionable"):
        if entity.is_terminal:
            continue
        if args.horizon and entity.id.text not in order:
            continue
        if parent_id and parent_id not in entity.targets(parent_relation):
            continue
        if _queries.blocked(session, graph, entity):
            continue
        candidates.append(entity)

    # Scheduled work first, in roadmap sequence; everything else after, by id.
    candidates.sort(key=lambda e: (order.get(e.id.text, len(order)),
                                   e.id.prefix, e.id.number))

    if args.as_json:
        emit_json([{"id": e.id.text, "status": e.status, "title": e.title,
                    "scheduled": e.id.text in order} for e in candidates])
        return 0

    rows, note = truncate(candidates, args.limit)
    width = id_width(rows)
    for entity in rows:
        out(f"{entity.id.text:<{width}}  {entity.status:<10}  {clip(entity.title)}")
    if note:
        out(note)
    return 0
