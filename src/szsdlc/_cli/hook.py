"""szsdlc hook: the entry point hooks.json invokes, never typed by anyone."""

from __future__ import annotations

import argparse

from .. import hooks as hooks_module


def add_parser(subparsers) -> None:
    # Hidden: invoked by hooks.json, never typed. It lives on this parser so
    # there is exactly one interpreter-resolution path to get wrong on Windows.
    hook = subparsers.add_parser("hook", prog="szsdlc hook")
    hook.add_argument("event", choices=sorted(hooks_module.HANDLERS))
    hook.set_defaults(run=run)


def run(args: argparse.Namespace) -> int:
    return hooks_module.dispatch(args.event, cwd=args.project)
