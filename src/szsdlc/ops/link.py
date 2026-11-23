"""szsdlc link / unlink: author or remove a relation."""

from __future__ import annotations

from dataclasses import dataclass

from ..errors import BadInput
from ..model import Entity
from ..text import nearest
from . import _views
from ._precondition import entity_for
from ._project import Project
from ._registry import operation
from ._types import Data


@dataclass(frozen=True)
class LinkParams(Data):
    ref: str
    relation: str
    target: str
    expect: str | None = None


@dataclass(frozen=True)
class LinkResult(Data):
    id: str
    relation: str
    target: str
    #: The generated inverse, so the back-link never has to be looked up.
    inverse: str | None


@dataclass(frozen=True)
class UnlinkResult(Data):
    id: str
    relation: str
    #: The relation's targets after the removal.
    remaining: tuple[str, ...]


def _relation_for(project: Project, entity: Entity, name: str):
    allowed = [r.name for r in project.config.relations_from(entity.type.name)]
    if name in allowed:
        return project.config.relations[name]
    inverse = project.config.relation_for_inverse(name)
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


@operation("link", params=LinkParams, result=LinkResult, kind="mutation")
def link(params: LinkParams, project: Project) -> LinkResult:
    entity = entity_for(project, params.ref, params.expect)
    relation = _relation_for(project, entity, params.relation)
    target = project.entity(params.target)

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
    _views.redraw(project)
    return LinkResult(entity.id.text, relation.name, target.id.text, relation.inverse)


@operation("unlink", params=LinkParams, result=UnlinkResult, kind="mutation")
def unlink(params: LinkParams, project: Project) -> UnlinkResult:
    entity = entity_for(project, params.ref, params.expect)
    relation = _relation_for(project, entity, params.relation)
    existing = entity.targets(relation.name)

    # Resolve tolerantly, but fall back to the literal text: unlinking a
    # dangling edge is exactly when the target cannot be resolved.
    try:
        wanted = project.ids.parse(params.target).text
    except BadInput:
        wanted = params.target
    if wanted not in existing:
        raise BadInput(
            f"unlink: {entity.id.text} does not {relation.name} {wanted}.",
            fix=f"szsdlc show {entity.id.text}",
            see=f"{relation.name}: {', '.join(existing) or '(none)'}",
        )

    entity.set_relation(relation.name, [t for t in existing if t != wanted])
    entity.save()
    _views.redraw(project)
    return UnlinkResult(entity.id.text, relation.name, tuple(entity.targets(relation.name)))
