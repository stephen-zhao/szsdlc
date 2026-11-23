"""szsdlc sync: render, then put the project in step with where it is kept."""

from __future__ import annotations

from . import render


def add_parser(subparsers) -> None:
    # Same runner as `render`: rendering is all a sync does locally, and the
    # legs that reach the backend are the wrapper's, keyed off the name.
    sync = subparsers.add_parser("sync", prog="szsdlc sync")
    sync.add_argument("--verbose", action="store_true")
    sync.set_defaults(run=render.run)
