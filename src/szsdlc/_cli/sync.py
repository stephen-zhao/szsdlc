"""szsdlc sync: render, then bring the project into step with where it is kept."""

from __future__ import annotations

import argparse

from ..ops import store
from . import render


def add_parser(subparsers) -> None:
    # The `render` operation is all a sync does locally; the legs that reach
    # the backend are the wrapper's, keyed off the name (see `run` in
    # __init__), so their warnings and refusals read as every command's do.
    sync = subparsers.add_parser("sync", prog="szsdlc sync")
    sync.add_argument("--verbose", action="store_true")
    sync.set_defaults(run=render.run, op="sync", to_params=to_params)


def to_params(args: argparse.Namespace) -> store.SyncParams:
    return store.SyncParams()
