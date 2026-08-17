"""Roadmaps: horizon buckets of ids, and the only home for placement.

Placement is a fact about a *set*, not about a member. There is no `priority`
field on any entity, because rescheduling would then edit N files to state one
change; here it edits one. The record is written only by `schedule` and
`unschedule`, one entity per line with stable ordering, so two branches
reordering different things resolve as line-level adds rather than a conflict
over a rewritten block.

Which statuses *must* be scheduled is per-roadmap configuration, so a global
roadmap can demand that everything ready appears exactly once while a per-epic
roadmap demands nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .config import Config, EntityType, RoadmapSpec, Workflow
from .errors import BadInput, Refused
from .graph import Finding
from .ids import EntityId, IdSpace
from .model import Entity, EntityStore

_HEADER = (
    "# Placement lives here and nowhere else — there is no priority field on an\n"
    "# entity. Written by `szsdlc schedule` / `szsdlc unschedule`.\n"
)


@dataclass(frozen=True)
class Placement:
    """Where an entity landed, and what it landed between.

    Reported back by `schedule` so no confirming `show` is ever needed (C1).
    """

    entity: str
    roadmap: str
    horizon: str
    index: int
    after: str | None = None
    before: str | None = None

    def __str__(self) -> str:
        neighbours = " ".join(
            part for part in (
                f"after {self.after}" if self.after else "",
                f"before {self.before}" if self.before else "",
            ) if part
        )
        where = f"{self.horizon}[{self.index}]"
        return f"{self.entity}: {where}" + (f" — {neighbours}" if neighbours else "")


def has_reached(workflow: Workflow, current: str | None, target: str) -> bool | None:
    """Has an entity at `current` reached `target`?

    Answered by reachability rather than by the order states happen to be
    declared in: an entity has reached `ready` when it is *at* ready, or when
    ready can no longer be got to by moving forward. That holds for branching
    workflows, which a declaration-order comparison quietly gets wrong.

    Returns None when `current` is outside the workflow entirely — a hand-edited
    status the caller must refuse rather than guess about.
    """
    if current is None or current not in workflow.states:
        return None
    if current == target:
        return True

    seen = {current}
    frontier = [current]
    while frontier:
        state = workflow.states[frontier.pop()]
        for nxt in state.to:
            if nxt == target:
                return False
            if nxt not in seen:
                seen.add(nxt)
                frontier.append(nxt)
    return True


# ---------------------------------------------------------------------------
# The record
# ---------------------------------------------------------------------------


@dataclass
class Roadmap:
    """One roadmap record. Ids are held as written and resolved by the caller."""

    spec: RoadmapSpec
    path: Path
    buckets: dict[str, list[str]] = field(default_factory=dict)
    #: Horizons in the file that config no longer declares. Preserved rather
    #: than dropped: rewriting the file must never silently discard work.
    unknown: dict[str, list[str]] = field(default_factory=dict)
    error: str | None = None

    @property
    def name(self) -> str:
        return self.spec.name

    @classmethod
    def load(cls, config: Config, name: str) -> Roadmap:
        spec = config.roadmaps[name]
        path = config.roadmap_path(name)
        roadmap = cls(spec=spec, path=path,
                      buckets={h: [] for h in spec.horizons})

        if not path.is_file():
            return roadmap

        try:
            loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            problem = getattr(exc, "problem", None) or "invalid YAML"
            roadmap.error = f"{problem}"
            return roadmap

        if loaded is None:
            return roadmap
        if not isinstance(loaded, dict):
            roadmap.error = f"must be a mapping of horizon to ids, not {type(loaded).__name__}"
            return roadmap

        for horizon, entries in loaded.items():
            if entries is None:
                entries = []
            if not isinstance(entries, list):
                roadmap.error = f"horizon {horizon!r} must hold a list of ids"
                return roadmap
            values = [str(e) for e in entries]
            if str(horizon) in roadmap.buckets:
                roadmap.buckets[str(horizon)] = values
            else:
                roadmap.unknown[str(horizon)] = values
        return roadmap

    # -- lookups ------------------------------------------------------------

    def __contains__(self, id_text: str) -> bool:
        return self.position(id_text) is not None

    def entries(self, horizon: str) -> list[str]:
        return list(self.buckets.get(horizon, []))

    def all_ids(self) -> list[str]:
        return [i for horizon in self.spec.horizons for i in self.buckets[horizon]]

    def position(self, id_text: str) -> tuple[str, int] | None:
        for horizon in self.spec.horizons:
            for index, entry in enumerate(self.buckets[horizon]):
                if entry == id_text:
                    return horizon, index
        return None

    def horizon_of(self, id_text: str) -> str | None:
        found = self.position(id_text)
        return found[0] if found else None

    def repeated(self) -> list[str]:
        counts: dict[str, int] = {}
        for horizon in self.spec.horizons:
            for entry in self.buckets[horizon]:
                counts[entry] = counts.get(entry, 0) + 1
        for entries in self.unknown.values():
            for entry in entries:
                counts[entry] = counts.get(entry, 0) + 1
        return sorted(entry for entry, count in counts.items() if count > 1)

    def _placement(self, id_text: str) -> Placement:
        horizon, index = self.position(id_text)  # type: ignore[misc]
        entries = self.buckets[horizon]
        return Placement(
            entity=id_text,
            roadmap=self.name,
            horizon=horizon,
            index=index,
            after=entries[index - 1] if index > 0 else None,
            before=entries[index + 1] if index + 1 < len(entries) else None,
        )

    # -- mutation -----------------------------------------------------------

    def _writable(self) -> None:
        if self.error:
            raise BadInput(
                f"{self.path}: {self.error}.",
                fix=f"correct {self.path}, then rerun",
            )

    def place(self, id_text: str, horizon: str, *, after: str | None = None,
              before: str | None = None, top: bool = False) -> Placement:
        """Put `id_text` at one position, moving it if already placed.

        One verb covers "schedule" and "move": an entity appearing twice is a
        finding, so placement is idempotent by construction rather than by the
        caller remembering to unschedule first.
        """
        self._writable()
        if horizon not in self.buckets:
            raise BadInput(
                f"{horizon!r} is not a horizon of roadmap {self.name!r}.",
                fix=f"szsdlc schedule {id_text} --horizon {self.spec.horizons[-1]}",
                see=f"horizons: {', '.join(self.spec.horizons)}",
            )
        if sum(bool(x) for x in (after, before, top)) > 1:
            raise BadInput(
                "--after, --before and --top are mutually exclusive.",
                fix=f"szsdlc schedule {id_text} --horizon {horizon} --top",
            )

        self.remove(id_text)
        entries = self.buckets[horizon]

        if top:
            index = 0
        elif after is not None:
            index = self._anchor(after, horizon, "--after") + 1
        elif before is not None:
            index = self._anchor(before, horizon, "--before")
        else:
            index = len(entries)

        entries.insert(index, id_text)
        return self._placement(id_text)

    def _anchor(self, anchor: str, horizon: str, flag: str) -> int:
        entries = self.buckets[horizon]
        if anchor in entries:
            return entries.index(anchor)
        elsewhere = self.horizon_of(anchor)
        if elsewhere:
            raise BadInput(
                f"{anchor} is on horizon {elsewhere!r}, not {horizon!r}.",
                fix=f"use --horizon {elsewhere}",
            )
        raise BadInput(
            f"{anchor} is not on roadmap {self.name!r}, so {flag} has nothing to anchor to.",
            fix=f"szsdlc schedule {anchor} --horizon {horizon}",
        )

    def remove(self, id_text: str) -> bool:
        self._writable()
        removed = False
        for entries in list(self.buckets.values()) + list(self.unknown.values()):
            while id_text in entries:
                entries.remove(id_text)
                removed = True
        return removed

    # -- rendering ----------------------------------------------------------

    def render(self) -> str:
        """Deterministic: horizons in configured order, one entity per line.

        Empty horizons are still printed, so the shape of the roadmap is
        visible without consulting the config.
        """
        lines = [_HEADER]
        for horizon in self.spec.horizons:
            lines.append(_render_bucket(horizon, self.buckets[horizon]))
        for horizon, entries in self.unknown.items():
            lines.append(f"\n# Not a configured horizon of {self.name!r}.\n")
            lines.append(_render_bucket(horizon, entries))
        return "".join(lines)

    def save(self) -> None:
        self._writable()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_bytes(self.render().encode("utf-8"))


def _render_bucket(horizon: str, entries: list[str]) -> str:
    if not entries:
        return f"{horizon}: []\n"
    body = "".join(f"  - {entry}\n" for entry in entries)
    return f"{horizon}:\n{body}"


def load_all(config: Config) -> dict[str, Roadmap]:
    return {name: Roadmap.load(config, name) for name in config.roadmaps}


# ---------------------------------------------------------------------------
# Scheduling, with the guards
# ---------------------------------------------------------------------------


class Scheduler:
    """`schedule` and `unschedule`: one verb, one ref, every refusal explained."""

    def __init__(self, config: Config, store: EntityStore,
                 roadmap: str | None = None, ids: IdSpace | None = None):
        self.config = config
        self.store = store
        self.ids = ids or IdSpace(config)
        spec = config.roadmaps[roadmap] if roadmap else config.only_roadmap()
        self.roadmap = Roadmap.load(config, spec.name)

    def _entity(self, ref: str | EntityId | Entity) -> Entity:
        if isinstance(ref, Entity):
            return ref
        entity_id = ref if isinstance(ref, EntityId) else self.ids.resolve(ref)
        entity = self.store.get(entity_id)
        if entity is None:
            raise BadInput(
                f"no such entity {entity_id.text!r}.",
                fix=f"szsdlc list --type {self.ids.type_of(entity_id).name}",
            )
        return entity

    def _check(self, entity: Entity) -> None:
        entity_type: EntityType = entity.type

        if not entity_type.schedulable:
            raise Refused(
                f"{entity.id.text}: {entity_type.name} is not schedulable, so it has "
                f"no place on a roadmap.",
                fix=f"szsdlc list --type {entity_type.name}",
            )

        if entity.is_terminal:
            raise Refused(
                f"{entity.id.text}: status {entity.status} is terminal; "
                f"finished work leaves the roadmap.",
                fix=f"szsdlc unschedule {entity.id.text}",
            )

        threshold = self.roadmap.spec.requires_scheduling.get(entity_type.name)
        if threshold is None:
            return

        from .workflow import step_toward, suggested_next

        reached = has_reached(entity_type.workflow, entity.status, threshold)
        if reached is None:
            states = ", ".join(entity_type.workflow.states)
            raise Refused(
                f"{entity.id.text}: status {entity.status!r} is not in the "
                f"{entity_type.name} workflow.",
                fix=f"szsdlc set {entity.id.text} status={suggested_next(entity)}",
                see=f"statuses: {states}",
            )
        if not reached:
            # The *next hop*, not the destination: `idea → ready` is two
            # transitions, so naming the destination produces another refusal
            # rather than progress.
            hop = step_toward(entity, threshold) or threshold
            raise Refused(
                f"{entity.id.text}: status {entity.status} has not reached "
                f"{threshold}, so it is not ready to schedule.",
                fix=f"szsdlc set {entity.id.text} status={hop}",
            )

    def schedule(self, ref: str | EntityId | Entity, horizon: str, *,
                 after: str | None = None, before: str | None = None,
                 top: bool = False) -> Placement:
        entity = self._entity(ref)
        self._check(entity)

        anchor_after = self.ids.resolve(after).text if after else None
        anchor_before = self.ids.resolve(before).text if before else None

        placement = self.roadmap.place(entity.id.text, horizon, after=anchor_after,
                                       before=anchor_before, top=top)
        self.roadmap.save()
        return placement

    def unschedule(self, ref: str | EntityId | Entity) -> bool:
        entity = self._entity(ref)
        removed = self.roadmap.remove(entity.id.text)
        if removed:
            self.roadmap.save()
        return removed


# ---------------------------------------------------------------------------
# Findings — what a prose priority list could never enforce
# ---------------------------------------------------------------------------


def scheduling_findings(config: Config, store: EntityStore,
                        roadmaps: dict[str, Roadmap] | None = None) -> list[Finding]:
    roadmaps = roadmaps if roadmaps is not None else load_all(config)
    findings: list[Finding] = []

    for roadmap in roadmaps.values():
        if roadmap.error:
            findings.append(Finding(
                kind="roadmap-unparseable",
                ref=str(roadmap.path),
                message=roadmap.error,
                fix=f"correct {roadmap.path}",
            ))
            continue

        for horizon, entries in roadmap.unknown.items():
            findings.append(Finding(
                kind="roadmap-horizon",
                ref=roadmap.name,
                message=(f"{horizon!r} is not a configured horizon; "
                         f"{len(entries)} entries are stranded there."),
                fix=f"add {horizon} to roadmaps.{roadmap.name}.horizons in .szsdlc/config.yml",
            ))

        for repeated in roadmap.repeated():
            findings.append(Finding(
                kind="roadmap-duplicate",
                ref=repeated,
                message=f"appears more than once on roadmap {roadmap.name!r}.",
                fix=f"szsdlc schedule {repeated} --horizon "
                    f"{roadmap.horizon_of(repeated) or roadmap.spec.horizons[0]}",
            ))

        for id_text in roadmap.all_ids():
            entity = store.by_text(id_text)
            if entity is None:
                findings.append(Finding(
                    kind="roadmap-dangling",
                    ref=id_text,
                    message=f"is on roadmap {roadmap.name!r} but does not exist.",
                    fix=f"szsdlc unschedule {id_text}",
                ))
                continue
            if not entity.type.schedulable:
                findings.append(Finding(
                    kind="roadmap-type",
                    ref=id_text,
                    message=f"is a {entity.type.name}, which is not schedulable.",
                    fix=f"szsdlc unschedule {id_text}",
                ))
            elif entity.is_terminal:
                findings.append(Finding(
                    kind="roadmap-terminal",
                    ref=id_text,
                    message=f"is {entity.status} but still on roadmap {roadmap.name!r}.",
                    fix=f"szsdlc unschedule {id_text}",
                ))

        for type_name, threshold in roadmap.spec.requires_scheduling.items():
            entity_type = config.entity_types.get(type_name)
            if entity_type is None:
                continue
            for entity in store.of_type(type_name):
                if entity.is_terminal or entity.id.text in roadmap:
                    continue
                if has_reached(entity_type.workflow, entity.status, threshold):
                    findings.append(Finding(
                        kind="roadmap-missing",
                        ref=entity.id.text,
                        message=(f"is {entity.status} but appears on no roadmap; "
                                 f"{type_name} must be scheduled from {threshold}."),
                        fix=f"szsdlc schedule {entity.id.text} "
                            f"--horizon {roadmap.spec.horizons[-1]}",
                    ))

    return findings
