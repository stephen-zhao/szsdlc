"""szsdlc backend: the sdlc backend in effect, its project sdlc store dir and root. Inspection only."""

from __future__ import annotations

import argparse

from ..ops import backend
from ._output import out


def add_parser(subparsers) -> None:
    parser = subparsers.add_parser("backend", prog="szsdlc backend")
    parser.set_defaults(run=run, op="backend", to_params=to_params)
    actions = parser.add_subparsers(dest="subcommand", metavar="{show,list,prune}")
    actions.add_parser("show", prog="szsdlc backend show").set_defaults(
        run=run, op="backend", to_params=to_params)
    actions.add_parser("list", prog="szsdlc backend list").set_defaults(
        run=run_list, op="project_sdlc_stores", to_params=list_params)
    actions.add_parser("prune", prog="szsdlc backend prune").set_defaults(
        run=run_prune, op="backend_prune", to_params=prune_params)


def to_params(args: argparse.Namespace) -> backend.BackendParams:
    return backend.BackendParams()


def prune_params(args: argparse.Namespace) -> backend.BackendPruneParams:
    return backend.BackendPruneParams()


def list_params(args: argparse.Namespace) -> backend.ProjectSdlcStoresParams:
    return backend.ProjectSdlcStoresParams()


def run_list(args: argparse.Namespace) -> int:
    """Each recorded store, then its asset roots; silent when none is recorded."""
    for store in backend.project_sdlc_stores(list_params(args), args.project).stores:
        gone = "" if store.exists else "  (gone)"
        out(backend._printable(f"{store.project_sdlc_root}  {store.backend_type}{gone}"))
        for asset in store.asset_roots:
            out(backend._printable(f"  asset root  {asset}"))
    return 0


def run_prune(args: argparse.Namespace) -> int:
    """One line per entry removed; silent when the record already holds."""
    for line in backend.backend_prune(prune_params(args), args.project).removed:
        out(line)
    return 0


def run(args: argparse.Namespace) -> int:
    """It used to sync as well, which made the inspector for the backend
    abstraction a second name for the workflow operation. `szsdlc sync` is
    that operation. Settings are not edited here: they live in Claude Code's
    settings files, beside everything else a user configures there.
    """
    shown = backend.backend(to_params(args), args.project)
    out(f"sdlc backend  {shown.type}  ({shown.source})")
    # `workdir` outside a project has neither settings nor a root to report:
    # one line is the whole truth.
    if not shown.rows:
        return 0
    width = max(len(label) for label, _ in shown.rows)
    for label, value in shown.rows:
        out(f"  {label:<{width}}  {value}")
    return 0
