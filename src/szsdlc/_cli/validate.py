"""szsdlc validate: every consistency rule; silent when clean."""

from __future__ import annotations

import argparse

from ..errors import EXIT_INVALID
from . import _queries, _session
from ._output import emit_json, out, truncate
from ._parser import DEFAULT_LIMIT


def add_parser(subparsers) -> None:
    validate = subparsers.add_parser("validate", prog="szsdlc validate")
    validate.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    validate.add_argument("--verbose", action="store_true")
    validate.add_argument("--json", action="store_true", dest="as_json")
    validate.set_defaults(run=run)


def run(args: argparse.Namespace) -> int:
    """Every consistency rule. Silent when clean; findings when not.

    The single enforcement point: `render` runs constantly and tolerantly during
    a turn, this runs once at the end and strictly.
    """
    from .. import validate as validate_module

    session = _session.Session(args.project)
    findings = validate_module.run(session.config, session.store, _queries.graph(session),
                                   _queries.roadmaps(session))
    errors, warnings = validate_module.summary(findings)

    if args.as_json:
        emit_json([{"level": f.level, "kind": f.kind, "ref": f.ref,
                    "message": f.message, "fix": f.fix} for f in findings])
        return EXIT_INVALID if errors else 0

    if not findings:
        # C4 — nothing on the happy path. This runs at every turn end.
        if args.verbose:
            out("clean")
        return 0

    rows, note = truncate(findings, args.limit)
    for finding in rows:
        out(f"{finding.level:<7} {finding.ref}  {finding.message}"
            + (f" — {finding.fix}" if finding.fix else ""))
    if note:
        out(note)
    out(f"{errors} error{'s' if errors != 1 else ''}, "
        f"{warnings} warning{'s' if warnings != 1 else ''}")

    # Exit 4, not 1: C7 gives validation failure its own code so a caller can
    # branch on it without parsing text.
    return EXIT_INVALID if errors else 0
