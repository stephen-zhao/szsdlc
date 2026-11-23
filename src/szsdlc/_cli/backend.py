"""szsdlc backend: where the project is kept, and why. Inspection only."""

from __future__ import annotations

import argparse

from ..ops import store
from ._output import out


def add_parser(subparsers) -> None:
    backend = subparsers.add_parser("backend", prog="szsdlc backend")
    backend.add_argument("subcommand", nargs="?", choices=["show"], default="show")
    backend.set_defaults(run=run, op="backend", to_params=to_params)


def to_params(args: argparse.Namespace) -> store.BackendParams:
    return store.BackendParams()


def run(args: argparse.Namespace) -> int:
    """It used to sync as well, which made the inspector for the backend
    abstraction a second name for the workflow operation. `szsdlc sync` is
    that operation. Settings are not edited here: they live in Claude Code's
    settings files, beside everything else a user configures there.
    """
    shown = store.backend(to_params(args), args.project)
    out(f"backend  {shown.type}  ({shown.source})")
    # `workdir` outside a project has neither settings nor a root to report:
    # one line is the whole truth.
    if not shown.rows:
        return 0
    width = max(len(label) for label, _ in shown.rows)
    for label, value in shown.rows:
        out(f"  {label:<{width}}  {value}")
    return 0
