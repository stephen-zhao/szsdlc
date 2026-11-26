"""szsdlc sync: render, then sync the project sdlc store."""

from __future__ import annotations

import argparse

from ..ops import sync
from ._output import out


def add_parser(subparsers) -> None:
    parser = subparsers.add_parser("sync", prog="szsdlc sync")
    parser.add_argument("--verbose", action="store_true")
    parser.set_defaults(run=run, op="sync", to_params=to_params)


def to_params(args: argparse.Namespace) -> sync.SyncParams:
    return sync.SyncParams()


def run(args: argparse.Namespace) -> int:
    synced = sync.sync(to_params(args), args.project)
    if args.verbose:
        for path in synced.changed:
            out(path)
    return 0
