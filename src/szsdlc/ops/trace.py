"""szsdlc trace: the relation graph around one entity."""

from __future__ import annotations

from dataclasses import dataclass

from . import _queries
from ._listing import DEFAULT_LIMIT, bounded
from ._project import Project
from ._registry import operation
from ._types import Data


@dataclass(frozen=True)
class TraceRow(Data):
    source: str
    relation: str
    target: str


@dataclass(frozen=True)
class TraceParams(Data):
    ref: str
    depth: int = 2
    limit: int = DEFAULT_LIMIT


@dataclass(frozen=True)
class TraceResult(Data):
    rows: tuple[TraceRow, ...]
    total: int


@operation("trace", params=TraceParams, result=TraceResult, kind="query")
def trace(params: TraceParams, project: Project) -> TraceResult:
    graph = _queries.graph(project)
    entity_id = project.ids.resolve(params.ref)
    edges = graph.trace(entity_id, depth=params.depth)
    rows, total = bounded([TraceRow(e.source.text, e.kind, e.target.label) for e in edges],
                          params.limit)
    return TraceResult(rows, total)
