"""szsdlc capture: capture an idea from an argument, stdin or $EDITOR."""

from __future__ import annotations

import argparse

from ..errors import BadInput
from ..model import create_entity
from . import _session
from ._input import read_text_input
from ._output import out


def add_parser(subparsers) -> None:

    capture = subparsers.add_parser("capture", prog="szsdlc capture")
    capture.add_argument("text", nargs="?", default=None)
    capture.add_argument("--type", dest="type_name", default=None,
                         help="intake type, when a project declares more than one")
    capture.set_defaults(run=run)


def run(args: argparse.Namespace) -> int:
    session = _session.Session(args.project)
    entity_type = session.intake_type(args.type_name)

    body = read_text_input(args.text).strip()
    if not body:
        raise BadInput(
            "nothing to capture: the input was empty.",
            fix='szsdlc capture "the thought"',
        )

    entity = create_entity(session.config, session.ids, entity_type,
                           body=body + "\n")
    out(entity.id.text)
    return 0
