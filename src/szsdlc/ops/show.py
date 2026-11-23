"""szsdlc show: one entity, with the hash a later write passes as `expect`."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import _queries
from ._paths import relative
from ._project import Project
from ._registry import operation
from ._types import Data


@dataclass(frozen=True)
class ShowParams(Data):
    ref: str
    #: Also build the context bundle an agent reads before working on it.
    context: bool = False


@dataclass(frozen=True)
class ShowResult(Data):
    id: str
    type: str
    status: str | None
    title: str
    tags: tuple[str, ...]
    relations: dict[str, Any]
    #: Keyed off the fields the *type* declares, so the key set is the type's contract.
    fields: dict[str, Any]
    derived: dict[str, str]
    path: str
    #: The entity's file as it stands: frontmatter and body.
    text: str
    #: The content hash a later mutation passes as `expect`.
    hash: str
    bundle: str | None = None


@operation("show", params=ShowParams, result=ShowResult, kind="query")
def show(params: ShowParams, project: Project) -> ShowResult:
    from .. import context as context_module

    entity = project.entity(params.ref)
    graph = _queries.graph(project)
    return ShowResult(
        id=entity.id.text, type=entity.type.name, status=entity.status,
        title=entity.title, tags=tuple(entity.tags), relations=dict(entity.relations),
        fields={name: entity.field(name) for name in entity.type.fields},
        derived={k: str(v) for k, v in graph.derived(entity).items()},
        path=relative(project.config.root, entity.path), text=entity.render(),
        hash=entity.content_hash(),
        bundle=(context_module.bundle(project.config, graph, entity)
                if params.context else None),
    )
