"""szsdlc standards: the standards that apply, by path or all of them."""

from __future__ import annotations

import argparse

from ..ops import queries
from . import _session
from ._output import bound_note, emit_json, out
from ._parser import STANDARDS_LIMIT


def add_parser(subparsers) -> None:
    standards = subparsers.add_parser("standards", prog="szsdlc standards")
    standards.add_argument("subcommand", choices=["match", "list"])
    standards.add_argument("paths", nargs="*")
    standards.add_argument("--limit", type=int, default=STANDARDS_LIMIT)
    standards.add_argument("--json", action="store_true", dest="as_json")
    standards.set_defaults(run=run, op="standards", to_params=to_params)


def to_params(args: argparse.Namespace) -> queries.StandardsParams:
    return queries.StandardsParams(mode=args.subcommand, paths=tuple(args.paths),
                                   limit=0 if args.as_json else args.limit)


def run(args: argparse.Namespace) -> int:
    params = to_params(args)
    found = queries.standards(params, _session.Session(args.project))
    if args.as_json:
        emit_json([{"name": r.name, "path": r.path, "applies_to": list(r.applies_to),
                    "body": r.body} for r in found.rows])
        return 0
    for row in found.rows:
        out(row.name)
    note = bound_note(found.total, params.limit)
    if note:
        out(note)
    return 0
