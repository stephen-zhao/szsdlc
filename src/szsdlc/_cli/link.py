"""szsdlc link / unlink: author or remove a relation."""

from __future__ import annotations

import argparse

from ..ops import entities
from . import _session
from ._output import out


def add_parser(subparsers) -> None:
    for verb, runner in (("link", run_link), ("unlink", run_unlink)):
        linker = subparsers.add_parser(verb, prog=f"szsdlc {verb}")
        linker.add_argument("ref")
        linker.add_argument("relation")
        linker.add_argument("target")
        linker.set_defaults(run=runner, op=verb, to_params=to_params)


def to_params(args: argparse.Namespace) -> entities.LinkParams:
    return entities.LinkParams(ref=args.ref, relation=args.relation, target=args.target)


def run_link(args: argparse.Namespace) -> int:
    linked = entities.link(to_params(args), _session.Session(args.project))
    # C1 — and the generated inverse, so the back-link never has to be looked up.
    out(f"{linked.id} {linked.relation} {linked.target}  "
        f"({linked.target} {linked.inverse} {linked.id})")
    return 0


def run_unlink(args: argparse.Namespace) -> int:
    unlinked = entities.unlink(to_params(args), _session.Session(args.project))
    out(f"{unlinked.id} {unlinked.relation}: {', '.join(unlinked.remaining) or '(none)'}")
    return 0
