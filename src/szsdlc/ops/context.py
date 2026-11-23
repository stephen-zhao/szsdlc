"""szsdlc context: the orientation pack an agent reads at session start."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import _queries
from ._project import Project
from ._registry import operation
from ._types import Data


@dataclass(frozen=True)
class InFlightRow(Data):
    id: str
    status: str | None
    title: str


@dataclass(frozen=True)
class ContextParams(Data):
    pass


@dataclass(frozen=True)
class ContextResult(Data):
    #: The pack an agent reads at session start, as text: that is what it is for.
    text: str
    counters: dict[str, Any]
    in_flight: tuple[InFlightRow, ...]
    current_entity: Any = None
    current_task: Any = None


@operation("context", params=ContextParams, result=ContextResult, kind="query")
def context(params: ContextParams, project: Project) -> ContextResult:
    from .. import context as context_module

    built = context_module.build(project.config, project.store, _queries.graph(project),
                                 _queries.roadmaps(project))
    return ContextResult(
        text=context_module.render(built),
        counters={label: value for label, value, _ in built.counters.rows()},
        in_flight=tuple(InFlightRow(e.id.text, e.status, e.title) for e in built.in_flight),
        current_entity=built.current_entity, current_task=built.current_task,
    )
