"""The relation graph, and everything computed from it rather than stored.

Relations are authored on one side only; every inverse here is generated and
never written to a file. The same principle extends to attributes: a
requirement's coverage, an epic's progress and an idea's spawned children are
*derived from edges at read time*, because storing them would recreate exactly
the stale-state problem this framework exists to remove. Nothing in this module
writes to disk.

The other half of the design is that invalidity is representable. An edge is
``(source, kind, target-string)`` with the target held **as written** and
resolved lazily — you cannot report a dangling reference you refused to
represent, and `sync` must render a graph that has them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Iterator

from .config import Config, Relation
from .errors import BadInput, InternalError
from .ids import EntityId, IdSpace, Tombstones
from .model import Entity, EntityStore, Progress


# ---------------------------------------------------------------------------
# Targets and edges
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Target:
    """A relation target, held as written and resolved lazily."""

    text: str
    entity_id: EntityId | None = None
    exists: bool = False
    #: The id originally written, when a tombstone redirected the lookup.
    redirected_from: str | None = None
    error: str | None = None

    @property
    def resolved(self) -> bool:
        return self.exists and self.entity_id is not None

    @property
    def label(self) -> str:
        """How a view shows it. Degrading visibly is the whole point: dropping
        a broken edge would let `sync` conceal what `validate` exists to find."""
        if self.resolved:
            return self.entity_id.text  # type: ignore[union-attr]
        if self.entity_id is not None:
            return f"{self.entity_id.text} (missing)"
        return f"{self.text} (unresolvable)"

    def __str__(self) -> str:
        return self.label


@dataclass(frozen=True)
class Edge:
    source: EntityId
    kind: str
    target: Target
    #: True for the reverse of an authored edge. Generated edges exist in the
    #: graph and never on disk.
    generated: bool = False

    def __str__(self) -> str:
        arrow = "<-" if self.generated else "->"
        return f"{self.source.text} {self.kind} {arrow} {self.target.label}"


@dataclass(frozen=True)
class Finding:
    """A structural problem, stated about the project rather than raised."""

    kind: str
    ref: str
    message: str
    fix: str | None = None

    def __str__(self) -> str:
        return f"{self.ref}: {self.message}"


# ---------------------------------------------------------------------------
# Derived values
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Aggregate:
    """An epic's rolled-up state. Never stored, so it can never go stale."""

    children: int = 0
    terminal: int = 0
    tasks: Progress = Progress(0, 0)

    @property
    def percent(self) -> int:
        """Measured in children completed, not checkboxes.

        A child is the epic's unit of work. Counting tasks instead would let
        one meticulously planned work item outvote five delivered ones, and
        would change an epic's percentage whenever a child's plan was edited.
        `tasks` is carried alongside for detail.
        """
        if not self.children:
            return 0
        return round(self.terminal / self.children * 100)

    @property
    def complete(self) -> bool:
        return self.children > 0 and self.terminal == self.children

    def __str__(self) -> str:
        return f"{self.terminal}/{self.children}"


# ---------------------------------------------------------------------------
# The graph
# ---------------------------------------------------------------------------


class Graph:
    """Every entity and every edge, keyed by canonical id."""

    def __init__(self, config: Config, store: EntityStore,
                 ids: IdSpace | None = None, tombstones: Tombstones | None = None):
        self.config = config
        self.store = store
        self.ids = ids or IdSpace(config, tombstones)
        self._targets: dict[str, Target] = {}
        self._edges: list[Edge] = []
        self._out: dict[EntityId, list[Edge]] = {}
        self._in: dict[EntityId, list[Edge]] = {}
        self._build()

    # -- construction -------------------------------------------------------

    def _resolve(self, text: str) -> Target:
        if text in self._targets:
            return self._targets[text]

        # A parse failure here is *data*, not control flow: an id nobody could
        # parse is exactly the kind of broken reference that must be reportable.
        try:
            entity_id = self.ids.parse(text)
        except BadInput as exc:
            target = Target(text=text, error=exc.problem)
            self._targets[text] = target
            return target

        redirected_from = None
        if entity_id.text in self.ids.tombstones:
            successor = self.ids.tombstones.resolve(entity_id.text)
            if successor != entity_id.text:
                try:
                    redirected_from, entity_id = entity_id.text, self.ids.parse(successor)
                except BadInput as exc:
                    target = Target(text=text, error=exc.problem)
                    self._targets[text] = target
                    return target

        target = Target(
            text=text,
            entity_id=entity_id,
            exists=entity_id in self.store,
            redirected_from=redirected_from,
        )
        self._targets[text] = target
        return target

    def _add(self, edge: Edge) -> None:
        self._edges.append(edge)
        self._out.setdefault(edge.source, []).append(edge)
        if edge.target.entity_id is not None:
            self._in.setdefault(edge.target.entity_id, []).append(edge)

    def _build(self) -> None:
        for entity in self.store:
            for kind, targets in entity.relations.items():
                for text in targets:
                    self._add(Edge(entity.id, kind, self._resolve(str(text))))

        # Inverses are generated here and nowhere else. An authored edge whose
        # target does not resolve produces no inverse — there is nothing to
        # hang it on — but the authored edge itself is kept, so it is still
        # reportable.
        for edge in list(self._edges):
            relation = self.config.relations.get(edge.kind)
            if relation is None or not edge.target.resolved:
                continue
            self._add(Edge(
                source=edge.target.entity_id,  # type: ignore[arg-type]
                kind=relation.inverse,
                target=Target(text=edge.source.text, entity_id=edge.source, exists=True),
                generated=True,
            ))

    # -- queries ------------------------------------------------------------

    def __iter__(self) -> Iterator[Edge]:
        return iter(self._edges)

    @property
    def edges(self) -> list[Edge]:
        return list(self._edges)

    @staticmethod
    def _key(ref: Entity | EntityId) -> EntityId:
        """Accept an entity or its id.

        Callers hold whichever is to hand — templates have entities, the CLI
        has ids — and requiring the right one turns a query that finds nothing
        into a silent wrong answer rather than an error.
        """
        return ref.id if isinstance(ref, Entity) else ref

    def outgoing(self, ref: Entity | EntityId, kind: str | None = None) -> list[Edge]:
        edges = self._out.get(self._key(ref), [])
        return [e for e in edges if kind is None or e.kind == kind]

    def incoming(self, ref: Entity | EntityId, kind: str | None = None) -> list[Edge]:
        """Edges pointing at this entity, in the kind they were *authored* as."""
        edges = self._in.get(self._key(ref), [])
        return [e for e in edges if (kind is None or e.kind == kind) and not e.generated]

    def sources(self, ref: Entity | EntityId, kind: str) -> list[Entity]:
        """Entities that author `kind` pointing at this one, in id order."""
        found = [self.store.get(e.source) for e in self.incoming(ref, kind)]
        return sorted((e for e in found if e is not None),
                      key=lambda e: (e.id.prefix, e.id.number))

    def targets(self, ref: Entity | EntityId, kind: str) -> list[Target]:
        return [e.target for e in self.outgoing(ref, kind) if not e.generated]

    def children(self, ref: Entity | EntityId) -> list[Entity]:
        return self.sources(ref, self.config.data["parent_relation"])

    def dangling(self) -> list[Edge]:
        return [e for e in self._edges if not e.generated and not e.target.resolved]

    def redirected(self) -> list[Edge]:
        """Authored edges pointing at a tombstoned id — a warning with a rewrite."""
        return [e for e in self._edges
                if not e.generated and e.target.redirected_from is not None]

    # -- structural findings ------------------------------------------------

    def cardinality_findings(self) -> list[Finding]:
        findings = []
        for entity in self.store:
            for kind, targets in entity.relations.items():
                relation = self.config.relations.get(kind)
                if relation is None or not relation.is_single or len(targets) <= 1:
                    continue
                findings.append(Finding(
                    kind="cardinality",
                    ref=entity.id.text,
                    message=(f"{kind} is single-valued but has {len(targets)} targets: "
                             f"{', '.join(targets)}."),
                    fix=f"szsdlc unlink {entity.id.text} {kind} {targets[-1]}",
                ))
        return findings

    def type_findings(self) -> list[Finding]:
        """Targets whose type the relation does not allow — including a `parent`
        pointing at something that does not declare `can_parent`."""
        findings = []
        for edge in self._edges:
            if edge.generated or not edge.target.resolved:
                continue
            relation = self.config.relations.get(edge.kind)
            if relation is None or not relation.target_types:
                continue
            target = self.store.require(edge.target.entity_id)  # type: ignore[arg-type]
            if target.type.name in relation.target_types:
                continue
            allowed = ", ".join(sorted(relation.target_types))
            findings.append(Finding(
                kind="relation-type",
                ref=edge.source.text,
                message=(f"{edge.kind} points at {target.id.text} ({target.type.name}); "
                         f"allowed: {allowed}."),
                fix=f"szsdlc unlink {edge.source.text} {edge.kind} {target.id.text}",
            ))
        return findings

    def missing_required_findings(self) -> list[Finding]:
        findings = []
        for relation in self.config.relations.values():
            for type_name in relation.required_on:
                for entity in self.store.of_type(type_name):
                    if entity.is_terminal or entity.targets(relation.name):
                        continue
                    findings.append(Finding(
                        kind="missing-relation",
                        ref=entity.id.text,
                        message=f"a {type_name} must declare {relation.name}.",
                        fix=f"szsdlc link {entity.id.text} {relation.name} <ref>",
                    ))
        return findings

    def cycles(self, kind: str | None = None) -> list[tuple[str, list[EntityId]]]:
        """Cycles per relation kind, for every relation declared `acyclic`.

        The relation name travels with the path so the refusal can name a
        runnable `unlink` rather than a placeholder.
        """
        relations: Iterable[Relation]
        if kind is None:
            relations = [r for r in self.config.relations.values() if r.acyclic]
        else:
            relation = self.config.relations.get(kind)
            relations = [relation] if relation else []

        found: list[tuple[str, list[EntityId]]] = []
        for relation in relations:
            adjacency: dict[EntityId, list[EntityId]] = {}
            for edge in self._edges:
                if edge.generated or edge.kind != relation.name or not edge.target.resolved:
                    continue
                adjacency.setdefault(edge.source, []).append(
                    edge.target.entity_id  # type: ignore[arg-type]
                )
            found.extend((relation.name, cycle) for cycle in _find_cycles(adjacency))
        return found

    def structural_findings(self) -> list[Finding]:
        findings: list[Finding] = []
        for edge in self.dangling():
            findings.append(Finding(
                kind="dangling",
                ref=edge.source.text,
                message=f"{edge.kind} points at {edge.target.text}, which does not exist.",
                fix=f"szsdlc unlink {edge.source.text} {edge.kind} {edge.target.text}",
            ))
        findings.extend(self.cardinality_findings())
        findings.extend(self.type_findings())
        findings.extend(self.missing_required_findings())
        for relation_name, cycle in self.cycles():
            path = " -> ".join(i.text for i in cycle)
            findings.append(Finding(
                kind="cycle",
                ref=cycle[0].text,
                message=f"{relation_name} cycle: {path} -> {cycle[0].text}.",
                fix=f"szsdlc unlink {cycle[-1].text} {relation_name} {cycle[0].text}",
            ))
        return findings

    # -- derived attributes -------------------------------------------------

    def derived(self, entity: Entity) -> dict[str, Any]:
        """This type's declared derived attributes, computed now, stored never."""
        return {name: self.derive(entity, name) for name in entity.type.derived}

    def derive(self, entity: Entity, name: str) -> Any:
        declaration = entity.type.derived.get(name)
        if declaration is None:
            raise InternalError(f"{entity.type.name} declares no derived {name!r}")

        sources = self.sources(entity.id, declaration.relation)

        if declaration.kind == "has_incoming":
            return bool(sources)
        if declaration.kind == "incoming_refs":
            return [s.id.text for s in sources]
        if declaration.kind == "all_incoming_terminal":
            # False with no sources: an approved requirement nobody implements
            # is emphatically not delivered.
            return bool(sources) and all(s.is_terminal for s in sources)
        if declaration.kind == "aggregate_progress":
            done = total = 0
            for child in sources:
                progress = child.progress
                if progress is not None:
                    done += progress.done
                    total += progress.total
            return Aggregate(
                children=len(sources),
                terminal=sum(1 for s in sources if s.is_terminal),
                tasks=Progress(done, total),
            )
        raise InternalError(f"unknown derived kind {declaration.kind!r}")

    # -- traversal ----------------------------------------------------------

    def trace(self, entity_id: EntityId, depth: int = 2) -> list[Edge]:
        """Every authored edge within `depth` hops, walked in both directions.

        Generated inverses are skipped: including them would report every
        relationship twice, once per side, for no added information. Direction
        is recoverable from an edge's own source and target.
        """
        seen_edges: list[Edge] = []
        seen_keys: set[tuple[str, str, str]] = set()
        frontier = {entity_id}
        visited: set[EntityId] = set()

        for _ in range(max(0, depth)):
            following: set[EntityId] = set()
            for current in sorted(frontier, key=lambda i: (i.prefix, i.number)):
                if current in visited:
                    continue
                visited.add(current)
                touching = self._out.get(current, []) + self._in.get(current, [])
                for edge in touching:
                    if edge.generated:
                        continue
                    key = (edge.source.text, edge.kind, edge.target.text)
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                    seen_edges.append(edge)
                    for other in (edge.source, edge.target.entity_id):
                        if other is not None and other not in visited:
                            following.add(other)
            frontier = following
        return seen_edges


def _find_cycles(adjacency: dict[EntityId, list[EntityId]]) -> list[list[EntityId]]:
    """Every elementary cycle reachable in one DFS pass, reported once each."""
    cycles: list[list[EntityId]] = []
    reported: set[frozenset[EntityId]] = set()
    colour: dict[EntityId, int] = {}
    stack: list[EntityId] = []

    def visit(node: EntityId) -> None:
        colour[node] = 1
        stack.append(node)
        for neighbour in adjacency.get(node, []):
            state = colour.get(neighbour, 0)
            if state == 0:
                visit(neighbour)
            elif state == 1:
                cycle = stack[stack.index(neighbour):]
                signature = frozenset(cycle)
                if signature not in reported:
                    reported.add(signature)
                    cycles.append(list(cycle))
        stack.pop()
        colour[node] = 2

    for node in sorted(adjacency, key=lambda i: (i.prefix, i.number)):
        if colour.get(node, 0) == 0:
            visit(node)
    return cycles


def build(config: Config, store: EntityStore | None = None,
          ids: IdSpace | None = None) -> Graph:
    from .model import load_all

    ids = ids or IdSpace(config)
    return Graph(config, store or load_all(config, ids), ids)
