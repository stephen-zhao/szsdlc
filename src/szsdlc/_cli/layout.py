"""szsdlc layout: lay an entry out as a section, file or directory."""

from __future__ import annotations

import argparse

from ..config import LAYOUTS
from ..errors import BadInput
from ..model import relayout
from . import _session
from ._output import display_path, out


def add_parser(subparsers) -> None:
    layout = subparsers.add_parser("layout", prog="szsdlc layout")
    layout.add_argument("ref")
    layout.add_argument("layout", metavar="LAYOUT", choices=list(LAYOUTS))
    layout.set_defaults(run=run)


# ---------------------------------------------------------------------------
# layout
#
# Named for the thing it changes, not the thing it does to the disk. `layout`
# is already the word in the config key, in the schema and on the entity, and
# a fifth word for the same idea is a translation step at every reading. It is
# `convert` for the other axis: convert changes an entry's type, layout
# changes what it occupies.
# ---------------------------------------------------------------------------
def run(args: argparse.Namespace) -> int:
    session = _session.Session(args.project)
    entity = session.entity(args.ref)
    target = args.layout

    if not entity.type.is_dynamic_layout:
        raise BadInput(
            f"layout: every {entity.type.name} is laid out as a "
            f"{entity.type.layout}, so there is no other layout to give it.",
            fix=f"set entity_types.{entity.type.name}.layout to dynamic",
        )
    if target == entity.layout:
        raise BadInput(
            f"layout: {entity.id.text} is already laid out as a {target}.",
            fix=f"szsdlc show {entity.id.text}",
        )

    carried = [p.name for p in entity.artifact_files()]
    if carried and target != "directory":
        raise BadInput(
            f"layout: a {target} has nowhere to put an artifact, but "
            f"{entity.id.text} carries {', '.join(carried)}.",
            fix="remove those artifacts, then rerun",
        )

    laid = relayout(session.config, session.ids, entity, target)
    out(f"{laid.id.text}  ({entity.layout} → {target}) {display_path(session, laid)}")
    return 0
