"""szsdlc init: a new project, here or wherever the backend keeps it."""

from __future__ import annotations

import argparse

from ..ops import init
from ._output import out


def add_parser(subparsers) -> None:
    init = subparsers.add_parser("init", prog="szsdlc init")
    init.add_argument("--name", default=None)
    init.set_defaults(run=run, op="init", to_params=to_params)


def to_params(args: argparse.Namespace) -> init.InitParams:
    return init.InitParams(name=args.name)


def run(args: argparse.Namespace) -> int:
    made = init.init(to_params(args), args.project)
    for path in made.created:
        out(path)
    return 0
