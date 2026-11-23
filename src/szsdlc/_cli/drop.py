"""szsdlc drop: abandon an entity, with a reason."""

from __future__ import annotations

import argparse
import sys

from ..ops import entities
from . import _session
from ._output import out


def add_parser(subparsers) -> None:
    drop = subparsers.add_parser("drop", prog="szsdlc drop")
    drop.add_argument("ref")
    drop.add_argument("--reason", default=None)
    drop.set_defaults(run=run, op="drop", to_params=to_params)


def to_params(args: argparse.Namespace) -> entities.DropParams:
    return entities.DropParams(ref=args.ref, reason=args.reason or "")


def run(args: argparse.Namespace) -> int:
    if args.reason is None and not sys.stdin.isatty():
        args.reason = sys.stdin.read()
    dropped = entities.drop(to_params(args), _session.Session(args.project))
    out(f"{dropped.id}: " + "; ".join([f"{dropped.before} → {dropped.after}", *dropped.notes]))
    return 0
