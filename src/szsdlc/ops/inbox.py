"""szsdlc inbox: unrefined captures, oldest first."""

from __future__ import annotations

from dataclasses import dataclass

from ._listing import DEFAULT_LIMIT, bounded
from ._project import Project
from ._registry import operation
from ._types import Data


@dataclass(frozen=True)
class InboxRow(Data):
    id: str
    status: str | None
    age_days: int | None
    title: str


@dataclass(frozen=True)
class InboxParams(Data):
    limit: int = DEFAULT_LIMIT


@dataclass(frozen=True)
class InboxResult(Data):
    rows: tuple[InboxRow, ...]
    total: int


@operation("inbox", params=InboxParams, result=InboxResult, kind="query")
def inbox(params: InboxParams, project: Project) -> InboxResult:
    from ..context import age_days

    unrefined = [entity for entity in project.store.with_flag("intake")
                 if not entity.is_terminal]
    # Oldest first: an inbox sorted newest-first is an inbox whose bottom never
    # gets drained.
    unrefined.sort(key=lambda e: (-(age_days(e) or 0), e.id.prefix, e.id.number))
    rows, total = bounded([InboxRow(e.id.text, e.status, age_days(e), e.title)
                           for e in unrefined], params.limit)
    return InboxResult(rows, total)
