"""szsdlc standards: the standards that apply, by path or all of them."""

from __future__ import annotations

from dataclasses import dataclass

from ..errors import BadInput
from ._listing import STANDARDS_LIMIT, bounded
from ._paths import relative
from ._project import Project
from ._registry import operation
from ._types import Data


@dataclass(frozen=True)
class StandardRow(Data):
    name: str
    path: str
    applies_to: tuple[str, ...]
    body: str


@dataclass(frozen=True)
class StandardsParams(Data):
    #: "list" for every standard, "match" for those applying to `paths`.
    mode: str = "list"
    paths: tuple[str, ...] = ()
    limit: int = STANDARDS_LIMIT


@dataclass(frozen=True)
class StandardsResult(Data):
    rows: tuple[StandardRow, ...]
    total: int


@operation("standards", params=StandardsParams, result=StandardsResult, kind="query")
def standards(params: StandardsParams, project: Project) -> StandardsResult:
    from .. import standards as standards_module

    if params.mode not in ("list", "match"):
        raise BadInput(f"standards: {params.mode!r} is not list or match.",
                       fix="szsdlc standards list")
    loaded = standards_module.load(project.config)
    if params.mode == "list":
        found = loaded
    else:
        if not params.paths:
            raise BadInput(
                "standards match: no paths given.",
                fix="szsdlc standards match <path>",
            )
        found = standards_module.matching(project.config, list(params.paths), loaded)
    rows, total = bounded([StandardRow(s.name, relative(project.config.root, s.path), tuple(s.applies_to), s.body)
                           for s in found], params.limit)
    return StandardsResult(rows, total)
