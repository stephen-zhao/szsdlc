"""szsdlc layout: change what an entity occupies on disk."""

from __future__ import annotations

import argparse

from ..config import LAYOUTS
from ..ops import Project, entities
from ._output import out


def add_parser(subparsers) -> None:
    layout = subparsers.add_parser("layout", prog="szsdlc layout")
    layout.add_argument("ref")
    layout.add_argument("layout", metavar="LAYOUT", choices=list(LAYOUTS))
    layout.set_defaults(run=run, op="layout", to_params=to_params)


def to_params(args: argparse.Namespace) -> entities.LayoutParams:
    return entities.LayoutParams(ref=args.ref, layout=args.layout)


def run(args: argparse.Namespace) -> int:
    laid = entities.layout(to_params(args), Project(args.project))
    out(f"{laid.id}  ({laid.before} → {laid.after}) {laid.path}")
    return 0
