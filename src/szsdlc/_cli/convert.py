"""szsdlc convert: reclassify, leaving a resolving tombstone."""

from __future__ import annotations

import argparse

from ..errors import BadInput
from ..model import create_entity
from . import _session
from ._output import out


def add_parser(subparsers) -> None:
    convert = subparsers.add_parser("convert", prog="szsdlc convert")
    convert.add_argument("ref")
    convert.add_argument("type_name", metavar="TYPE")
    convert.set_defaults(run=run)


def run(args: argparse.Namespace) -> int:
    import shutil

    session = _session.Session(args.project)
    entity = session.entity(args.ref)
    target_type = session.config.type_for(args.type_name)

    if target_type.name == entity.type.name:
        raise BadInput(
            f"convert: {entity.id.text} is already a {target_type.name}.",
            fix=f"szsdlc show {entity.id.text}",
        )
    extra = [p.name for p in entity.artifact_files()] if entity.home else []
    if extra and not target_type.carries_artifacts:
        raise BadInput(
            f"convert: a {target_type.name} has nowhere to put an artifact, "
            f"but {entity.id.text} carries {', '.join(extra)}.",
            fix=f"remove those artifacts, then rerun",
        )
    # Artifacts decide the child's layout when the type leaves it open. The
    # alternative is creating it in the cheapest one and dropping them.
    landing = "directory" if extra and target_type.is_dynamic_layout else None

    new_id = session.ids.next_id(target_type)
    # A status that exists in both workflows is kept; otherwise the entity
    # restarts, because a status from another workflow means nothing here.
    status = (entity.status if entity.status in target_type.workflow.states
              else target_type.workflow.initial)

    child = create_entity(
        session.config, session.ids, target_type,
        title=entity.title, body=entity.body, status=status,
        tags=entity.tags,
        relations={k: (v[0] if session.config.relations[k].is_single else v)
                   for k, v in entity.relations.items()
                   if k in {r.name for r in session.config.relations_from(target_type.name)}},
        layout=landing,
    )
    assert child.id == new_id

    if entity.home is not None and child.home is not None:
        for artifact in entity.artifact_files():
            shutil.copy2(artifact, child.home / artifact.name)

    entity.delete()

    tombstones = session.ids.tombstones
    tombstones.record(entity.id.text, child.id.text)
    tombstones.save()

    out(f"{child.id.text}  ({entity.id.text} → {child.id.text}, status {status})")
    out(f"{entity.id.text} is tombstoned; existing references still resolve.")
    return 0
