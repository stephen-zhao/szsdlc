"""szsdlc tag / untag: add or remove tags."""

from __future__ import annotations

from dataclasses import dataclass

from ..text import add_tags, remove_tags
from . import _views
from ._precondition import entity_for
from ._project import Project
from ._registry import operation
from ._types import Data


@dataclass(frozen=True)
class TagParams(Data):
    ref: str
    tags: tuple[str, ...]
    expect: str | None = None


@dataclass(frozen=True)
class TagResult(Data):
    id: str
    #: Every tag the entity has now.
    tags: tuple[str, ...]


def _apply_tags(params: TagParams, project: Project, adding: bool) -> TagResult:
    entity = entity_for(project, params.ref, params.expect)
    before = entity.tags
    entity.set_tags(add_tags(before, params.tags) if adding else remove_tags(before, params.tags))
    entity.save()
    _views.redraw(project)
    return TagResult(entity.id.text, tuple(entity.tags))


@operation("tag", params=TagParams, result=TagResult, kind="mutation")
def tag(params: TagParams, project: Project) -> TagResult:
    return _apply_tags(params, project, adding=True)


@operation("untag", params=TagParams, result=TagResult, kind="mutation")
def untag(params: TagParams, project: Project) -> TagResult:
    return _apply_tags(params, project, adding=False)
