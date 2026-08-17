"""Entities: one interface over both storage layouts, and one that can hold
invalidity rather than choking on it.

A `file` entity is a single markdown file; a `directory` entity is an
`entity.md` plus artifacts. Nothing downstream branches on which — asking an
entity for an artifact it cannot have simply answers "no".

The harder property is the second one. `sync` must render a half-built project,
so loading is *permissive*: a file whose frontmatter will not parse becomes an
explicit :class:`UnparseableEntity` carrying its path and the parse error, and
a status outside the configured workflow loads exactly as written. Neither is
an exception, because one corrupt file must not stop the other 199 from
loading, and you cannot report a problem you refused to represent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from jsonschema import Draft202012Validator

from . import frontmatter
from .config import Config, EntityType
from .errors import InternalError
from .ids import EntityId, IdSpace
from .text import normalize_tags

_CHECKBOX = re.compile(r"^[ \t]*[-*+][ \t]+\[([ xX])\][ \t]")
_HEADING = re.compile(r"^#{1,6}[ \t]+")


# ---------------------------------------------------------------------------
# Progress
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Progress:
    """Checkbox counts from a `tracks_progress` type's artifact.

    Derived on every read. An entity never stores a percentage, so it can never
    hold one that has gone stale.
    """

    done: int
    total: int

    @property
    def fraction(self) -> float:
        return self.done / self.total if self.total else 0.0

    @property
    def percent(self) -> int:
        return round(self.fraction * 100)

    @property
    def complete(self) -> bool:
        """An artifact with no tasks in it is not complete — it is unwritten."""
        return self.total > 0 and self.done == self.total

    @property
    def remaining(self) -> int:
        return self.total - self.done

    def __str__(self) -> str:
        return f"{self.done}/{self.total}"


def parse_progress(text: str) -> Progress:
    done = total = 0
    for line in text.splitlines():
        match = _CHECKBOX.match(line)
        if match:
            total += 1
            if match.group(1) in "xX":
                done += 1
    return Progress(done=done, total=total)


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------


@dataclass
class UnparseableEntity:
    """A file the loader could not read, represented rather than raised.

    It appears in views with its path and error — dropping it would let `sync`
    conceal precisely what `validate` exists to surface.
    """

    path: Path
    error: str
    entity_id: EntityId | None = None
    entity_type: EntityType | None = None

    @property
    def ref(self) -> str:
        return self.entity_id.text if self.entity_id else self.path.name

    def __str__(self) -> str:
        return f"{self.ref} (unparseable: {self.error})"


class Entity:
    """One entity, loaded from either layout."""

    def __init__(self, entity_id: EntityId, entity_type: EntityType, config: Config,
                 path: Path, document: frontmatter.Document, home: Path | None = None):
        self.id = entity_id
        self.type = entity_type
        self.config = config
        #: The markdown file holding the frontmatter.
        self.path = path
        #: The entity's own directory, or None for a `file` layout entity.
        self.home = home
        self.doc = document

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Entity {self.id.text} {self.status!r}>"

    # -- the hand-authored state -------------------------------------------

    @property
    def data(self) -> dict[str, Any]:
        return self.doc.data

    @property
    def status(self) -> str | None:
        """Held exactly as written, even when outside the workflow."""
        value = self.data.get("status")
        return str(value) if value is not None else None

    @property
    def title(self) -> str:
        """The stored title, or the first meaningful body line.

        Derivation is what lets intake cost one command: capturing a thought
        must never require also naming it.
        """
        stored = self.data.get("title")
        if stored:
            return str(stored)
        return self.derived_title()

    def derived_title(self) -> str:
        for line in self.body.splitlines():
            text = _HEADING.sub("", line).strip().lstrip("-*+ ").strip()
            if text:
                return text
        return ""

    @property
    def has_stored_title(self) -> bool:
        return bool(self.data.get("title"))

    @property
    def body(self) -> str:
        return self.doc.body

    @property
    def raw_tags(self) -> list[str]:
        """Tags exactly as written, so `validate` can see an unnormalized one."""
        value = self.data.get("tags") or []
        if isinstance(value, str):
            return [value]
        return [str(v) for v in value]

    @property
    def tags(self) -> list[str]:
        return normalize_tags(self.raw_tags, normalize=self._normalize_tags)

    @property
    def _normalize_tags(self) -> bool:
        return bool((self.config.data.get("tags") or {}).get("normalize", True))

    @property
    def relations(self) -> dict[str, list[str]]:
        """Edge targets held **as written** and resolved lazily elsewhere.

        A dangling reference has to be representable, or it could never be
        reported. Cardinality is normalized to a list here purely so callers
        need not branch; what was authored is preserved on disk.
        """
        raw = self.data.get("relations") or {}
        if not isinstance(raw, dict):
            return {}
        out: dict[str, list[str]] = {}
        for kind, value in raw.items():
            if value is None:
                continue
            targets = [value] if isinstance(value, str) else list(value)
            out[str(kind)] = [str(t) for t in targets if t is not None]
        return out

    def targets(self, kind: str) -> list[str]:
        return self.relations.get(kind, [])

    def parent(self) -> str | None:
        targets = self.targets(self.config.data["parent_relation"])
        return targets[0] if targets else None

    def field(self, name: str) -> Any:
        return self.data.get(name)

    # -- derived-from-disk --------------------------------------------------

    @property
    def is_terminal(self) -> bool:
        return bool(self.status) and self.type.workflow.is_terminal(self.status or "")

    def artifact_path(self, name: str) -> Path | None:
        """Where an artifact would live. None when the layout has no room for one."""
        if self.home is None:
            return None
        return self.home / name

    def has_artifact(self, name: str) -> bool:
        """Present *and* non-empty. An empty design.md has not been written."""
        path = self.artifact_path(name)
        return bool(path and path.is_file() and path.stat().st_size > 0)

    def read_artifact(self, name: str) -> str | None:
        path = self.artifact_path(name)
        if path is None or not path.is_file():
            return None
        return path.read_bytes().decode("utf-8", errors="replace")

    def artifact_files(self) -> list[Path]:
        """Every file in the entity's directory except its own entity.md."""
        if self.home is None or not self.home.is_dir():
            return []
        return sorted(p for p in self.home.iterdir()
                      if p.is_file() and p.name != self.config.entity_filename)

    @property
    def progress(self) -> Progress | None:
        """None when the type does not track progress — not zero, which would
        render as "0% done" for something that has no tasks at all."""
        if not self.type.tracks_progress or not self.type.progress_artifact:
            return None
        text = self.read_artifact(self.type.progress_artifact)
        return parse_progress(text) if text is not None else Progress(0, 0)

    # -- mutation -----------------------------------------------------------

    def set_field(self, name: str, value: Any) -> None:
        self.doc.set(name, value)

    def unset_field(self, name: str) -> None:
        self.doc.unset(name)

    def set_status(self, status: str) -> None:
        """Writes the status. Gates and transition rules live in workflow.py —
        this is the mechanism, not the policy."""
        self.doc.set("status", status)

    def set_tags(self, tags: list[str]) -> None:
        normalized = normalize_tags(tags, normalize=self._normalize_tags)
        if normalized:
            self.doc.set("tags", normalized, flow=True)
        else:
            self.doc.unset("tags")

    def set_relation(self, kind: str, targets: list[str] | str | None) -> None:
        """Rewrite one relation. Cardinality follows the configured shape, so a
        single-valued relation is written as a scalar, as an author would."""
        relations = dict(self.data.get("relations") or {})
        relation = self.config.relations.get(kind)

        if targets is None or (isinstance(targets, list) and not targets):
            relations.pop(kind, None)
        elif isinstance(targets, str):
            relations[kind] = targets
        elif relation is not None and relation.is_single:
            relations[kind] = targets[0]
        else:
            relations[kind] = list(targets)

        if relations:
            self.doc.set("relations", relations, flow=False)
        else:
            self.doc.unset("relations")

    def render(self) -> str:
        return self.doc.render()

    def save(self) -> None:
        """Write bytes, not text: line endings are part of what stays untouched."""
        self.path.write_bytes(self.render().encode("utf-8"))


# ---------------------------------------------------------------------------
# Frontmatter schema, composed per type
# ---------------------------------------------------------------------------


_FIELD_SCHEMAS = {
    "string": {"type": "string"},
    "text": {"type": "string"},
    "date": {"type": "string"},
    "integer": {"type": "integer"},
    "number": {"type": "number"},
    "boolean": {"type": "boolean"},
    "array": {"type": "array"},
}


def frontmatter_schema(config: Config, entity_type: EntityType) -> dict[str, Any]:
    """Core fields plus this type's configured ones, composed on demand."""
    relation_props: dict[str, Any] = {}
    for relation in config.relations_from(entity_type.name):
        one = {"type": "string"}
        # A `many` relation may be authored as a bare scalar; accepting that is
        # tolerance on read, not a second storage form — writes are canonical.
        relation_props[relation.name] = (
            one if relation.is_single
            else {"anyOf": [one, {"type": "array", "items": one}]}
        )

    properties: dict[str, Any] = {
        "id": {"type": "string"},
        "title": {"type": "string", "minLength": 1},
        "tags": {"type": "array", "items": {"type": "string"}},
        # Deliberately not an enum: a hand-edited status outside the workflow
        # must load, and is reported by its own validate rule with its own fix.
        "status": {"type": "string", "minLength": 1},
        "relations": {
            "type": "object",
            "properties": relation_props,
            "additionalProperties": False,
        },
    }

    required = ["id", "status"] if entity_type.intake else ["id", "title", "status"]

    for name, spec in entity_type.fields.items():
        schema = dict(_FIELD_SCHEMAS[spec.type])
        if spec.enum is not None:
            schema["enum"] = list(spec.enum)
        properties[name] = schema
        if spec.required and name not in required:
            required.append(name)

    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def schema_findings(config: Config, entity: Entity) -> list[str]:
    """Frontmatter problems as messages. Returned, never raised: these describe
    the *project*, and `sync` has to render a project that has them."""
    schema = frontmatter_schema(config, entity.type)
    data = frontmatter.jsonify(entity.data)
    validator = Draft202012Validator(schema)
    findings = []
    for error in sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path)):
        path = ".".join(str(p) for p in error.absolute_path)
        findings.append(f"{path}: {error.message}" if path else error.message)
    return findings


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def load_entity(config: Config, entity_id: EntityId, entity_type: EntityType,
                entry: Path) -> Entity | UnparseableEntity:
    """Load one entity from either layout. Never raises for bad content."""
    if entity_type.is_directory_layout:
        home: Path | None = entry
        path = entry / config.entity_filename
    else:
        home = None
        path = entry

    if not path.is_file():
        return UnparseableEntity(
            path=path,
            error=f"{config.entity_filename} is missing",
            entity_id=entity_id,
            entity_type=entity_type,
        )

    try:
        text = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return UnparseableEntity(path=path, error=str(exc),
                                 entity_id=entity_id, entity_type=entity_type)

    document = frontmatter.parse(text)
    if not document.ok:
        return UnparseableEntity(path=path, error=document.error or "unparseable",
                                 entity_id=entity_id, entity_type=entity_type)

    return Entity(entity_id, entity_type, config, path, document, home)


@dataclass
class EntityStore:
    """Every entity in the project, plus everything that would not load."""

    config: Config
    entities: dict[EntityId, Entity] = field(default_factory=dict)
    unparseable: list[UnparseableEntity] = field(default_factory=list)
    #: (id, path) for a second file claiming an id already taken. Reported with
    #: both paths, since directory-scan allocation cannot prevent this.
    duplicates: list[tuple[EntityId, Path]] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.entities)

    def __iter__(self) -> Iterator[Entity]:
        return iter(self.entities.values())

    def __contains__(self, entity_id: EntityId) -> bool:
        return entity_id in self.entities

    def get(self, entity_id: EntityId) -> Entity | None:
        return self.entities.get(entity_id)

    def by_text(self, id_text: str) -> Entity | None:
        return next((e for e in self.entities.values() if e.id.text == id_text), None)

    def of_type(self, type_name: str) -> list[Entity]:
        return [e for e in self.entities.values() if e.type.name == type_name]

    def with_flag(self, flag: str) -> list[Entity]:
        """The only legitimate way to ask for "all work" or "all definitions"."""
        return [e for e in self.entities.values() if e.type.flag(flag)]

    def require(self, entity_id: EntityId) -> Entity:
        entity = self.entities.get(entity_id)
        if entity is None:  # pragma: no cover - callers resolve first
            raise InternalError(f"{entity_id.text} is not loaded")
        return entity


def load_all(config: Config, ids: IdSpace | None = None) -> EntityStore:
    """Load every entity of every type, in deterministic id order."""
    ids = ids or IdSpace(config)
    store = EntityStore(config)
    seen: set[EntityId] = set()

    for entity_type in config.entity_types.values():
        for entity_id, entry in ids.scan(entity_type):
            # Claimed on sight, whether or not it parsed: a second file for an
            # id is a duplicate even when the first one is broken.
            if entity_id in seen:
                store.duplicates.append((entity_id, entry))
                continue
            seen.add(entity_id)

            loaded = load_entity(config, entity_id, entity_type, entry)
            if isinstance(loaded, UnparseableEntity):
                store.unparseable.append(loaded)
            else:
                store.entities[entity_id] = loaded

    store.entities = {k: store.entities[k] for k in sorted(store.entities)}
    return store
