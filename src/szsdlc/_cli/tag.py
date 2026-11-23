"""szsdlc tag and untag: add or remove tags, normalized on write."""

from __future__ import annotations

import argparse

from ..text import add_tags, remove_tags
from . import _session
from ._output import out


def add_parser(subparsers) -> None:
    for verb, runner in (("tag", run_tag), ("untag", run_untag)):
        tagger = subparsers.add_parser(verb, prog=f"szsdlc {verb}")
        tagger.add_argument("ref")
        tagger.add_argument("tags", nargs="+", metavar="tag")
        tagger.set_defaults(run=runner, adding=(verb == "tag"))


def _apply_tags(args: argparse.Namespace, adding: bool) -> int:
    session = _session.Session(args.project)
    entity = session.entity(args.ref)
    before = entity.tags
    entity.set_tags(add_tags(before, args.tags) if adding
                    else remove_tags(before, args.tags))
    entity.save()
    out(f"{entity.id.text}: {', '.join(entity.tags) or '(no tags)'}")
    return 0


def run_tag(args: argparse.Namespace) -> int:
    return _apply_tags(args, adding=True)


def run_untag(args: argparse.Namespace) -> int:
    return _apply_tags(args, adding=False)
