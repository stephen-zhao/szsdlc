"""szsdlc new: one entity, at the workflow's initial status or at --status."""

from __future__ import annotations

import argparse

from ..ops import entities
from . import _session
from ._output import out


def add_parser(subparsers) -> None:
    new = subparsers.add_parser("new", prog="szsdlc new")
    new.add_argument("type_name", metavar="TYPE")
    new.add_argument("--title", required=True)
    new.add_argument("--status", default=None)
    new.set_defaults(run=run, op="new", to_params=to_params)


def to_params(args: argparse.Namespace) -> entities.NewParams:
    return entities.NewParams(type=args.type_name, title=args.title, status=args.status)


def run(args: argparse.Namespace) -> int:
    """`--status` is the import path for existing history: checked against the
    workflow, not its entry gates, and the line names what is owed (C1)."""
    made = entities.new(to_params(args), _session.Session(args.project))
    line = f"{made.id}  {made.path}"
    if made.imported:
        line += f"  {made.status}" + (f"; owes: {'; '.join(made.owes)}" if made.owes else "")
    out(line)
    return 0
