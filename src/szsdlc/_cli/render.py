"""szsdlc render: regenerate every view and record. Never validates, never reaches a remote."""

from __future__ import annotations

import argparse

from ..ops import store
from . import _session
from ._output import out


def add_parser(subparsers) -> None:
    render = subparsers.add_parser("render", prog="szsdlc render")
    render.add_argument("--verbose", action="store_true")
    render.set_defaults(run=run, op="render", to_params=to_params)


def to_params(args: argparse.Namespace) -> store.RenderParams:
    return store.RenderParams()


def run(args: argparse.Namespace) -> int:
    """Rendering is a one-way derivation: entities in, generated markdown out. It
    is the whole of `szsdlc render`, and the first step of `sync`, because
    nothing should record or send a view that is out of date.

    C4 — silent on the happy path. This runs after every file write and at
    every turn end; output there has no consumer and would reach context.
    """
    rendered = store.render(to_params(args), _session.Session(args.project))
    if args.verbose:
        for path in rendered.changed:
            out(path)
    return 0
