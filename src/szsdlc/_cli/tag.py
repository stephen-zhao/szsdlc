"""szsdlc tag / untag: add or remove tags."""

from __future__ import annotations

import argparse

from ..ops import Project, tag
from ._output import out


def add_parser(subparsers) -> None:
    for verb, runner in (("tag", run_tag), ("untag", run_untag)):
        tagger = subparsers.add_parser(verb, prog=f"szsdlc {verb}")
        tagger.add_argument("ref")
        tagger.add_argument("tags", nargs="+", metavar="tag")
        tagger.set_defaults(run=runner, adding=(verb == "tag"), op=verb, to_params=to_params)


def to_params(args: argparse.Namespace) -> tag.TagParams:
    return tag.TagParams(ref=args.ref, tags=tuple(args.tags))


def _apply(args: argparse.Namespace, operation) -> int:
    tagged = operation(to_params(args), Project(args.project))
    out(f"{tagged.id}: {', '.join(tagged.tags) or '(no tags)'}")
    return 0


def run_tag(args: argparse.Namespace) -> int:
    return _apply(args, tag.tag)


def run_untag(args: argparse.Namespace) -> int:
    return _apply(args, tag.untag)
