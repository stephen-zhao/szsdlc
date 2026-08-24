"""Identifiers: opaque, one form everywhere, one counter per type.

An id is a type prefix and a sequential number, and nothing else. It is
deliberately undecodable: anything embedded that can later change — a status, a
component, an owner, a release — invalidates every published reference to it
the moment it changes. The type prefix is the accepted exception, because a
type carries its own schema and workflow, and typeless intake keeps
reclassification off the common path.

Two consequences shape this module:

**No counter file.** The next number for a type is the maximum already on disk
plus one, found by scanning that type's directory. A stored counter is a second
home for a fact that the filenames already state, and two worktrees can
disagree with a stored value in a way they cannot disagree with a directory
listing.

**Nothing is renumbered, ever.** A converted entity leaves a tombstone so its
old id keeps resolving *and* stays allocated. Allocation therefore considers
tombstones as well as live entities: an id whose entity has moved away must
never be handed out again.

Lookups are tolerant — any padding, any case, so ``szsdlc show wi-42`` resolves
``WI-0042`` — while everything written to disk is canonical.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Iterable

import yaml

from .errors import BadInput, InternalError
from .text import nearest, slugify

if TYPE_CHECKING:  # pragma: no cover
    from .config import Config, EntityType

TOMBSTONE_FILENAME = "tombstones.yml"

_TOKEN = re.compile(r"\{(prefix|number|key)\}")
_GROUPS = {
    "prefix": r"(?P<prefix>[A-Za-z][A-Za-z0-9]*)",
    "number": r"(?P<number>\d+)",
    "key": r"(?P<key>[A-Za-z][A-Za-z0-9]*)",
}

#: An id two edits away is a plausible typo; further away it is a different id,
#: and a wrong suggestion costs more than none.
SUGGESTION_DISTANCE = 2


@dataclass(frozen=True, order=True)
class EntityId:
    """A parsed identifier. `text` is the canonical rendering, always."""

    prefix: str
    number: int
    key: str | None = None
    text: str = ""

    def __str__(self) -> str:
        return self.text

    def __post_init__(self) -> None:
        if not self.text:
            raise InternalError("EntityId built without its canonical text")


def _compile(pattern: str, *, leading: bool = False) -> re.Pattern[str]:
    """Turn an id pattern into a matcher.

    `leading` matches an id at the head of a longer name — ``WI-0042-add-tls``
    — which is how an entity's file or directory is recognised.
    """
    parts: list[str] = []
    position = 0
    for match in _TOKEN.finditer(pattern):
        parts.append(re.escape(pattern[position:match.start()]))
        parts.append(_GROUPS[match.group(1)])
        position = match.end()
    parts.append(re.escape(pattern[position:]))
    body = "".join(parts)
    tail = r"(?=-|$)" if leading else r"$"
    return re.compile(rf"^{body}{tail}", re.IGNORECASE)


class Tombstones:
    """The record of ids that moved, so old references keep resolving.

    One file, written only by `convert`, one entry per line — the same
    properties that keep the roadmap merge-friendly. Reading it is also part of
    allocation, so a converted id can never be handed out a second time.
    """

    def __init__(self, mapping: dict[str, str] | None = None, path: Path | None = None):
        self.mapping = dict(mapping or {})
        self.path = path

    @classmethod
    def load(cls, root: Path) -> Tombstones:
        path = root / ".szsdlc" / TOMBSTONE_FILENAME
        if not path.is_file():
            return cls({}, path)
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            raise BadInput(
                f"{path}: must be a mapping of old id to new id.",
                fix=f"correct or delete {path}",
            )
        return cls({str(k): str(v) for k, v in loaded.items()}, path)

    def __contains__(self, id_text: str) -> bool:
        return id_text in self.mapping

    def __len__(self) -> int:
        return len(self.mapping)

    def chain(self, id_text: str) -> list[str]:
        """Every id from `id_text` to its final successor.

        Total by construction: a hand-edited cycle stops the walk rather than
        hanging. Reporting the cycle is `validate`'s job, not this one's.
        """
        seen = [id_text]
        current = id_text
        while current in self.mapping:
            current = self.mapping[current]
            if current in seen:
                break
            seen.append(current)
        return seen

    def resolve(self, id_text: str) -> str:
        return self.chain(id_text)[-1]

    def record(self, old: str, new: str) -> None:
        self.mapping[old] = new

    def save(self) -> None:
        if self.path is None:
            raise InternalError("Tombstones.save() without a path")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        body = "".join(f"{old}: {new}\n" for old, new in sorted(self.mapping.items()))
        header = (
            "# Ids that moved. Written only by `szsdlc convert`.\n"
            "# Old references keep resolving, and a retired id is never reissued.\n"
        )
        self.path.write_text(header + body, encoding="utf-8")


class IdSpace:
    """Parsing, formatting and allocation for one project's identifiers."""

    def __init__(self, config: Config, tombstones: Tombstones | None = None):
        self.config = config
        self.pattern = config.id_pattern
        self.padding = config.id_padding
        self.key = config.id_key
        self._full = _compile(self.pattern)
        self._leading = _compile(self.pattern, leading=True)
        self._tombstones = tombstones

    # -- tombstones ---------------------------------------------------------

    @property
    def tombstones(self) -> Tombstones:
        if self._tombstones is None:
            self._tombstones = Tombstones.load(self.config.root)
        return self._tombstones

    # -- formatting ---------------------------------------------------------

    def format(self, prefix: str, number: int) -> str:
        return self.pattern.format(
            prefix=prefix.upper(),
            number=str(number).zfill(self.padding),
            key=(self.key or ""),
        )

    def make(self, prefix: str, number: int) -> EntityId:
        return EntityId(
            prefix=prefix.upper(),
            number=number,
            key=self.key,
            text=self.format(prefix, number),
        )

    def basename(self, entity_id: EntityId, title: str | None = None) -> str:
        """The on-disk name: the id, then a cosmetic slug that is never parsed."""
        slug = slugify(title or "")
        return f"{entity_id.text}-{slug}" if slug else entity_id.text

    # -- parsing ------------------------------------------------------------

    def parse(self, ref: str) -> EntityId:
        """Parse a reference. Tolerant of padding and case; strict about type.

        Does not touch the filesystem — use :meth:`resolve` when the entity is
        supposed to exist.
        """
        text = (ref or "").strip()
        match = self._full.match(text)
        if not match:
            raise BadInput(
                f"{ref!r}: not an id; expected the form "
                f"{self.format(self.config.prefixes[0], 42)}.",
                fix="szsdlc list --limit 20",
            )
        return self._from_match(match, ref)

    def parse_leading(self, name: str) -> EntityId | None:
        """The id at the head of a file, directory or section name, or None."""
        match = self._leading.match(name.split()[0] if name.split() else name)
        if not match:
            return None
        try:
            return self._from_match(match, name)
        except BadInput:
            return None

    def _from_match(self, match: re.Match[str], ref: str) -> EntityId:
        groups = match.groupdict()

        found_key = groups.get("key")
        if self.key and found_key and found_key.upper() != self.key.upper():
            raise BadInput(
                f"{ref!r}: project key {found_key.upper()!r} is not this project's "
                f"key {self.key!r}.",
                fix=f"use {self.format(self.config.prefixes[0], 42)}",
            )

        prefix = groups["prefix"].upper()
        if self.config.type_for_prefix(prefix) is None:
            raise BadInput(
                f"{ref!r}: unknown id prefix {prefix!r}.",
                fix="szsdlc list --limit 20",
                see=f"prefixes: {', '.join(self.config.prefixes)}",
            )

        return self.make(prefix, int(groups["number"]))

    def type_of(self, entity_id: EntityId) -> EntityType:
        entity_type = self.config.type_for_prefix(entity_id.prefix)
        if entity_type is None:  # pragma: no cover - parse already rejected it
            raise InternalError(f"no type for prefix {entity_id.prefix!r}")
        return entity_type

    # -- what exists on disk ------------------------------------------------

    def scan(self, entity_type: EntityType | str) -> list[tuple[EntityId, Path]]:
        """Every on-disk name of one type that parses as an id, in path order.

        Returns a list rather than a mapping so a duplicated id survives to be
        reported with *both* paths. Collapsing it here would hide the one
        failure mode directory-scan allocation can produce — and a `dynamic`
        type, whose entries may be in any of three layouts, is where that
        failure is most likely: a half-finished relayout leaves the same id in
        two of them.
        """
        et = (entity_type if not isinstance(entity_type, str)
              else self.config.type_for(entity_type))
        layouts = et.layouts

        found: list[tuple[EntityId, Path]] = []
        if "section" in layouts:
            found.extend(self.scan_sections(et))

        directory = self.config.dir_for(et)
        if not directory.is_dir():
            return found

        shared = self.config.section_path(et) if et.holds_sections else None
        for entry in sorted(directory.iterdir()):
            if entry.is_dir():
                if "directory" not in layouts:
                    continue
                name = entry.name
            else:
                if "file" not in layouts or entry.suffix != ".md" or entry == shared:
                    continue
                name = entry.stem

            entity_id = self.parse_leading(name)
            if entity_id is None or entity_id.prefix != et.prefix:
                continue
            found.append((entity_id, entry))
        return found

    def scan_sections(self, entity_type: EntityType) -> list[tuple[EntityId, Path]]:
        """Every entry in a `section` type's shared file, in file order.

        The path reported is the shared file, the same for every entry in it.
        That is the honest answer — it is where the entry is — and it is what
        makes a duplicated id inside one file report as a duplicate rather
        than as two entities.
        """
        from . import sections

        path = self.config.section_path(entity_type)
        _, found_sections = sections.split(
            sections.read(path),
            lambda name: self.section_id(entity_type, name) is not None,
        )
        found: list[tuple[EntityId, Path]] = []
        for section in found_sections:
            entity_id = self.section_id(entity_type, section.name)
            if entity_id is not None:
                found.append((entity_id, path))
        return found

    def section_id(self, entity_type: EntityType, heading: str) -> EntityId | None:
        """The id a heading names, or None when it names no entry of this type.

        A heading that is not an id of this type is prose — a title, a note to
        the reader, a `## Notes` written inside a body — and must not split the
        entry it sits in.
        """
        entity_id = self.parse_leading(heading)
        if entity_id is None or entity_id.prefix != entity_type.prefix:
            return None
        return entity_id

    def entries(self, entity_type: EntityType | str) -> dict[EntityId, Path]:
        """Every entity of one type, found by scanning its directory.

        The directory listing *is* the index. Nothing is cached and no counter
        is stored, so there is no second copy of this fact to go stale.
        """
        found: dict[EntityId, Path] = {}
        for entity_id, path in self.scan(entity_type):
            found.setdefault(entity_id, path)
        return found

    def all_entries(self) -> dict[EntityId, Path]:
        found: dict[EntityId, Path] = {}
        for entity_type in self.config.entity_types.values():
            found.update(self.entries(entity_type))
        return found

    def numbers(self, entity_type: EntityType | str) -> set[int]:
        """Numbers already spoken for: live entities plus retired ids."""
        et = (entity_type if not isinstance(entity_type, str)
              else self.config.type_for(entity_type))
        used = {entity_id.number for entity_id in self.entries(et)}
        for old in self.tombstones.mapping:
            retired = self.parse_leading(old)
            if retired is not None and retired.prefix == et.prefix:
                used.add(retired.number)
        return used

    # -- allocation ---------------------------------------------------------

    def next_id(self, entity_type: EntityType | str) -> EntityId:
        """Max + 1. Gaps are left alone: a reissued id is a broken reference."""
        et = (entity_type if not isinstance(entity_type, str)
              else self.config.type_for(entity_type))
        used = self.numbers(et)
        return self.make(et.prefix, (max(used) + 1) if used else 1)

    # -- resolution ---------------------------------------------------------

    def resolve(self, ref: str) -> EntityId:
        """Parse `ref`, follow any tombstone, and require that it exists.

        The unknown-reference refusal names the nearest known id, which turns
        the most likely agent typo into one corrected call rather than a search.
        """
        entity_id = self.parse(ref)

        if entity_id.text in self.tombstones:
            successor = self.tombstones.resolve(entity_id.text)
            if successor != entity_id.text:
                return self.parse(successor)

        known = self.all_entries()
        if entity_id in known:
            return entity_id

        suggestion = nearest(
            entity_id.text,
            (existing.text for existing in known),
            max_distance=SUGGESTION_DISTANCE,
        )
        if suggestion:
            raise BadInput(
                f"no such entity {entity_id.text!r}; did you mean {suggestion}?",
                fix=f"szsdlc show {suggestion}",
            )
        raise BadInput(
            f"no such entity {entity_id.text!r}.",
            fix=f"szsdlc list --type {self.type_of(entity_id).name}",
        )

    def path_of(self, entity_id: EntityId) -> Path | None:
        return self.entries(self.type_of(entity_id)).get(entity_id)


def id_space(config: Config, tombstones: Tombstones | None = None) -> IdSpace:
    return IdSpace(config, tombstones)


def sorted_ids(ids: Iterable[EntityId]) -> list[EntityId]:
    """Stable ordering everywhere: by prefix, then number. Never by text, which
    would sort WI-10 before WI-9 whenever padding is too narrow."""
    return sorted(ids, key=lambda i: (i.prefix, i.number))
