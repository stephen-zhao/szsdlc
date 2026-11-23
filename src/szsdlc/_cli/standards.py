"""szsdlc standards: conventions governing these paths."""

from __future__ import annotations

import argparse

from ..errors import BadInput
from . import _session
from ._output import emit_json, out, relative, truncate
from ._parser import STANDARDS_LIMIT


def add_parser(subparsers) -> None:
    standards = subparsers.add_parser("standards", prog="szsdlc standards")
    standards.add_argument("subcommand", choices=["match", "list"])
    standards.add_argument("paths", nargs="*")
    standards.add_argument("--limit", type=int, default=STANDARDS_LIMIT)
    standards.add_argument("--json", action="store_true", dest="as_json")
    standards.set_defaults(run=run)


def run(args: argparse.Namespace) -> int:
    from .. import standards as standards_module

    session = _session.Session(args.project)
    loaded = standards_module.load(session.config)

    if args.subcommand == "list":
        found = loaded
    else:
        if not args.paths:
            raise BadInput(
                "standards match: no paths given.",
                fix="szsdlc standards match <path>",
            )
        found = standards_module.matching(session.config, args.paths, loaded)

    if args.as_json:
        emit_json([{"name": s.name, "path": relative(session, s.path),
                    "applies_to": list(s.applies_to), "body": s.body}
                   for s in found])
        return 0

    rows, note = truncate(found, args.limit)
    for standard in rows:
        out(standard.name)
    if note:
        out(note)
    return 0
