"""szsdlc convert: change an entity's type; the old id is tombstoned."""

from __future__ import annotations

import argparse

from ..ops import entities
from . import _session
from ._output import out


def add_parser(subparsers) -> None:
    convert = subparsers.add_parser("convert", prog="szsdlc convert")
    convert.add_argument("ref")
    convert.add_argument("type_name", metavar="TYPE")
    convert.set_defaults(run=run, op="convert", to_params=to_params)


def to_params(args: argparse.Namespace) -> entities.ConvertParams:
    return entities.ConvertParams(ref=args.ref, type=args.type_name)


def run(args: argparse.Namespace) -> int:
    converted = entities.convert(to_params(args), _session.Session(args.project))
    out(f"{converted.id}  ({converted.was} → {converted.id}, status {converted.status})")
    out(f"{converted.was} is tombstoned; existing references still resolve.")
    return 0
