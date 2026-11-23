"""szsdlc validate: every consistency rule. Silent when clean; findings when not."""

from __future__ import annotations

import argparse

from ..errors import EXIT_INVALID
from ..ops import queries
from . import _session
from ._output import bound_note, emit_json, out
from ._parser import DEFAULT_LIMIT


def add_parser(subparsers) -> None:
    validate = subparsers.add_parser("validate", prog="szsdlc validate")
    validate.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    validate.add_argument("--verbose", action="store_true")
    validate.add_argument("--json", action="store_true", dest="as_json")
    validate.set_defaults(run=run, op="validate", to_params=to_params)


def to_params(args: argparse.Namespace) -> queries.ValidateParams:
    return queries.ValidateParams(limit=0 if args.as_json else args.limit)


def run(args: argparse.Namespace) -> int:
    """The single enforcement point: `render` runs constantly and tolerantly during
    a turn, this runs once at the end and strictly.
    """
    params = to_params(args)
    found = queries.validate(params, _session.Session(args.project))
    if args.as_json:
        emit_json([{"level": f.level, "kind": f.kind, "ref": f.ref,
                    "message": f.message, "fix": f.fix} for f in found.rows])
        return EXIT_INVALID if found.errors else 0

    if not found.total:
        # C4 — nothing on the happy path. This runs at every turn end.
        if args.verbose:
            out("clean")
        return 0
    for finding in found.rows:
        out(f"{finding.level:<7} {finding.ref}  {finding.message}"
            + (f" — {finding.fix}" if finding.fix else ""))
    note = bound_note(found.total, params.limit)
    if note:
        out(note)
    errors, warnings = found.errors, found.warnings
    out(f"{errors} error{'s' if errors != 1 else ''}, "
        f"{warnings} warning{'s' if warnings != 1 else ''}")
    # Exit 4, not 1: C7 gives validation failure its own code so a caller can
    # branch on it without parsing text.
    return EXIT_INVALID if errors else 0
