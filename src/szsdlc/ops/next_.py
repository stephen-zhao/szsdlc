"""szsdlc next: actionable work, in roadmap order, unblocked."""

from __future__ import annotations

from dataclasses import dataclass

from . import _queries
from ._listing import NEXT_LIMIT, bounded
from ._project import Project
from ._registry import operation
from ._types import Data


@dataclass(frozen=True)
class NextRow(Data):
    id: str
    status: str | None
    title: str
    #: On a roadmap, as against queued after everything that is.
    scheduled: bool


@dataclass(frozen=True)
class NextParams(Data):
    horizon: str | None = None
    parent: str | None = None
    roadmap: str | None = None
    limit: int = NEXT_LIMIT


@dataclass(frozen=True)
class NextResult(Data):
    rows: tuple[NextRow, ...]
    total: int


@operation("next", params=NextParams, result=NextResult, kind="query")
def next_(params: NextParams, project: Project) -> NextResult:
    graph = _queries.graph(project)
    roadmaps = _queries.roadmaps(project, params.roadmap)
    order = _queries.roadmap_order(roadmaps, params.horizon)
    parent_id = project.ids.resolve(params.parent).text if params.parent else None
    parent_relation = project.config.data["parent_relation"]

    candidates = []
    for entity in project.store.with_flag("actionable"):
        if entity.is_terminal:
            continue
        if params.horizon and entity.id.text not in order:
            continue
        if parent_id and parent_id not in entity.targets(parent_relation):
            continue
        if _queries.blocked(project, graph, entity):
            continue
        candidates.append(entity)

    # Scheduled work first, in roadmap sequence; everything else after, by id.
    candidates.sort(key=lambda e: (order.get(e.id.text, len(order)),
                                   e.id.prefix, e.id.number))
    rows, total = bounded([NextRow(e.id.text, e.status, e.title, e.id.text in order)
                           for e in candidates], params.limit)
    return NextResult(rows, total)
