"""szsdlc show: one entity, as its file, as JSON, or with its context bundle."""

from __future__ import annotations

import argparse

from ..frontmatter import jsonify
from ..ops import Project, queries
from ._output import emit_json, out


def add_parser(subparsers) -> None:
    show = subparsers.add_parser("show", prog="szsdlc show")
    show.add_argument("ref")
    show.add_argument("--context", action="store_true", dest="with_context")
    show.add_argument("--json", action="store_true", dest="as_json")
    show.set_defaults(run=run, op="show", to_params=to_params)


def to_params(args: argparse.Namespace) -> queries.ShowParams:
    return queries.ShowParams(ref=args.ref, context=args.with_context)


def run(args: argparse.Namespace) -> int:
    shown = queries.show(to_params(args), Project(args.project))
    if args.with_context:
        out(shown.bundle)
        return 0
    if args.as_json:
        emit_json({
            "id": shown.id,
            "type": shown.type,
            "status": shown.status,
            "title": shown.title,
            "tags": list(shown.tags),
            "relations": shown.relations,
            "fields": jsonify(shown.fields),
            "derived": shown.derived,
            "path": shown.path,
        })
        return 0
    out(shown.text.rstrip("\n"))
    return 0
