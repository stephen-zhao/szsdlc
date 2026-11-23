"""szsdlc render: regenerate every view and record; silent, never validates."""

from __future__ import annotations

import argparse

from . import _queries, _session
from ._output import out, relative


def add_parser(subparsers) -> None:
    render = subparsers.add_parser("render", prog="szsdlc render")
    render.add_argument("--verbose", action="store_true")
    render.set_defaults(run=run)


def run(args: argparse.Namespace) -> int:
    """Regenerate every view and record. Never validates, never reaches a remote.

    Rendering is a one-way derivation: entities in, generated markdown out. It
    is the whole of `szsdlc render`, and the first step of `commit` and `sync`,
    because neither should record or send a view that is out of date.

    C4 — silent on the happy path. This runs after every file write and at
    every turn end; output there has no consumer and would reach context.
    """
    from .. import render as render_module

    session = _session.Session(args.project)
    changed = render_module.render(session.config, session.store, _queries.graph(session),
                                   _queries.roadmaps(session))
    if args.verbose:
        for item in changed:
            out(relative(session, item.path))
    return 0
