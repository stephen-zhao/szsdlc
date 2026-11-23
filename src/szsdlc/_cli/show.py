"""szsdlc show: one entity, or a budgeted context bundle."""

from __future__ import annotations

import argparse

from ..frontmatter import jsonify
from . import _queries, _session
from ._output import emit_json, out, relative


def add_parser(subparsers) -> None:
    show = subparsers.add_parser("show", prog="szsdlc show")
    show.add_argument("ref")
    show.add_argument("--context", action="store_true", dest="with_context")
    show.add_argument("--json", action="store_true", dest="as_json")
    show.set_defaults(run=run)


def run(args: argparse.Namespace) -> int:
    from .. import context as context_module

    session = _session.Session(args.project)
    entity = session.entity(args.ref)

    if args.with_context:
        out(context_module.bundle(session.config, _queries.graph(session), entity))
        return 0

    if args.as_json:
        graph = _queries.graph(session)
        emit_json({
            "id": entity.id.text,
            "type": entity.type.name,
            "status": entity.status,
            "title": entity.title,
            "tags": entity.tags,
            "relations": entity.relations,
            # Keyed off the fields the *type* declares, not off whatever this
            # entity's frontmatter happens to hold: the key set is the type's
            # contract, so a declared field nobody wrote reads as null rather
            # than going missing, and a stray hand-written key stays out.
            "fields": jsonify({name: entity.field(name)
                               for name in entity.type.fields}),
            "derived": {k: str(v) for k, v in graph.derived(entity).items()},
            "path": relative(session, entity.path),
        })
        return 0

    out(entity.render().rstrip("\n"))
    return 0
