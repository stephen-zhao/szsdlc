"""szsdlc log: append dated lines to an entity's journal."""

from __future__ import annotations

from dataclasses import dataclass

from ..errors import BadInput
from ..model import relayout
from . import _journal, _views
from ._paths import relative
from ._precondition import entity_for
from ._project import Project
from ._registry import operation
from ._types import Data


@dataclass(frozen=True)
class LogParams(Data):
    ref: str
    message: str
    expect: str | None = None


@dataclass(frozen=True)
class LogResult(Data):
    id: str
    #: The journal written to.
    path: str


@operation("log", params=LogParams, result=LogResult, kind="mutation")
def log(params: LogParams, project: Project) -> LogResult:
    entity = entity_for(project, params.ref, params.expect)
    artifact = entity.type.journal_artifact
    if not artifact:
        raise BadInput(
            f"{entity.type.name} declares no journal artifact, so there is "
            f"nowhere to log to.",
            fix=f"set entity_types.{entity.type.name}.journal_artifact",
        )
    if entity.home is None and entity.type.is_dynamic_layout:
        # The only fix for the refusal would have been `layout … directory`,
        # and a refusal whose fix is one unconditional command is a step nobody
        # should have to type. Wanting a journal *is* earning the directory.
        entity = relayout(project.config, project.ids, entity, "directory")
    if entity.home is None:
        raise BadInput(
            f"a {entity.type.name} is stored as a {entity.layout}, which has "
            f"nowhere to keep {artifact}.",
            fix=f"set entity_types.{entity.type.name}.layout to directory",
        )
    message = params.message.strip()
    if not message:
        raise BadInput(
            f"log: nothing to write.",
            fix=f'szsdlc log {entity.id.text} "what happened"',
        )
    _journal.append_journal(entity, artifact, message)
    _views.redraw(project)
    return LogResult(entity.id.text, relative(project.config.root, entity.artifact_path(artifact)))
