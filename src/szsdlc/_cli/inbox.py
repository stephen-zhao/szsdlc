"""szsdlc inbox: unrefined ideas, oldest first."""

from __future__ import annotations

import argparse

from ..text import clip
from . import _session
from ._output import emit_json, id_width, out, truncate
from ._parser import DEFAULT_LIMIT


def add_parser(subparsers) -> None:
    inbox = subparsers.add_parser("inbox", prog="szsdlc inbox")
    inbox.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    inbox.add_argument("--json", action="store_true", dest="as_json")
    inbox.set_defaults(run=run)


def run(args: argparse.Namespace) -> int:
    from ..context import age_days as _age_days

    session = _session.Session(args.project)

    unrefined = [
        entity for entity in session.store.with_flag("intake")
        if not entity.is_terminal
    ]
    # Oldest first: an inbox sorted newest-first is an inbox whose bottom never
    # gets drained.
    unrefined.sort(key=lambda e: (-(_age_days(e) or 0), e.id.prefix, e.id.number))

    if args.as_json:
        emit_json([{"id": e.id.text, "status": e.status, "age_days": _age_days(e),
                    "title": e.title} for e in unrefined])
        return 0

    rows, note = truncate(unrefined, args.limit)
    width = id_width(rows)
    for entity in rows:
        age = _age_days(entity)
        stamp = f"{age}d" if age is not None else "-"
        out(f"{entity.id.text:<{width}}  {stamp:>5}  {clip(entity.title)}")
    if note:
        out(note)
    return 0
