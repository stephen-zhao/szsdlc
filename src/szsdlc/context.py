"""Compact project state for `SessionStart` and `show --context`.

The design rule this module exists to enforce: **counters in context, details
on demand.** Every condition worth knowing about is a *scalar* here, costing a
handful of tokens at session start. The corresponding listing is only spent
when the scalar is non-zero and the detail is actually wanted.

That distinction is what keeps a migration — where every one of two hundred
entities is unscheduled — from dumping two hundred lines into the context
window before the session has done anything.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from .config import Config
from .graph import Graph
from .model import Entity, EntityStore
from .roadmap import Roadmap, has_reached, load_all as load_roadmaps
from .text import clip

#: Hard cap on the rendered context block. Deterministic, so a session's first
#: tokens cost the same whether the project has twenty entities or two thousand.
CONTEXT_CHAR_CAP = 1800
SHOW_CONTEXT_CHAR_CAP = 2400


@dataclass(frozen=True)
class Counters:
    """Scalars, never lists. Each names a listing worth spending tokens on."""

    entities: int = 0
    inbox: int = 0
    inbox_oldest_days: int = 0
    unscheduled: int = 0
    uncovered_requirements: int = 0
    unparseable: int = 0
    errors: int = 0
    warnings: int = 0

    def rows(self) -> list[tuple[str, int, str]]:
        """(label, value, the command that shows the detail)."""
        return [
            ("entities", self.entities, "szsdlc list"),
            ("inbox", self.inbox, "szsdlc inbox"),
            ("unscheduled", self.unscheduled, "szsdlc list --unscheduled"),
            ("uncovered reqs", self.uncovered_requirements, "szsdlc list --uncovered"),
            ("unparseable", self.unparseable, "szsdlc validate"),
            ("errors", self.errors, "szsdlc validate"),
            ("warnings", self.warnings, "szsdlc validate"),
        ]


@dataclass
class Context:
    counters: Counters = field(default_factory=Counters)
    in_flight: list[Entity] = field(default_factory=list)
    current_task: str | None = None
    current_entity: str | None = None


def age_days(entity: Entity) -> int | None:
    """Days since this entity's first declared date field.

    Read off the type's own fields rather than hunting the frontmatter for
    anything date-shaped, so a project whose intake type records `raised`
    rather than `captured` still gets an age.
    """
    for name, spec in entity.type.fields.items():
        if spec.type != "date":
            continue
        value = entity.data.get(name)
        if isinstance(value, dt.datetime):
            value = value.date()
        if isinstance(value, dt.date):
            return (dt.date.today() - value).days
    return None


def in_flight(store: EntityStore, thresholds: dict[str, str] | None = None) -> list[Entity]:
    """Work actually under way: actionable, started, not finished.

    "Started" means *strictly past* the status at which this type becomes
    schedulable — `ready` is groomed and waiting, not under way. That threshold
    is already declared, per type, in a roadmap's `requires_scheduling`, so this
    reuses the project's own statement of when work becomes real rather than
    inventing a second one. With no threshold declared, past the initial status
    is the best available answer.

    The distinction earns its place: listing everything non-terminal here would
    make `context` a backlog dump, which is exactly what the counters exist to
    avoid.
    """
    thresholds = thresholds or {}
    found = []
    for entity in store.with_flag("actionable"):
        if entity.is_terminal:
            continue
        threshold = thresholds.get(entity.type.name)
        if threshold is None:
            started = entity.status != entity.type.workflow.initial
        else:
            started = (entity.status != threshold
                       and bool(has_reached(entity.type.workflow, entity.status,
                                            threshold)))
        if started:
            found.append(entity)
    return sorted(found, key=lambda e: (e.id.prefix, e.id.number))


def scheduling_thresholds(roadmaps: dict[str, Roadmap]) -> dict[str, str]:
    """type -> the status at which it must be scheduled, across all roadmaps."""
    thresholds: dict[str, str] = {}
    for roadmap in roadmaps.values():
        for type_name, status in roadmap.spec.requires_scheduling.items():
            thresholds.setdefault(type_name, status)
    return thresholds


def first_unchecked(entity: Entity) -> str | None:
    """The next task in this entity's plan, if it has one."""
    if not entity.type.tracks_progress or not entity.type.progress_artifact:
        return None
    text = entity.read_artifact(entity.type.progress_artifact) or ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(("- [ ]", "* [ ]", "+ [ ]")):
            return stripped[5:].strip()
    return None


def build(config: Config, store: EntityStore, graph: Graph,
          roadmaps: dict[str, Roadmap] | None = None) -> Context:
    roadmaps = roadmaps if roadmaps is not None else load_roadmaps(config)
    scheduled: set[str] = {
        id_text for roadmap in roadmaps.values() for id_text in roadmap.all_ids()
    }

    intake = [e for e in store.with_flag("intake") if not e.is_terminal]
    unscheduled = [
        entity for entity in store
        if entity.type.schedulable and not entity.is_terminal
        and entity.id.text not in scheduled
    ]

    uncovered = 0
    for entity in store.with_flag("persistent"):
        for name, declaration in entity.type.derived.items():
            if declaration.kind == "has_incoming" and not graph.derive(entity, name):
                uncovered += 1
                break

    # Imported here rather than at module scope: validate reads this module for
    # `age_days`, and a session's first call should not pay for a cycle.
    from .validate import run as run_validate, summary

    errors, warnings = summary(run_validate(config, store, graph, roadmaps))

    flight = in_flight(store, scheduling_thresholds(roadmaps))
    current_entity = next((e for e in flight if first_unchecked(e)), None)

    return Context(
        counters=Counters(
            entities=len(store),
            inbox=len(intake),
            inbox_oldest_days=max((age_days(e) or 0 for e in intake), default=0),
            unscheduled=len(unscheduled),
            uncovered_requirements=uncovered,
            unparseable=len(store.unparseable),
            errors=errors,
            warnings=warnings,
        ),
        in_flight=flight,
        current_entity=current_entity.id.text if current_entity else None,
        current_task=first_unchecked(current_entity) if current_entity else None,
    )


def render(context: Context, *, cap: int = CONTEXT_CHAR_CAP, limit: int = 8) -> str:
    """The `SessionStart` payload. Deterministic ordering, hard cap."""
    lines: list[str] = []

    if context.in_flight:
        lines.append("in flight:")
        shown = context.in_flight[:limit]
        # In-flight work mixes types by construction, and ids differ in width
        # with the prefix, so the column is measured rather than assumed.
        width = max(len(entity.id.text) for entity in shown)
        for entity in shown:
            lines.append(f"  {entity.id.text:<{width}}  {entity.status}  "
                         f"{clip(entity.title)}")
        if len(context.in_flight) > limit:
            lines.append(f"  … {len(context.in_flight) - limit} more — szsdlc next")

    if context.current_task:
        lines.append(f"current task ({context.current_entity}): {context.current_task}")

    counters = [f"{label} {value}" for label, value, _ in context.counters.rows()]
    lines.append(" | ".join(counters))

    rendered = "\n".join(lines)
    return rendered if len(rendered) <= cap else rendered[:cap].rsplit("\n", 1)[0]


def bundle(config: Config, graph: Graph, entity: Entity,
           *, cap: int = SHOW_CONTEXT_CHAR_CAP) -> str:
    """`show --context` — everything needed to resume this one entity.

    Budgeted rather than complete: a design document pasted in full is how a
    context window gets spent on something the model will re-read anyway.
    """
    lines: list[str] = [f"{entity.id.text}  {entity.status}  {clip(entity.title)}"]

    if entity.tags:
        lines.append(f"tags: {', '.join(entity.tags)}")

    progress = entity.progress
    if progress is not None:
        lines.append(f"progress: {progress} ({progress.percent}%)")

    task = first_unchecked(entity)
    if task:
        lines.append(f"next task: {task}")

    for relation in config.relations_from(entity.type.name):
        targets = graph.targets(entity.id, relation.name)
        if targets:
            lines.append(f"{relation.name}: {', '.join(t.label for t in targets)}")
    for relation in config.relations.values():
        incoming = graph.sources(entity.id, relation.name)
        if incoming:
            lines.append(f"{relation.inverse}: "
                         f"{', '.join(e.id.text for e in incoming)}")

    for artifact in entity.type.artifacts:
        if artifact == entity.type.journal_artifact or not entity.has_artifact(artifact):
            continue
        summary = _summarize(entity.read_artifact(artifact) or "")
        if summary:
            lines.append(f"{artifact}: {summary}")

    journal = entity.type.journal_artifact
    if journal and entity.has_artifact(journal):
        recent = (entity.read_artifact(journal) or "").strip().splitlines()[-3:]
        lines.append("journal:")
        lines.extend(f"  {line.strip()}" for line in recent)

    rendered = "\n".join(lines)
    return rendered if len(rendered) <= cap else rendered[:cap].rsplit("\n", 1)[0]


def _summarize(text: str, *, width: int = 200) -> str:
    """The first prose paragraph, flattened and clipped."""
    paragraph: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            if paragraph:
                break
            continue
        paragraph.append(stripped)
    joined = " ".join(paragraph)
    return joined if len(joined) <= width else joined[:width].rsplit(" ", 1)[0] + "…"
