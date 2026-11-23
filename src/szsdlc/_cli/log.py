"""szsdlc log: append dated lines to an entity's journal."""

from __future__ import annotations

import argparse
import sys

from ..ops import Project, log


def add_parser(subparsers) -> None:
    log = subparsers.add_parser("log", prog="szsdlc log")
    log.add_argument("ref")
    log.add_argument("message", nargs="?", default=None)
    log.set_defaults(run=run, op="log", to_params=to_params)


def to_params(args: argparse.Namespace) -> log.LogParams:
    return log.LogParams(ref=args.ref, message=args.message or "")


def run(args: argparse.Namespace) -> int:
    if args.message is None and not sys.stdin.isatty():
        args.message = sys.stdin.read()
    log.log(to_params(args), Project(args.project))
    # C4 — the audit budgets this at zero lines. It runs constantly.
    return 0
