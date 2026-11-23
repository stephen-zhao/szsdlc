"""szsdlc capture: a thought into the inbox, from an argument, stdin or $EDITOR."""

from __future__ import annotations

import argparse

from ..ops import entities
from . import _session
from ._input import read_text_input
from ._output import out


def add_parser(subparsers) -> None:
    capture = subparsers.add_parser("capture", prog="szsdlc capture")
    capture.add_argument("text", nargs="?", default=None)
    capture.add_argument("--type", dest="type_name", default=None,
                         help="intake type, when a project declares more than one")
    capture.set_defaults(run=run, op="capture", to_params=to_params)


def to_params(args: argparse.Namespace) -> entities.CaptureParams:
    return entities.CaptureParams(text=args.text or "", type=args.type_name)


def run(args: argparse.Namespace) -> int:
    session = _session.Session(args.project)
    session.intake_type(args.type_name)  # refused before anything is read
    args.text = read_text_input(args.text)
    captured = entities.capture(to_params(args), session)
    out(captured.id)
    return 0
