"""The read-only operations: show, list, next, inbox, trace, context, standards, validate.

Each returns data and never prints. A listing takes `limit` (0 for all) and
returns `total`, so a caller always knows when it was shown less than there is.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..errors import BadInput
from ..text import add_tags
from . import _queries
from ._project import Project
from ._registry import operation
from ._types import Data

#: C6 — every listing is bounded, and the bound is the same everywhere.
DEFAULT_LIMIT = 20
#: `next` is tighter still: it answers "what now", and twenty things is not an answer.
NEXT_LIMIT = 10
STANDARDS_LIMIT = 10


def bounded(rows: list[Any], limit: int) -> tuple[tuple[Any, ...], int]:
    """The first `limit` rows (all of them for 0) and how many there were."""
    shown = rows if limit <= 0 else rows[:limit]
    return tuple(shown), len(rows)


def _relative(project: Project, path) -> str:
    try:
        return str(path.relative_to(project.config.root)).replace("\\", "/")
    except ValueError:  # pragma: no cover - paths are always under the root
        return str(path)


# --------------------------------------------------------------------- show

@dataclass(frozen=True)
class ShowParams(Data):
    ref: str
    #: Also build the context bundle an agent reads before working on it.
    context: bool = False


@dataclass(frozen=True)
class ShowResult(Data):
    id: str
    type: str
    status: str | None
    title: str
    tags: tuple[str, ...]
    relations: dict[str, Any]
    #: Keyed off the fields the *type* declares, so the key set is the type's contract.
    fields: dict[str, Any]
    derived: dict[str, str]
    path: str
    #: The entity's file as it stands: frontmatter and body.
    text: str
    #: The content hash a later mutation passes as `expect`.
    hash: str
    bundle: str | None = None


@operation("show", params=ShowParams, result=ShowResult, kind="query")
def show(params: ShowParams, project: Project) -> ShowResult:
    from .. import context as context_module

    entity = project.entity(params.ref)
    graph = _queries.graph(project)
    return ShowResult(
        id=entity.id.text, type=entity.type.name, status=entity.status,
        title=entity.title, tags=tuple(entity.tags), relations=dict(entity.relations),
        fields={name: entity.field(name) for name in entity.type.fields},
        derived={k: str(v) for k, v in graph.derived(entity).items()},
        path=_relative(project, entity.path), text=entity.render(),
        hash=entity.content_hash(),
        bundle=(context_module.bundle(project.config, graph, entity)
                if params.context else None),
    )


# --------------------------------------------------------------------- list

@dataclass(frozen=True)
class EntityRow(Data):
    id: str
    type: str
    status: str | None
    title: str
    tags: tuple[str, ...]
    fields: dict[str, Any]


@dataclass(frozen=True)
class ListParams(Data):
    type: str | None = None
    status: str | None = None
    tags: tuple[str, ...] = ()
    parent: str | None = None
    uncovered: bool = False
    unscheduled: bool = False
    limit: int = DEFAULT_LIMIT


@dataclass(frozen=True)
class ListResult(Data):
    rows: tuple[EntityRow, ...]
    total: int


@operation("list", params=ListParams, result=ListResult, kind="query")
def list_(params: ListParams, project: Project) -> ListResult:
    graph = _queries.graph(project)
    entities = list(project.store)

    if params.type:
        project.config.type_for(params.type)  # refuses with the known types
        entities = [e for e in entities if e.type.name == params.type]
    if params.status:
        entities = [e for e in entities if e.status == params.status]
    for tag in params.tags:
        normalized = add_tags([], [tag])
        entities = [e for e in entities if set(normalized) <= set(e.tags)]
    if params.parent:
        parent_id = project.ids.resolve(params.parent).text
        relation = project.config.data["parent_relation"]
        entities = [e for e in entities if parent_id in e.targets(relation)]
    if params.uncovered:
        entities = [e for e in entities if _queries.is_uncovered(graph, e)]
    if params.unscheduled:
        scheduled = {i for r in _queries.roadmaps(project).values() for i in r.all_ids()}
        entities = [e for e in entities
                    if e.type.schedulable and not e.is_terminal
                    and e.id.text not in scheduled]

    entities.sort(key=lambda e: (e.id.prefix, e.id.number))
    rows, total = bounded([
        EntityRow(e.id.text, e.type.name, e.status, e.title, tuple(e.tags),
                  {name: e.field(name) for name in e.type.fields})
        for e in entities], params.limit)
    return ListResult(rows, total)


# --------------------------------------------------------------------- next

@dataclass(frozen=True)
class NextRow(Data):
    id: str
    status: str | None
    title: str
    #: On a roadmap, as against queued after everything that is.
    scheduled: bool


@dataclass(frozen=True)
class NextParams(Data):
    horizon: str | None = None
    parent: str | None = None
    roadmap: str | None = None
    limit: int = NEXT_LIMIT


@dataclass(frozen=True)
class NextResult(Data):
    rows: tuple[NextRow, ...]
    total: int


@operation("next", params=NextParams, result=NextResult, kind="query")
def next_(params: NextParams, project: Project) -> NextResult:
    graph = _queries.graph(project)
    roadmaps = _queries.roadmaps(project, params.roadmap)
    order = _queries.roadmap_order(roadmaps, params.horizon)
    parent_id = project.ids.resolve(params.parent).text if params.parent else None
    parent_relation = project.config.data["parent_relation"]

    candidates = []
    for entity in project.store.with_flag("actionable"):
        if entity.is_terminal:
            continue
        if params.horizon and entity.id.text not in order:
            continue
        if parent_id and parent_id not in entity.targets(parent_relation):
            continue
        if _queries.blocked(project, graph, entity):
            continue
        candidates.append(entity)

    # Scheduled work first, in roadmap sequence; everything else after, by id.
    candidates.sort(key=lambda e: (order.get(e.id.text, len(order)),
                                   e.id.prefix, e.id.number))
    rows, total = bounded([NextRow(e.id.text, e.status, e.title, e.id.text in order)
                           for e in candidates], params.limit)
    return NextResult(rows, total)


# -------------------------------------------------------------------- inbox

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


# -------------------------------------------------------------------- trace

@dataclass(frozen=True)
class TraceRow(Data):
    source: str
    relation: str
    target: str


@dataclass(frozen=True)
class TraceParams(Data):
    ref: str
    depth: int = 2
    limit: int = DEFAULT_LIMIT


@dataclass(frozen=True)
class TraceResult(Data):
    rows: tuple[TraceRow, ...]
    total: int


@operation("trace", params=TraceParams, result=TraceResult, kind="query")
def trace(params: TraceParams, project: Project) -> TraceResult:
    graph = _queries.graph(project)
    entity_id = project.ids.resolve(params.ref)
    edges = graph.trace(entity_id, depth=params.depth)
    rows, total = bounded([TraceRow(e.source.text, e.kind, e.target.label) for e in edges],
                          params.limit)
    return TraceResult(rows, total)


# ------------------------------------------------------------------ context

@dataclass(frozen=True)
class InFlightRow(Data):
    id: str
    status: str | None
    title: str


@dataclass(frozen=True)
class ContextParams(Data):
    pass


@dataclass(frozen=True)
class ContextResult(Data):
    #: The pack an agent reads at session start, as text: that is what it is for.
    text: str
    counters: dict[str, Any]
    in_flight: tuple[InFlightRow, ...]
    current_entity: Any = None
    current_task: Any = None


@operation("context", params=ContextParams, result=ContextResult, kind="query")
def context(params: ContextParams, project: Project) -> ContextResult:
    from .. import context as context_module

    built = context_module.build(project.config, project.store, _queries.graph(project),
                                 _queries.roadmaps(project))
    return ContextResult(
        text=context_module.render(built),
        counters={label: value for label, value, _ in built.counters.rows()},
        in_flight=tuple(InFlightRow(e.id.text, e.status, e.title) for e in built.in_flight),
        current_entity=built.current_entity, current_task=built.current_task,
    )


# ---------------------------------------------------------------- standards

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
    rows, total = bounded([StandardRow(s.name, _relative(project, s.path), tuple(s.applies_to), s.body)
                           for s in found], params.limit)
    return StandardsResult(rows, total)


# ----------------------------------------------------------------- validate

@dataclass(frozen=True)
class FindingRow(Data):
    level: str
    kind: str
    ref: str
    message: str
    fix: str | None


@dataclass(frozen=True)
class ValidateParams(Data):
    limit: int = DEFAULT_LIMIT


@dataclass(frozen=True)
class ValidateResult(Data):
    """Findings, most severe first. Errors and warnings count all of them, not just those shown."""

    rows: tuple[FindingRow, ...]
    total: int
    errors: int
    warnings: int

    @property
    def ok(self) -> bool:
        return self.errors == 0


@operation("validate", params=ValidateParams, result=ValidateResult, kind="query")
def validate(params: ValidateParams, project: Project) -> ValidateResult:
    from .. import validate as validate_module

    findings = validate_module.run(project.config, project.store, _queries.graph(project),
                                   _queries.roadmaps(project))
    errors, warnings = validate_module.summary(findings)
    rows, total = bounded([FindingRow(f.level, f.kind, f.ref, f.message, f.fix) for f in findings],
                          params.limit)
    return ValidateResult(rows, total, errors, warnings)
