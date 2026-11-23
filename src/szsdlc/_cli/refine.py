"""szsdlc refine: turn a captured idea into a typed entity."""

from __future__ import annotations

import argparse

from ..ops import Project, refine
from ._output import out


def add_parser(subparsers) -> None:
    refine = subparsers.add_parser("refine", prog="szsdlc refine")
    refine.add_argument("ref")
    refine.add_argument("--into", dest="into", required=True)
    refine.add_argument("--title", dest="title", default=None)
    refine.set_defaults(run=run, op="refine", to_params=to_params)


def to_params(args: argparse.Namespace) -> refine.RefineParams:
    return refine.RefineParams(ref=args.ref, into=args.into, title=args.title)


def run(args: argparse.Namespace) -> int:
    refined = refine.refine(to_params(args), Project(args.project))
    # C1 — the resulting state, so no confirming `show` is ever needed.
    out(f"{refined.id}  ({refined.source}: {refined.before} → {refined.after})")
    return 0
