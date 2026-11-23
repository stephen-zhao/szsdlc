"""The graph, the roadmaps and the queries several commands share."""

from __future__ import annotations

from ..errors import BadInput
from ..model import Entity
from ._project import Project


# ---------------------------------------------------------------------------
# Queries
#
# Design rule — counters in context, details on demand. Every listing here is
# bounded by default and every truncation is printed, because a silently capped
# list reads as "nothing more to see".
# ---------------------------------------------------------------------------
def graph(session: Project):
    from ..graph import Graph

    return Graph(session.config, session.store, session.ids)


def roadmaps(session: Project, name: str | None = None):
    from ..roadmap import Roadmap, load_all as load_roadmaps

    if name:
        if name not in session.config.roadmaps:
            raise BadInput(
                f"{name!r} is not a configured roadmap.",
                fix=f"use one of: {', '.join(sorted(session.config.roadmaps))}",
            )
        return {name: Roadmap.load(session.config, name)}
    return load_roadmaps(session.config)


def roadmap_order(roadmaps: dict, horizon: str | None) -> dict[str, int]:
    """id -> sort key, following horizon order then position within it.

    Ordering by the roadmap rather than by a per-entity field is the whole
    reason there is no `priority`: sequence is a property of the list.
    """
    order: dict[str, int] = {}
    position = 0
    for roadmap in roadmaps.values():
        for bucket in roadmap.spec.horizons:
            if horizon and bucket != horizon:
                continue
            for id_text in roadmap.entries(bucket):
                order.setdefault(id_text, position)
                position += 1
    return order


#: The relation whose targets must be terminal before work is actionable.
#: Named once, so `next` and the reopen report cannot disagree about it.
DEPENDENCY_RELATION = "depends_on"


def blocked(session: Project, graph, entity: Entity) -> bool:
    """True while any dependency has not reached a terminal status.

    Saying so here is cheaper than letting somebody start the work and find
    out. An unresolvable dependency does not block — it is a validate finding,
    and treating a typo as a blocker would hide the queue rather than the typo.
    """
    for target in graph.targets(entity.id, DEPENDENCY_RELATION):
        if not target.resolved:
            continue
        dependency = session.store.get(target.entity_id)
        if dependency is not None and not dependency.is_terminal:
            return True
    return False


def is_uncovered(graph, entity: Entity) -> bool:
    """True when a `has_incoming` derived attribute of this entity is false.

    Expressed through the declared derivation rather than by naming
    requirements, so a project with a differently named definitional type gets
    `--uncovered` for free.
    """
    for name, declaration in entity.type.derived.items():
        if declaration.kind == "has_incoming" and not graph.derive(entity, name):
            return True
    return False
