"""Config, ids and entities, loaded once per invocation."""

from __future__ import annotations

from .. import config as config_module
from ..errors import BadInput
from ..ids import IdSpace
from ..model import Entity, EntityStore, load_all


class Session:
    """Config, ids and entities, loaded once per invocation."""

    def __init__(self, start: str | None = None):
        self.config = config_module.load(start)
        self.ids = IdSpace(self.config)
        self._store: EntityStore | None = None

    @property
    def store(self) -> EntityStore:
        if self._store is None:
            self._store = load_all(self.config, self.ids)
        return self._store

    def entity(self, ref: str) -> Entity:
        entity_id = self.ids.resolve(ref)
        entity = self.store.get(entity_id)
        if entity is None:
            raise BadInput(
                f"{entity_id.text} exists on disk but could not be read.",
                fix="szsdlc validate",
            )
        return entity

    def intake_type(self, name: str | None = None):
        if name:
            entity_type = self.config.type_for(name)
            if not entity_type.intake:
                intake = ", ".join(t.name for t in self.config.types_with("intake"))
                raise BadInput(
                    f"{name}: not an intake type.",
                    fix=f"szsdlc capture --type {intake.split(', ')[0]}" if intake
                        else "set intake: true on a type in .szsdlc/config.yml",
                )
            return entity_type

        candidates = self.config.types_with("intake")
        if len(candidates) == 1:
            return candidates[0]
        if not candidates:
            raise BadInput(
                "this project declares no intake type, so there is nothing to capture into.",
                fix="set intake: true on a type in .szsdlc/config.yml",
            )
        names = ", ".join(t.name for t in candidates)
        raise BadInput(
            f"--type is required: {len(candidates)} intake types are declared.",
            fix=f"szsdlc capture --type {candidates[0].name}",
            see=f"intake types: {names}",
        )
