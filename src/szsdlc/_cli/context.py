"""szsdlc context: in-flight work and the counters block."""

from __future__ import annotations

import argparse

from . import _queries, _session
from ._output import emit_json, out


def add_parser(subparsers) -> None:
    ctx = subparsers.add_parser("context", prog="szsdlc context")
    ctx.add_argument("--json", action="store_true", dest="as_json")
    ctx.set_defaults(run=run)


def run(args: argparse.Namespace) -> int:
    from .. import context as context_module

    session = _session.Session(args.project)
    built = context_module.build(session.config, session.store, _queries.graph(session),
                                 _queries.roadmaps(session))
    if args.as_json:
        emit_json({
            "counters": {label: value for label, value, _ in built.counters.rows()},
            "in_flight": [{"id": e.id.text, "status": e.status, "title": e.title}
                          for e in built.in_flight],
            "current_entity": built.current_entity,
            "current_task": built.current_task,
        })
        return 0

    rendered = context_module.render(built)
    if rendered:
        out(rendered)
    return 0
