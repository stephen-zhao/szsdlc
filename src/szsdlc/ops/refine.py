"""szsdlc refine: turn a captured idea into a typed entity."""

from __future__ import annotations

from dataclasses import dataclass

from ..errors import BadInput
from ..model import Entity, create_entity
from ..text import clip
from . import _views
from ._precondition import entity_for
from ._project import Project
from ._registry import operation
from ._types import Data


@dataclass(frozen=True)
class RefineParams(Data):
    ref: str
    into: str
    title: str | None = None
    expect: str | None = None


@dataclass(frozen=True)
class RefineResult(Data):
    #: The new entity.
    id: str
    #: The idea it came from, and that idea's status before and after.
    source: str
    before: str | None
    after: str | None


@operation("refine", params=RefineParams, result=RefineResult, kind="mutation")
def refine(params: RefineParams, project: Project) -> RefineResult:
    idea = entity_for(project, params.ref, params.expect)
    if not idea.type.intake:
        raise BadInput(
            f"{idea.id.text} is a {idea.type.name}, not an intake entity, so it "
            f"cannot be refined.",
            # Clipped: the title may be a whole captured paragraph, and a
            # refusal that runs to 200 characters has stopped being a fix.
            fix=f"szsdlc new {params.into} --title \"{clip(idea.title)}\"",
        )
    target = project.config.type_for(params.into)
    if target.intake:
        typed = next((t.name for t in project.config.entity_types.values()
                      if not t.intake), "work_item")
        raise BadInput(
            f"{target.name} is itself an intake type, so refining into it would "
            f"only make another idea.",
            fix=f"szsdlc refine {idea.id.text} --into {typed}",
        )
    workflow = idea.type.workflow
    if idea.status == workflow.abandoned_status:
        # Dropping is somebody deciding this goes nowhere. Reviving it is a
        # deliberate act, not a side effect of refining.
        raise BadInput(
            f"{idea.id.text} is {idea.status}; a dropped idea is not refined further.",
            fix=f"szsdlc set {idea.id.text} status={workflow.initial}",
        )

    relation = _refined_from_relation(project, idea, target)
    child = create_entity(
        project.config, project.ids, target,
        title=params.title or idea.title,
        relations={relation: idea.id.text} if relation else None,
    )

    # One idea routinely yields several entities, so this is deliberately not a
    # one-shot: refining again from an already-refined idea adds another child.
    # The idea itself is provenance and stays exactly where it is.
    before = idea.status
    after = workflow.completed_status
    if after and before != after:
        idea.set_status(after)
        idea.save()
    _views.redraw(project)
    return RefineResult(child.id.text, idea.id.text, before, idea.status)


def _refined_from_relation(project: Project, idea: Entity, target) -> str | None:
    """The relation the child uses to point back at its idea.

    Found by shape rather than by name: exactly one relation may be authored by
    the target type and points at the intake type. Hardcoding `refined_from`
    would make provenance a framework concept rather than a configured one.
    """
    candidates = [
        relation for relation in project.config.relations_from(target.name)
        if idea.type.name in relation.target_types and relation.is_single
    ]
    if not candidates:
        raise BadInput(
            f"no relation lets a {target.name} point at a {idea.type.name}, so the "
            f"idea could not be recorded as its provenance.",
            fix="declare one under relations: in .szsdlc/config.yml",
        )
    return candidates[0].name
