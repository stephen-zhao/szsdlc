"""szsdlc refresh: make the latest where it is kept current; sends nothing."""

from __future__ import annotations

import argparse

from ..errors import BadInput, Refused, SzsdlcError
from . import _session
from ._output import out


def add_parser(subparsers) -> None:
    refresh = subparsers.add_parser("refresh", prog="szsdlc refresh")
    refresh.add_argument("--force", action="store_true",
                         help="replace local state that differs from the latest")
    refresh.set_defaults(run=run)


def run(args: argparse.Namespace) -> int:
    """Make the latest state where the project is kept the current one.

    Sends nothing and integrates nothing. Replacing local state that differs is
    the case that can lose something, so at the command line it needs
    `--force`; the backend operation itself does not ask. Renders afterwards,
    as `sync` does, so views match what arrived — the only local write.
    """
    from .. import backend as backend_module
    from .. import sync as sync_module

    backend_module.project_root(args.project)  # make the store ready, as any command does
    backend = backend_module.locate(args.project)
    if backend is None:  # pragma: no cover - project_root just made it
        raise BadInput("refresh: no project here.", fix="szsdlc init")
    if not args.force and backend.has_local_changes():
        raise Refused(
            "refresh: local changes differ from where the project is kept, and "
            "refreshing would replace them.",
            fix="szsdlc refresh --force",
            see="szsdlc sync sends them instead",
        )

    report = backend_module.refresh(backend)
    if not report.ok:
        raise Refused(f"refresh: {report.problem}", fix=report.fix or "szsdlc backend")

    try:
        sync_module.render(_session.Session(args.project))
    except SzsdlcError:
        pass  # a project that will not load after refreshing is validate's to report

    line = "refresh: changes arrived" if report.incoming else "refresh: nothing new"
    if report.replaced:
        line += "; local state replaced" + (f" ({report.note})" if report.note else "")
    out(line)
    return 0
