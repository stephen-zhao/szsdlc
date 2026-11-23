"""szsdlc backend: where the project is kept, and why."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from ._output import out


def add_parser(subparsers) -> None:
    backend = subparsers.add_parser("backend", prog="szsdlc backend")
    backend.add_argument("subcommand", nargs="?", choices=["show"], default="show")
    backend.set_defaults(run=run)


def run(args: argparse.Namespace) -> int:
    """Show where the project is kept, and why. Inspection only.

    It used to sync as well, which made the inspector for the backend
    abstraction a second name for the workflow operation — two commands for one
    thing, differing in error discipline and pass count rather than in what
    they did. `szsdlc sync` is that operation, and works in any szsdlc
    repository now that `workdir` is a backend like the others.

    Settings are not edited here: they live in Claude Code's settings files,
    beside everything else a user configures there (see szsdlc.settings).
    """
    from .. import backend as backend_module

    def show(line: str) -> None:
        # Every value here came from a settings file someone else may have
        # written; none of it reaches the terminal as a control sequence.
        out(re.sub(r"[\x00-\x1f\x7f-\x9f]", "?", line))

    selection = backend_module.select(args.project)

    # Asked first, not as a fallback: standing inside a store is a fact about
    # where you are, and it used to be inferred from `resolve` finding nothing
    # — which it now never does, because every project has a backend.
    inside = backend_module.managed(args.project or Path.cwd())
    if inside is not None:
        current = inside
        show(f"backend  {current.type}  (this directory is inside its store)")
        rows = [(label, value) for label, value, _ in current.describe()]
    else:
        current = backend_module.resolve(args.project)
        show(f"backend  {current.type}  ({selection.source or 'default'})")
        found = selection.found
        rows = [(label, f"{value}  ({next((found[k].source for k in keys if k in found),
                                          'default')})")
                for label, value, keys in current.describe()]

    if current.root is not None:
        rows.append(("root", str(current.root)
                     + ("" if current.store.exists() else "  (not created yet)")))
    # `workdir` outside a project has neither: no settings to report, and no
    # root to report them about. One line is the whole truth.
    if not rows:
        return 0
    width = max(len(label) for label, _ in rows)
    for label, value in rows:
        show(f"  {label:<{width}}  {value}")
    return 0
