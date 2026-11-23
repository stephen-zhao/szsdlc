"""szsdlc set: scalar fields and the status, through the workflow."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

from ..errors import BadInput
from ..model import Entity
from ..text import nearest
from . import _status, _views
from ._precondition import entity_for
from ._project import Project
from ._registry import operation
from ._types import Data


#: C3 — one job per command. These have their own verbs, and composing them on
#: a command line is exactly the input that takes an agent three attempts.
DELEGATED_FIELDS = {
    "relations": "szsdlc link {ref} <relation> <ref>",
    "tags": "szsdlc tag {ref} <tag>",
    "id": "szsdlc convert {ref} <TYPE>",
}


@dataclass(frozen=True)
class SetParams(Data):
    ref: str
    #: `field=value`, as typed, in order.
    assignments: tuple[str, ...]
    #: Override the workflow for a status= assignment; needs `reason`.
    force: bool = False
    reason: str | None = None
    expect: str | None = None


@dataclass(frozen=True)
class Change(Data):
    field: str
    before: Any
    after: Any
    #: Set when a status move overrode the workflow.
    forced: str | None = None
    #: What the change implied: "off main", "blocks again: WI-0012", …
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class SetResult(Data):
    id: str
    changes: tuple[Change, ...]


def _coerce(entity: Entity, field: str, raw: str) -> Any:
    spec = entity.type.fields.get(field)
    if spec is None:
        return raw
    try:
        if spec.type == "date":
            return dt.date.fromisoformat(raw)
        if spec.type == "integer":
            return int(raw)
        if spec.type == "number":
            return float(raw)
        if spec.type == "boolean":
            if raw.lower() not in {"true", "false"}:
                raise ValueError(raw)
            return raw.lower() == "true"
    except ValueError:
        raise BadInput(
            f"{entity.id.text}: {field}={raw!r} is not a valid {spec.type}.",
            fix=f"szsdlc set {entity.id.text} {field}=<{spec.type}>",
        ) from None
    if spec.type == "array":
        raise BadInput(
            f"set: {field!r} is list-valued and is never edited through `set`.",
            fix=f"edit {field} in the entity's frontmatter directly",
        )
    if spec.enum and raw not in [str(v) for v in spec.enum]:
        raise BadInput(
            f"{entity.id.text}: {field}={raw!r} is not one of "
            f"{', '.join(str(v) for v in spec.enum)}.",
            fix=f"szsdlc set {entity.id.text} {field}={spec.enum[0]}",
        )
    return raw


@operation("set", params=SetParams, result=SetResult, kind="mutation")
def set_(params: SetParams, project: Project) -> SetResult:
    from .. import workflow as workflow_module

    entity = entity_for(project, params.ref, params.expect)
    settable = ["title", "status", *entity.type.fields]
    changes: list[Change] = []
    reason = _status.force_reason(entity, params.assignments, params.force, params.reason)

    for assignment in params.assignments:
        field, separator, raw = assignment.partition("=")
        if not separator:
            raise BadInput(
                f"set: {assignment!r} is not field=value.",
                fix=f"szsdlc set {entity.id.text} "
                    f"status={workflow_module.suggested_next(entity)}",
            )

        field = field.strip()
        if field in DELEGATED_FIELDS:
            raise BadInput(
                f"set: {field!r} is not settable.",
                fix=DELEGATED_FIELDS[field].format(ref=entity.id.text),
            )
        if field not in settable:
            suggestion = nearest(field, settable)
            raise BadInput(
                f"set: {entity.type.name} has no field {field!r}"
                + (f"; did you mean {suggestion}?" if suggestion else "."),
                fix=f"szsdlc set {entity.id.text} {suggestion or settable[0]}=<value>",
            )

        if field == "status":
            was_terminal = entity.is_terminal
            transition = workflow_module.move(entity, raw, force=params.force)
            if transition.forced:
                assert reason is not None
                _status.record_forced_move(entity, transition, reason)
            notes = _status.after_status_change(project, entity, was_terminal)
            changes.append(Change("status", transition.before, transition.after,
                                  reason if transition.forced else None, tuple(notes)))
        else:
            before = entity.field(field)
            entity.set_field(field, _coerce(entity, field, raw))
            changes.append(Change(field, before, entity.field(field)))

    entity.save()
    _views.redraw(project)
    return SetResult(entity.id.text, tuple(changes))
