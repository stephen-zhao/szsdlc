"""szsdlc link and unlink: author or remove one edge; inverses are generated."""

from __future__ import annotations

import argparse

from ..errors import BadInput
from ..model import Entity
from ..text import nearest
from . import _session
from ._output import out


def add_parser(subparsers) -> None:
    for verb, runner in (("link", run_link), ("unlink", run_unlink)):
        linker = subparsers.add_parser(verb, prog=f"szsdlc {verb}")
        linker.add_argument("ref")
        linker.add_argument("relation")
        linker.add_argument("target")
        linker.set_defaults(run=runner)


def _relation_for(session: _session.Session, entity: Entity, name: str):
    allowed = [r.name for r in session.config.relations_from(entity.type.name)]
    if name in allowed:
        return session.config.relations[name]

    inverse = session.config.relation_for_inverse(name)
    if inverse is not None:
        raise BadInput(
            f"link: {name!r} is a generated inverse and is never authored.",
            fix=f"szsdlc link <ref> {inverse.name} {entity.id.text}",
        )

    suggestion = nearest(name, allowed)
    raise BadInput(
        f"link: a {entity.type.name} cannot author {name!r}"
        + (f"; did you mean {suggestion}?" if suggestion else "."),
        fix=f"szsdlc link {entity.id.text} {suggestion or (allowed or ['<relation>'])[0]} <ref>",
    )


def run_link(args: argparse.Namespace) -> int:
    session = _session.Session(args.project)
    entity = session.entity(args.ref)
    relation = _relation_for(session, entity, args.relation)
    target = session.entity(args.target)

    if relation.target_types and target.type.name not in relation.target_types:
        raise BadInput(
            f"link: {relation.name} may not point at a {target.type.name}.",
            fix=f"szsdlc list --type {sorted(relation.target_types)[0]}",
            see=f"allowed: {', '.join(sorted(relation.target_types))}",
        )
    if target.id == entity.id:
        raise BadInput(
            f"link: {entity.id.text} cannot {relation.name} itself.",
            fix=f"szsdlc show {entity.id.text}",
        )

    existing = entity.targets(relation.name)
    if target.id.text in existing:
        raise BadInput(
            f"link: {entity.id.text} already {relation.name} {target.id.text}.",
            fix=f"szsdlc show {entity.id.text}",
        )

    updated = [target.id.text] if relation.is_single else [*existing, target.id.text]
    entity.set_relation(relation.name, updated)
    entity.save()

    # C1 — and the generated inverse, so the back-link never has to be looked up.
    out(f"{entity.id.text} {relation.name} {target.id.text}  "
        f"({target.id.text} {relation.inverse} {entity.id.text})")
    return 0


def run_unlink(args: argparse.Namespace) -> int:
    session = _session.Session(args.project)
    entity = session.entity(args.ref)
    relation = _relation_for(session, entity, args.relation)

    existing = entity.targets(relation.name)
    # Resolve tolerantly, but fall back to the literal text: unlinking a
    # dangling edge is exactly when the target cannot be resolved.
    try:
        wanted = session.ids.parse(args.target).text
    except BadInput:
        wanted = args.target

    if wanted not in existing:
        raise BadInput(
            f"unlink: {entity.id.text} does not {relation.name} {wanted}.",
            fix=f"szsdlc show {entity.id.text}",
            see=f"{relation.name}: {', '.join(existing) or '(none)'}",
        )

    entity.set_relation(relation.name, [t for t in existing if t != wanted])
    entity.save()
    remaining = entity.targets(relation.name)
    out(f"{entity.id.text} {relation.name}: {', '.join(remaining) or '(none)'}")
    return 0
