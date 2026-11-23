"""szsdlc new: one entity, at the workflow's initial status or an imported one."""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import Any

from ..errors import BadInput
from ..model import create_entity
from . import _views
from ._paths import relative
from ._project import Project
from ._registry import operation
from ._types import Data


@dataclass(frozen=True)
class NewParams(Data):
    type: str
    title: str
    #: The import path for existing history; checked against the workflow,
    #: but its entry gates are not (a new entity has no artifacts).
    status: str | None = None


@dataclass(frozen=True)
class NewResult(Data):
    id: str
    path: str
    status: str | None
    #: The gates `status` has not met yet, which `validate` will report.
    owes: tuple[str, ...] = ()
    #: Whether `status` was given, as against left to the workflow.
    imported: bool = False


@operation("new", params=NewParams, result=NewResult, kind="mutation")
def new(params: NewParams, project: Project) -> NewResult:
    from ..workflow import unmet_gates

    entity_type = project.config.type_for(params.type)
    status = params.status
    if status is not None:
        _check_import_status(entity_type, params.title, status)
    entity = create_entity(project.config, project.ids, entity_type,
                           title=params.title, status=status)
    owes = tuple(gate.reason for gate in unmet_gates(entity, status)) if status is not None else ()
    _views.redraw(project)
    return NewResult(entity.id.text, relative(project.config.root, entity.path), entity.status, owes,
                     imported=status is not None)


def _check_import_status(entity_type: Any, title: str, status: str) -> None:
    """Refuse a `new --status` this type cannot be created at."""
    # Every value in a fix is quoted, and the title is passed so argparse can
    # never read it as an option (`--title=…`, `-- text`): the fix is meant to
    # be run as printed, whatever the caller typed.
    quoted = shlex.quote(title if title.strip() else "<text>")
    name = shlex.quote(entity_type.name)
    if entity_type.intake:
        raise BadInput(
            f"new: {entity_type.name} is an intake type; its status is written "
            f"by capture, refine and drop, never by --status.",
            fix=f"szsdlc capture --type {name} -- {quoted}",
        )
    workflow = entity_type.workflow
    if status not in workflow.states:
        terminal = workflow.completed_status or workflow.initial
        raise BadInput(
            f"new: {status!r} is not a status of {entity_type.name}.",
            fix=f"szsdlc new {name} --title={quoted} --status {shlex.quote(terminal)}",
            see=f"statuses: {', '.join(workflow.states)}",
        )
