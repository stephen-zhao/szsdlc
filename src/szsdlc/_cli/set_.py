"""szsdlc set: set scalar fields; enforces the workflow."""

from __future__ import annotations

import argparse
import sys

from ..ops import Project, entities
from ._output import out


def add_parser(subparsers) -> None:
    setter = subparsers.add_parser("set", prog="szsdlc set")
    setter.add_argument("ref")
    setter.add_argument("assignments", nargs="+", metavar="field=value")
    setter.add_argument("--force", action="store_true")
    setter.add_argument("--reason", default=None)
    setter.set_defaults(run=run, op="set", to_params=to_params)


def to_params(args: argparse.Namespace) -> entities.SetParams:
    return entities.SetParams(ref=args.ref, assignments=tuple(args.assignments),
                              force=args.force, reason=args.reason)


def run(args: argparse.Namespace) -> int:
    # A forced move needs a reason; read from stdin when --reason is not given,
    # as `drop` does, and never defaulted.
    if args.force and args.reason is None and not sys.stdin.isatty():
        args.reason = sys.stdin.read()
    changed = entities.set_(to_params(args), Project(args.project))
    reported: list[str] = []
    for change in changed.changes:
        line = f"{change.field} {change.before} → {change.after}"
        reported.append(line + (f" (forced: {change.forced})" if change.forced else ""))
        reported += change.notes
    # C1 — the resulting state, one line, so no confirming `show` is needed.
    out(f"{changed.id}: " + "; ".join(reported))
    return 0
