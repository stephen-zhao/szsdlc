"""szsdlc render: regenerate every view and record."""

from __future__ import annotations

from dataclasses import dataclass

from . import _queries
from ._paths import relative
from ._project import Project
from ._registry import operation
from ._types import Data


@dataclass(frozen=True)
class RenderParams(Data):
    pass


@dataclass(frozen=True)
class RenderResult(Data):
    #: The generated files this render rewrote.
    changed: tuple[str, ...]


@operation("render", params=RenderParams, result=RenderResult, kind="mutation")
def render(params: RenderParams, project: Project) -> RenderResult:
    """Regenerate every view and record. Never validates, never reaches a remote.

    Strict, unlike the redraw a mutation ends with: asked for by name, a
    template that will not draw is the answer.
    """
    from .. import render as render_module

    changed = render_module.render(project.config, project.store, _queries.graph(project),
                                   _queries.roadmaps(project))
    return RenderResult(tuple(relative(project.config.root, item.path) for item in changed))
