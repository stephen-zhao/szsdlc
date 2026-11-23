"""szsdlc drop: abandon an entity, with a reason."""

from __future__ import annotations

from dataclasses import dataclass

from ..errors import BadInput
from . import _status, _views
from ._precondition import entity_for
from ._project import Project
from ._registry import operation
from ._types import Data


@dataclass(frozen=True)
class DropParams(Data):
    ref: str
    reason: str
    expect: str | None = None


@dataclass(frozen=True)
class DropResult(Data):
    id: str
    before: str | None
    after: str
    notes: tuple[str, ...] = ()


@operation("drop", params=DropParams, result=DropResult, kind="mutation")
def drop(params: DropParams, project: Project) -> DropResult:
    from .. import workflow as workflow_module

    entity = entity_for(project, params.ref, params.expect)
    reason = (params.reason or "").strip()
    if not reason:
        raise BadInput(
            f"{entity.id.text}: dropping needs a reason, so the same thought "
            f"arriving twice is recognisable.",
            fix=f'szsdlc drop {entity.id.text} --reason "already covered by WI-0001"',
        )
    status = entity.type.workflow.abandoned_status
    if status is None:
        terminal = ", ".join(entity.type.workflow.terminal_states) or "none"
        raise BadInput(
            f"{entity.type.name} declares no abandoned status, so there is "
            f"nowhere to drop it to.",
            fix=f"mark one terminal state of {entity.type.name} `abandoned: true`",
            see=f"terminal statuses: {terminal}",
        )
    before = entity.status
    if before == status:
        raise BadInput(
            f"{entity.id.text} is already {status}.",
            fix=f"szsdlc show {entity.id.text}",
        )

    # Through the same checked move as `set`: the workflow's edges and the
    # target's gates apply, and so do the effects of reaching a terminal state.
    was_terminal = entity.is_terminal
    workflow_module.move(entity, status)
    if "dropped_reason" in entity.type.fields:
        entity.set_field("dropped_reason", reason)
    notes = _status.after_status_change(project, entity, was_terminal)
    entity.save()
    _views.redraw(project)
    return DropResult(entity.id.text, before, status, tuple(notes))
