"""szsdlc convert: change an entity's type; the old id is tombstoned."""

from __future__ import annotations

import shutil
from dataclasses import dataclass

from ..errors import BadInput
from ..model import create_entity
from . import _views
from ._precondition import entity_for
from ._project import Project
from ._registry import operation
from ._types import Data


@dataclass(frozen=True)
class ConvertParams(Data):
    ref: str
    type: str
    expect: str | None = None


@dataclass(frozen=True)
class ConvertResult(Data):
    #: The new entity's id; the old one is tombstoned and still resolves.
    id: str
    was: str
    status: str


@operation("convert", params=ConvertParams, result=ConvertResult, kind="mutation")
def convert(params: ConvertParams, project: Project) -> ConvertResult:
    entity = entity_for(project, params.ref, params.expect)
    target_type = project.config.type_for(params.type)
    if target_type.name == entity.type.name:
        raise BadInput(
            f"convert: {entity.id.text} is already a {target_type.name}.",
            fix=f"szsdlc show {entity.id.text}",
        )

    extra = [p.name for p in entity.artifact_files()] if entity.home else []
    if extra and not target_type.carries_artifacts:
        raise BadInput(
            f"convert: a {target_type.name} has nowhere to put an artifact, "
            f"but {entity.id.text} carries {', '.join(extra)}.",
            fix=f"remove those artifacts, then rerun",
        )
    # Artifacts decide the child's layout when the type leaves it open. The
    # alternative is creating it in the cheapest one and dropping them.
    landing = "directory" if extra and target_type.is_dynamic_layout else None

    new_id = project.ids.next_id(target_type)
    # A status that exists in both workflows is kept; otherwise the entity
    # restarts, because a status from another workflow means nothing here.
    status = (entity.status if entity.status in target_type.workflow.states
              else target_type.workflow.initial)
    child = create_entity(
        project.config, project.ids, target_type,
        title=entity.title, body=entity.body, status=status,
        tags=entity.tags,
        relations={k: (v[0] if project.config.relations[k].is_single else v)
                   for k, v in entity.relations.items()
                   if k in {r.name for r in project.config.relations_from(target_type.name)}},
        layout=landing,
    )
    assert child.id == new_id
    if entity.home is not None and child.home is not None:
        for artifact in entity.artifact_files():
            shutil.copy2(artifact, child.home / artifact.name)
    entity.delete()

    tombstones = project.ids.tombstones
    tombstones.record(entity.id.text, child.id.text)
    tombstones.save()
    _views.redraw(project)
    return ConvertResult(child.id.text, entity.id.text, status)
