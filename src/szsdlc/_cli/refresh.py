"""szsdlc refresh: take the latest from where the project is kept; send nothing."""

from __future__ import annotations

import argparse

from ..ops import refresh
from ._output import out


def add_parser(subparsers) -> None:
    refresh = subparsers.add_parser("refresh", prog="szsdlc refresh")
    refresh.add_argument("--force", action="store_true",
                         help="replace local state that differs from the latest")
    refresh.set_defaults(run=run, op="refresh", to_params=to_params)


def to_params(args: argparse.Namespace) -> refresh.RefreshParams:
    return refresh.RefreshParams(force=args.force)


def run(args: argparse.Namespace) -> int:
    refreshed = refresh.refresh(to_params(args), args.project)
    line = "refresh: changes arrived" if refreshed.incoming else "refresh: nothing new"
    if refreshed.replaced:
        line += "; local state replaced" + (f" ({refreshed.note})" if refreshed.note else "")
    out(line)
    return 0
