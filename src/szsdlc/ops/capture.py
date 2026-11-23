"""szsdlc capture: a thought into an intake type."""

from __future__ import annotations

from dataclasses import dataclass

from ..errors import BadInput
from ..model import create_entity
from . import _views
from ._project import Project
from ._registry import operation
from ._types import Data


@dataclass(frozen=True)
class CaptureParams(Data):
    text: str
    #: The intake type, when a project declares more than one.
    type: str | None = None


@dataclass(frozen=True)
class CaptureResult(Data):
    id: str


@operation("capture", params=CaptureParams, result=CaptureResult, kind="mutation")
def capture(params: CaptureParams, project: Project) -> CaptureResult:
    entity_type = project.intake_type(params.type)
    body = params.text.strip()
    if not body:
        raise BadInput(
            "nothing to capture: the input was empty.",
            fix='szsdlc capture "the thought"',
        )
    entity = create_entity(project.config, project.ids, entity_type, body=body + "\n")
    _views.redraw(project)
    return CaptureResult(entity.id.text)
