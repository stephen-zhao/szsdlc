"""szsdlc layout: change what an entity occupies on disk."""

from __future__ import annotations

from dataclasses import dataclass

from ..config import LAYOUTS
from ..errors import BadInput
from ..model import relayout
from . import _views
from ._paths import display_path
from ._precondition import entity_for
from ._project import Project
from ._registry import operation
from ._types import Data


#
# Named for the thing it changes, not the thing it does to the disk. `layout`
# is already the word in the config key, in the schema and on the entity. It is
# `convert` for the other axis: convert changes an entry's type, layout
# changes what it occupies.

@dataclass(frozen=True)
class LayoutParams(Data):
    ref: str
    layout: str
    expect: str | None = None


@dataclass(frozen=True)
class LayoutResult(Data):
    id: str
    before: str
    after: str
    path: str


@operation("layout", params=LayoutParams, result=LayoutResult, kind="mutation")
def layout(params: LayoutParams, project: Project) -> LayoutResult:
    entity = entity_for(project, params.ref, params.expect)
    target = params.layout
    if target not in LAYOUTS:
        raise BadInput(f"layout: {target!r} is not one of {', '.join(LAYOUTS)}.",
                       fix=f"szsdlc layout {entity.id.text} directory")
    if not entity.type.is_dynamic_layout:
        raise BadInput(
            f"layout: every {entity.type.name} is laid out as a "
            f"{entity.type.layout}, so there is no other layout to give it.",
            fix=f"set entity_types.{entity.type.name}.layout to dynamic",
        )
    if target == entity.layout:
        raise BadInput(
            f"layout: {entity.id.text} is already laid out as a {target}.",
            fix=f"szsdlc show {entity.id.text}",
        )
    carried = [p.name for p in entity.artifact_files()]
    if carried and target != "directory":
        raise BadInput(
            f"layout: a {target} has nowhere to put an artifact, but "
            f"{entity.id.text} carries {', '.join(carried)}.",
            fix="remove those artifacts, then rerun",
        )
    before = entity.layout
    laid = relayout(project.config, project.ids, entity, target)
    _views.redraw(project)
    return LayoutResult(laid.id.text, before, target, display_path(project, laid))
