"""Preconditions: refuse to write over a change made since the caller read."""

from __future__ import annotations

from ..errors import StalePrecondition
from ..model import Entity
from ._project import Project


def check(entity: Entity, expect: str | None) -> None:
    """Refuse when `expect` is given and `entity` no longer hashes to it.

    Called before anything is written. The CLI never passes `expect`: a
    command runs on what is there. A caller that showed something, then
    writes it, passes the `hash` it was shown.
    """
    if expect is not None and entity.content_hash() != expect:
        raise StalePrecondition(
            f"{entity.id.text} changed since it was read.",
            fix=f"szsdlc show {entity.id.text}",
        )


def entity_for(project: Project, ref: str, expect: str | None) -> Entity:
    """The entity `ref` names, refused as stale if `expect` no longer matches it."""
    entity = project.entity(ref)
    check(entity, expect)
    return entity
