"""szsdlc sync: render, then bring the project into step with where it is kept."""

from __future__ import annotations

import argparse

from .. import backend as backend_module
from ..errors import Refused, SzsdlcError
from ..ops import store
from . import _session, _sync
from ._output import out


def add_parser(subparsers) -> None:
    sync = subparsers.add_parser("sync", prog="szsdlc sync")
    sync.add_argument("--verbose", action="store_true")
    sync.set_defaults(run=run, op="sync", to_params=to_params)


def to_params(args: argparse.Namespace) -> store.SyncParams:
    return store.SyncParams()


def run(args: argparse.Namespace) -> int:
    """The `sync` operation's two halves, with the CLI's reporting between them.

    It does what `ops.store.sync` does: create the store if needed, render
    (strictly, as `szsdlc render` does), then the backend legs — a sync, and
    a second pass to draw whatever arrived. It runs the halves itself rather
    than calling the operation whole so that `--verbose` reports the draw
    before anything is sent, and a backend that fails on the filesystem only
    warns, as it does after every other command. A sync that ran and failed
    refuses: it was asked for by name.
    """
    backend_module.project_root(args.project)  # create the store if needed
    rendered = store.render(store.RenderParams(), _session.Session(args.project))
    if args.verbose:
        for path in rendered.changed:
            out(path)
    try:
        report = store.send(args.project)
    except SzsdlcError:  # pragma: no cover - the render just loaded it
        return 0
    except OSError as exc:
        _sync.warn_backend(exc)
        return 0
    if report is not None and not report.ok:
        raise Refused(report.problem, fix=report.fix)
    return 0
