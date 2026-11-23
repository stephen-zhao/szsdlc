"""szsdlc schedule / unschedule: place an entity on a roadmap, or take it off."""

from __future__ import annotations

from dataclasses import dataclass

from ..errors import BadInput
from . import _views
from ._precondition import entity_for
from ._project import Project
from ._registry import operation
from ._types import Data


@dataclass(frozen=True)
class ScheduleParams(Data):
    ref: str
    horizon: str
    after: str | None = None
    before: str | None = None
    top: bool = False
    roadmap: str | None = None
    expect: str | None = None


@dataclass(frozen=True)
class ScheduleResult(Data):
    """Where the entity landed, and what it landed between."""

    entity: str
    roadmap: str
    horizon: str
    index: int
    after: str | None = None
    before: str | None = None


@dataclass(frozen=True)
class UnscheduleParams(Data):
    ref: str
    roadmap: str | None = None
    expect: str | None = None


@dataclass(frozen=True)
class UnscheduleResult(Data):
    id: str
    roadmap: str


@operation("schedule", params=ScheduleParams, result=ScheduleResult, kind="mutation")
def schedule(params: ScheduleParams, project: Project) -> ScheduleResult:
    from ..roadmap import Scheduler

    scheduler = Scheduler(project.config, project.store, params.roadmap, project.ids)
    if params.expect is not None:
        entity_for(project, params.ref, params.expect)
    placement = scheduler.schedule(params.ref, params.horizon, after=params.after,
                                   before=params.before, top=params.top)
    _views.redraw(project)
    return ScheduleResult(placement.entity, placement.roadmap, placement.horizon,
                          placement.index, placement.after, placement.before)


@operation("unschedule", params=UnscheduleParams, result=UnscheduleResult, kind="mutation")
def unschedule(params: UnscheduleParams, project: Project) -> UnscheduleResult:
    from ..roadmap import Scheduler

    scheduler = Scheduler(project.config, project.store, params.roadmap, project.ids)
    entity_id = project.ids.resolve(params.ref)
    if params.expect is not None:
        entity_for(project, params.ref, params.expect)
    if not scheduler.unschedule(entity_id):
        raise BadInput(
            f"{entity_id.text} is not on roadmap {scheduler.roadmap.name!r}.",
            fix=f"szsdlc schedule {entity_id.text} --horizon "
                f"{scheduler.roadmap.spec.horizons[-1]}",
        )
    _views.redraw(project)
    return UnscheduleResult(entity_id.text, scheduler.roadmap.name)
