"""szsdlc list: entities, filtered and bounded."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..text import add_tags
from . import _queries
from ._listing import DEFAULT_LIMIT, bounded
from ._project import Project
from ._registry import operation
from ._types import Data


@dataclass(frozen=True)
class EntityRow(Data):
    id: str
    type: str
    status: str | None
    title: str
    tags: tuple[str, ...]
    fields: dict[str, Any]


@dataclass(frozen=True)
class ListParams(Data):
    type: str | None = None
    status: str | None = None
    tags: tuple[str, ...] = ()
    parent: str | None = None
    uncovered: bool = False
    unscheduled: bool = False
    limit: int = DEFAULT_LIMIT


@dataclass(frozen=True)
class ListResult(Data):
    rows: tuple[EntityRow, ...]
    total: int


@operation("list", params=ListParams, result=ListResult, kind="query")
def list_(params: ListParams, project: Project) -> ListResult:
    graph = _queries.graph(project)
    entities = list(project.store)

    if params.type:
        project.config.type_for(params.type)  # refuses with the known types
        entities = [e for e in entities if e.type.name == params.type]
    if params.status:
        entities = [e for e in entities if e.status == params.status]
    for tag in params.tags:
        normalized = add_tags([], [tag])
        entities = [e for e in entities if set(normalized) <= set(e.tags)]
    if params.parent:
        parent_id = project.ids.resolve(params.parent).text
        relation = project.config.data["parent_relation"]
        entities = [e for e in entities if parent_id in e.targets(relation)]
    if params.uncovered:
        entities = [e for e in entities if _queries.is_uncovered(graph, e)]
    if params.unscheduled:
        scheduled = {i for r in _queries.roadmaps(project).values() for i in r.all_ids()}
        entities = [e for e in entities
                    if e.type.schedulable and not e.is_terminal
                    and e.id.text not in scheduled]

    entities.sort(key=lambda e: (e.id.prefix, e.id.number))
    rows, total = bounded([
        EntityRow(e.id.text, e.type.name, e.status, e.title, tuple(e.tags),
                  {name: e.field(name) for name in e.type.fields})
        for e in entities], params.limit)
    return ListResult(rows, total)
