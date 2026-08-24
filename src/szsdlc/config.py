"""Load, merge and validate `.szsdlc/config.yml`.

Everything the framework knows about a project's *shape* — its entity types,
their workflows, the relations between them, the roadmap horizons — arrives
through here as data. No module downstream names a type or a status; they read
capability flags off the objects this module builds.

Three layers of checking, in order, because each can only run once the previous
has passed:

1. **Parse** — YAML, reported with the file and the line.
2. **Schema** — `schemas/config.schema.json`, which catches shape and unknown
   keys. Unknown keys are errors, since a silently ignored key is a
   configuration that does not do what it says.
3. **Semantics** — the cross-references a JSON Schema cannot express: a
   transition to a state that does not exist, a relation naming an unknown
   type, a roadmap demanding that a non-schedulable type be scheduled.

Every failure is a :class:`~szsdlc.errors.ConfigError` naming the offending key
path. Note that config fixes are hand edits rather than commands — config is
the one hand-authored surface in the framework — so the `Fix:` line names the
key and the legal values instead of a command to run.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

from .errors import ConfigError

CONFIG_DIRNAME = ".szsdlc"
CONFIG_FILENAME = "config.yml"

#: A key inside any mapping that switches deep-merge to wholesale replacement,
#: which is what "replace the default entity types entirely" needs.
REPLACE_KEY = "_replace"

#: Frontmatter keys the framework owns. A type may not redeclare these as
#: custom fields, and a derived attribute may not shadow one.
CORE_FIELDS = frozenset({"id", "title", "tags", "status", "relations"})

#: Where a `section`-layout type keeps its entries when it does not say. Inside
#: the type's own directory, so everything that watches or scans that directory
#: — the sync hook included — keeps working without being told about layouts.
DEFAULT_SECTION_FILE = "index.md"

#: What one entry may occupy. `dynamic` is not one of them: it is a type saying
#: its entries may take any of these, one at a time, and change which.
LAYOUTS = ("section", "file", "directory")

#: Where a `dynamic` type puts an entry it has just been handed. The cheapest
#: layout that fits, for the same reason `workflow.initial` is the cheapest
#: status: something that has just arrived has not earned anything more.
DEFAULT_INITIAL_LAYOUT = "section"

CAPABILITY_FLAGS = (
    "intake",
    "actionable",
    "tracks_progress",
    "can_parent",
    "schedulable",
    "persistent",
)


# ---------------------------------------------------------------------------
# Resolved config objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class State:
    """One status in a type's workflow, plus the gates guarding entry to it."""

    name: str
    to: tuple[str, ...] = ()
    terminal: bool = False
    #: Deliberately given up on rather than completed — the status `drop` moves
    #: to. Declared rather than inferred, so a project that renames its statuses
    #: still gets a working `drop`.
    abandoned: bool = False
    requires_artifact: tuple[str, ...] = ()
    requires_tasks_complete: bool = False
    description: str | None = None


@dataclass(frozen=True)
class Workflow:
    initial: str
    states: dict[str, State]

    def __contains__(self, status: str) -> bool:
        return status in self.states

    def is_terminal(self, status: str) -> bool:
        """Unknown statuses are non-terminal: a hand-edited file still loads."""
        state = self.states.get(status)
        return bool(state and state.terminal)

    def can_move(self, source: str, target: str) -> bool:
        state = self.states.get(source)
        return bool(state and target in state.to)

    @property
    def terminal_states(self) -> tuple[str, ...]:
        return tuple(n for n, s in self.states.items() if s.terminal)

    @property
    def abandoned_status(self) -> str | None:
        """Where `drop` puts things. Declared in config, never guessed."""
        return next((n for n, s in self.states.items() if s.abandoned), None)

    @property
    def completed_status(self) -> str | None:
        """The terminal status that means finished rather than given up on."""
        return next((n for n, s in self.states.items()
                     if s.terminal and not s.abandoned), None)


@dataclass(frozen=True)
class Derived:
    """An attribute computed from the relation graph and never written back."""

    name: str
    kind: str
    relation: str
    description: str | None = None


@dataclass(frozen=True)
class Field:
    name: str
    type: str
    required: bool = False
    enum: tuple[Any, ...] | None = None
    default: Any = None
    description: str | None = None


@dataclass(frozen=True)
class EntityType:
    name: str
    prefix: str
    dir: str
    layout: str
    title: str
    workflow: Workflow
    section_file: str = DEFAULT_SECTION_FILE
    initial_layout: str = DEFAULT_INITIAL_LAYOUT
    fields: dict[str, Field] = field(default_factory=dict)
    artifacts: tuple[str, ...] = ()
    progress_artifact: str | None = None
    journal_artifact: str | None = None
    derived: dict[str, Derived] = field(default_factory=dict)
    intake: bool = False
    actionable: bool = False
    tracks_progress: bool = False
    can_parent: bool = False
    schedulable: bool = False
    persistent: bool = False

    @property
    def is_dynamic_layout(self) -> bool:
        """Entries may take any layout, and may be laid out again later."""
        return self.layout == "dynamic"

    @property
    def layouts(self) -> tuple[str, ...]:
        """Every layout an entry of this type may be found in.

        One value for a type that declared a layout, all three for `dynamic`.
        Callers ask this rather than comparing the string, which is why adding
        `dynamic` did not add a branch anywhere outside this file.
        """
        return LAYOUTS if self.is_dynamic_layout else (self.layout,)

    @property
    def is_directory_layout(self) -> bool:
        return self.layout == "directory"

    @property
    def is_section_layout(self) -> bool:
        return self.layout == "section"

    @property
    def holds_sections(self) -> bool:
        """Whether this type has a shared file to look in at all."""
        return "section" in self.layouts

    @property
    def new_entry_layout(self) -> str:
        """The layout an entry arrives in."""
        return self.initial_layout if self.is_dynamic_layout else self.layout

    @property
    def carries_artifacts(self) -> bool:
        """Whether an entry of this type can have a file beside it — now, or
        after being moved into a directory."""
        return "directory" in self.layouts

    def flag(self, name: str) -> bool:
        if name not in CAPABILITY_FLAGS:
            raise KeyError(name)
        return bool(getattr(self, name))


@dataclass(frozen=True)
class Relation:
    name: str
    inverse: str
    source_types: tuple[str, ...]
    target_types: tuple[str, ...]
    cardinality: str
    acyclic: bool = False
    required_on: tuple[str, ...] = ()
    description: str | None = None

    @property
    def is_single(self) -> bool:
        return self.cardinality == "one"


@dataclass(frozen=True)
class RoadmapSpec:
    name: str
    horizons: tuple[str, ...]
    title: str
    requires_scheduling: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ViewSpec:
    name: str
    template: str
    output: str
    title: str


@dataclass(frozen=True)
class RecordSpec:
    name: str
    data: str
    template: str
    output: str
    title: str
    schema: str | None = None


@dataclass(frozen=True)
class Config:
    """A validated project configuration, with paths resolved against `root`."""

    root: Path
    source: Path | None
    data: dict[str, Any]
    entity_types: dict[str, EntityType]
    relations: dict[str, Relation]
    roadmaps: dict[str, RoadmapSpec]
    views: dict[str, ViewSpec]
    records: dict[str, RecordSpec]

    # -- lookups ------------------------------------------------------------

    @property
    def parent_relation(self) -> Relation:
        return self.relations[self.data["parent_relation"]]

    def type_for(self, name: str) -> EntityType:
        try:
            return self.entity_types[name]
        except KeyError:
            known = sorted(self.entity_types)
            # No single fix exists — we cannot know which type was meant — so
            # line 2 is the diagnostic that reveals the answer, per C2.
            raise ConfigError(
                f"unknown entity type {name!r}.",
                fix=f"szsdlc list --type {known[0]}" if known else "szsdlc init",
                see=f"types: {', '.join(known)}",
            ) from None

    def type_for_prefix(self, prefix: str) -> EntityType | None:
        """Prefix lookup is case-insensitive so `szsdlc show wi-42` resolves."""
        return self._by_prefix.get(prefix.upper())

    @property
    def _by_prefix(self) -> dict[str, EntityType]:
        return {t.prefix: t for t in self.entity_types.values()}

    @property
    def prefixes(self) -> tuple[str, ...]:
        return tuple(sorted(t.prefix for t in self.entity_types.values()))

    def types_with(self, flag: str) -> tuple[EntityType, ...]:
        """Every type declaring `flag` — the only legitimate way to ask
        "which types are work?", since no module may name a type."""
        return tuple(t for t in self.entity_types.values() if t.flag(flag))

    def relations_from(self, type_name: str) -> tuple[Relation, ...]:
        """Relations this type may author. Derived from each relation's
        `source_types` rather than restated per type."""
        return tuple(r for r in self.relations.values() if type_name in r.source_types)

    def relation_for_inverse(self, inverse: str) -> Relation | None:
        return next((r for r in self.relations.values() if r.inverse == inverse), None)

    def only_roadmap(self) -> RoadmapSpec:
        """The default when `--roadmap` is omitted; ambiguous only if several."""
        if len(self.roadmaps) == 1:
            return next(iter(self.roadmaps.values()))
        names = ", ".join(sorted(self.roadmaps)) or "(none configured)"
        raise ConfigError(
            f"--roadmap is required: {len(self.roadmaps)} roadmaps are configured.",
            fix=f"pass --roadmap with one of: {names}",
        )

    # -- resolved paths -----------------------------------------------------

    def _path(self, key: str) -> Path:
        return (self.root / self.data["paths"][key]).resolve()

    @property
    def entities_dir(self) -> Path:
        return self._path("entities")

    @property
    def roadmaps_dir(self) -> Path:
        return self._path("roadmaps")

    @property
    def views_dir(self) -> Path:
        return self._path("views")

    @property
    def records_dir(self) -> Path:
        return self._path("records")

    @property
    def standards_dir(self) -> Path:
        return self._path("standards")

    @property
    def templates_dir(self) -> Path:
        return self._path("templates")

    @property
    def entity_filename(self) -> str:
        return self.data["paths"]["entity_file"]

    def dir_for(self, entity_type: EntityType | str) -> Path:
        et = entity_type if isinstance(entity_type, EntityType) else self.type_for(entity_type)
        return (self.entities_dir / et.dir).resolve()

    def section_path(self, entity_type: EntityType | str) -> Path:
        """The shared file holding one `section`-layout type's entries."""
        et = entity_type if isinstance(entity_type, EntityType) else self.type_for(entity_type)
        return self.dir_for(et) / et.section_file

    def roadmap_path(self, name: str) -> Path:
        return self.roadmaps_dir / f"{name}.yml"

    def view_path(self, name: str) -> Path:
        return self.views_dir / self.views[name].output

    def record_path(self, name: str) -> Path:
        return self.records_dir / self.records[name].output

    # -- id formatting ------------------------------------------------------

    @property
    def id_key(self) -> str | None:
        return self.data["id"].get("key")

    @property
    def id_padding(self) -> int:
        return int(self.data["id"]["padding"])

    @property
    def id_pattern(self) -> str:
        return self.data["id"]["pattern"]


# ---------------------------------------------------------------------------
# Bundled data
# ---------------------------------------------------------------------------


def _package_text(relative: str) -> str:
    """Read a data file shipped inside the package.

    Addressed from the `szsdlc` package rather than from a sub-package, so
    `defaults/` and `schemas/` stay plain data directories with no `__init__`.
    """
    return resources.files("szsdlc").joinpath(relative).read_text(encoding="utf-8")


def load_defaults() -> dict[str, Any]:
    """The six shipped entity types and everything else, as plain data.

    Re-read rather than cached: callers merge into the result, and a shared
    mutable default is the kind of action-at-a-distance this framework is
    supposed to be free of.
    """
    return yaml.safe_load(_package_text("defaults/config.yml"))


@lru_cache(maxsize=1)
def load_schema() -> dict[str, Any]:
    return json.loads(_package_text("schemas/config.schema.json"))


# ---------------------------------------------------------------------------
# Locating the project
# ---------------------------------------------------------------------------


def find_project_root(start: Path | str | None = None) -> Path | None:
    """Walk up from `start` looking for `.szsdlc/config.yml`."""
    here = Path(start or Path.cwd()).resolve()
    if here.is_file():
        here = here.parent
    for candidate in (here, *here.parents):
        if (candidate / CONFIG_DIRNAME / CONFIG_FILENAME).is_file():
            return candidate
    return None


def config_path_for(root: Path) -> Path:
    return root / CONFIG_DIRNAME / CONFIG_FILENAME


# ---------------------------------------------------------------------------
# Merging
# ---------------------------------------------------------------------------


def _strip_replace(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {k: _strip_replace(v) for k, v in value.items() if k != REPLACE_KEY}
    if isinstance(value, list):
        return [_strip_replace(v) for v in value]
    return value


def deep_merge(base: Any, override: Any) -> Any:
    """Merge `override` over `base`.

    Three behaviours, each earning its place:

    - mappings merge key by key, so a project can flip one flag;
    - a mapping carrying ``_replace: true`` replaces its counterpart outright,
      which is how the default entity types get swapped wholesale;
    - a ``null`` override *deletes* a declared block (``spike: null`` drops the
      type), but only when the base value is itself a mapping — setting a
      scalar to null must stay an ordinary assignment, or ``id.key: null``
      would silently remove the key rather than clear it.

    Lists always replace. Merging them positionally is never what anyone means.
    """
    if isinstance(override, Mapping) and override.get(REPLACE_KEY) is True:
        return _strip_replace(dict(override))

    if not (isinstance(base, Mapping) and isinstance(override, Mapping)):
        return _strip_replace(override)

    merged = dict(base)
    for key, value in override.items():
        if key == REPLACE_KEY:
            continue
        if value is None and isinstance(merged.get(key), Mapping):
            merged.pop(key, None)
            continue
        if key in merged:
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = _strip_replace(value)
    return merged


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------

_ADDITIONAL_PROP = re.compile(r"'([^']+)' was unexpected")


def _key_path(parts: Iterable[Any]) -> str:
    out = ""
    for part in parts:
        if isinstance(part, int):
            out += f"[{part}]"
        else:
            out = f"{out}.{part}" if out else str(part)
    return out or "(root)"


def _schema_check(data: Any, where: str) -> None:
    validator = Draft202012Validator(load_schema())
    errors = sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path))
    if not errors:
        return

    err = errors[0]
    path = list(err.absolute_path)
    # An unknown key is reported against its *parent*, which is the least
    # useful place to point. Name the key itself.
    if err.validator == "additionalProperties":
        match = _ADDITIONAL_PROP.search(err.message)
        if match:
            path.append(match.group(1))

    key = _key_path(path)
    extra = f" ({len(errors) - 1} more)" if len(errors) > 1 else ""
    raise ConfigError(
        f"{where}: {key}: {err.message}{extra}",
        fix=_schema_fix(err, key),
    )


def _schema_fix(err: Any, key: str) -> str:
    schema = err.schema if isinstance(err.schema, Mapping) else {}
    if err.validator == "additionalProperties":
        allowed = sorted(schema.get("properties", {}))
        if allowed:
            return f"remove it, or use one of: {', '.join(allowed)}"
        return f"remove {key}"
    if err.validator == "enum":
        return f"set {key} to one of: {', '.join(map(str, err.validator_value))}"
    if err.validator == "required":
        return f"add the missing key under {key}"
    if err.validator == "const":
        return f"set {key} to {err.validator_value!r}"
    return f"correct {key} in .szsdlc/config.yml"


# ---------------------------------------------------------------------------
# Semantic validation
#
# One check per rule, each naming the key it is unhappy about. These are the
# cross-references JSON Schema cannot express.
# ---------------------------------------------------------------------------


def _fail(key: str, problem: str, fix: str) -> None:
    raise ConfigError(f"config: {key}: {problem}", fix=fix)


def _check_ids(data: dict[str, Any]) -> None:
    pattern = data["id"]["pattern"]
    for token in ("{prefix}", "{number}"):
        if token not in pattern:
            _fail("id.pattern", f"{pattern!r} does not contain {token}.",
                  "include {prefix} and {number}, e.g. \"{prefix}-{number}\"")
    key = data["id"].get("key")
    if key and "{key}" not in pattern:
        _fail("id.pattern", f"id.key is {key!r} but the pattern never uses {{key}}.",
              "add {key} to id.pattern, or set id.key to null")
    if not key and "{key}" in pattern:
        _fail("id.pattern", "the pattern uses {key} but id.key is not set.",
              "set id.key, or remove {key} from id.pattern")


def _check_paths(data: dict[str, Any]) -> None:
    for name, value in data["paths"].items():
        if name == "entity_file":
            continue
        if ".." in Path(value).parts:
            _fail(f"paths.{name}", f"{value!r} escapes the project root.",
                  f"set paths.{name} to a path inside the project")


def _check_entity_types(data: dict[str, Any]) -> None:
    types = data["entity_types"]

    seen_prefix: dict[str, str] = {}
    seen_dir: dict[str, str] = {}

    for name, spec in types.items():
        base = f"entity_types.{name}"

        prefix = spec["prefix"]
        if prefix in seen_prefix:
            _fail(f"{base}.prefix", f"prefix {prefix!r} is already used by {seen_prefix[prefix]!r}.",
                  f"give {name} a prefix no other type uses")
        seen_prefix[prefix] = name

        directory = spec["dir"]
        if ".." in Path(directory).parts:
            _fail(f"{base}.dir", f"{directory!r} escapes the project root.",
                  f"set {base}.dir to a path inside the project")
        if directory in seen_dir:
            _fail(f"{base}.dir", f"directory {directory!r} is already used by {seen_dir[directory]!r}.",
                  f"give {name} a directory no other type uses")
        seen_dir[directory] = name

        layout = spec["layout"]
        holds = LAYOUTS if layout == "dynamic" else (layout,)

        artifacts = set(spec.get("artifacts") or ())
        if artifacts and "directory" not in holds:
            _fail(f"{base}.artifacts",
                  f"a `{layout}` layout entity has nowhere to put an artifact.",
                  f"set {base}.layout to directory, or remove {base}.artifacts")

        if spec.get("section_file") and "section" not in holds:
            _fail(f"{base}.section_file",
                  f"a `{layout}` layout entity is not stored in a shared file.",
                  f"set {base}.layout to section, or remove {base}.section_file")

        if spec.get("initial_layout") and layout != "dynamic":
            _fail(f"{base}.initial_layout",
                  f"a `{layout}` layout entity has only one layout to arrive in.",
                  f"set {base}.layout to dynamic, or remove {base}.initial_layout")

        for slot in ("progress_artifact", "journal_artifact"):
            value = spec.get(slot)
            if value and value not in artifacts:
                _fail(f"{base}.{slot}", f"{value!r} is not listed in {base}.artifacts.",
                      f"add {value} to {base}.artifacts")

        if spec.get("tracks_progress") and not spec.get("progress_artifact"):
            _fail(f"{base}.tracks_progress", "progress is tracked but no progress_artifact is declared.",
                  f"set {base}.progress_artifact, or set {base}.tracks_progress to false")

        for field_name in spec.get("fields") or ():
            if field_name in CORE_FIELDS:
                _fail(f"{base}.fields.{field_name}", f"{field_name!r} is a core frontmatter field.",
                      f"remove {base}.fields.{field_name}; the framework already provides it")

        _check_workflow(name, spec, artifacts)


def _check_workflow(name: str, spec: dict[str, Any], artifacts: set[str]) -> None:
    base = f"entity_types.{name}.workflow"
    workflow = spec["workflow"]
    states = workflow["states"]

    if workflow["initial"] not in states:
        _fail(f"{base}.initial", f"{workflow['initial']!r} is not one of this type's states.",
              f"set {base}.initial to one of: {', '.join(sorted(states))}")

    for state_name, state in states.items():
        state = state or {}
        skey = f"{base}.states.{state_name}"

        for target in state.get("to") or ():
            if target not in states:
                _fail(f"{skey}.to", f"transition target {target!r} is not a state of {name}.",
                      f"use one of: {', '.join(sorted(states))}")

        if state.get("terminal") and state.get("to"):
            _fail(f"{skey}", "a terminal state cannot have outgoing transitions.",
                  f"remove {skey}.to, or set {skey}.terminal to false")

        if state.get("abandoned") and not state.get("terminal"):
            _fail(f"{skey}.abandoned", "only a terminal state can be abandoned.",
                  f"set {skey}.terminal to true")

        for artifact in state.get("requires_artifact") or ():
            if artifact not in artifacts:
                _fail(f"{skey}.requires_artifact", f"{artifact!r} is not an artifact of {name}.",
                      f"add {artifact} to entity_types.{name}.artifacts")

        if state.get("requires_tasks_complete") and not spec.get("tracks_progress"):
            _fail(f"{skey}.requires_tasks_complete",
                  f"{name} does not declare tracks_progress, so it has no tasks to complete.",
                  f"set entity_types.{name}.tracks_progress to true")

    reachable = {workflow["initial"]}
    frontier = [workflow["initial"]]
    while frontier:
        current = frontier.pop()
        for target in (states.get(current) or {}).get("to") or ():
            if target not in reachable:
                reachable.add(target)
                frontier.append(target)
    abandoned = [n for n, s in states.items() if (s or {}).get("abandoned")]
    if len(abandoned) > 1:
        _fail(f"{base}.states", f"more than one abandoned status: {', '.join(abandoned)}.",
              f"mark exactly one terminal state of {name} as abandoned")

    orphans = sorted(set(states) - reachable)
    if orphans:
        _fail(f"{base}.states", f"unreachable from {workflow['initial']!r}: {', '.join(orphans)}.",
              f"add a transition into {orphans[0]}, or remove it")


def _check_relations(data: dict[str, Any]) -> None:
    types = data["entity_types"]
    relations = data["relations"]

    names = set(relations)
    seen_inverse: dict[str, str] = {}

    for name, spec in relations.items():
        base = f"relations.{name}"

        inverse = spec["inverse"]
        if inverse in names:
            _fail(f"{base}.inverse", f"{inverse!r} is also the name of an authored relation.",
                  f"rename {base}.inverse; a generated back-link may not shadow one")
        if inverse in seen_inverse:
            _fail(f"{base}.inverse", f"{inverse!r} is already the inverse of {seen_inverse[inverse]!r}.",
                  f"give {base}.inverse a name no other relation uses")
        if inverse in CORE_FIELDS:
            _fail(f"{base}.inverse", f"{inverse!r} is a core frontmatter field.",
                  f"rename {base}.inverse")
        seen_inverse[inverse] = name

        for slot in ("source_types", "target_types", "required_on"):
            for type_name in spec.get(slot) or ():
                if type_name not in types:
                    _fail(f"{base}.{slot}", f"unknown entity type {type_name!r}.",
                          f"use one of: {', '.join(sorted(types))}")

        for type_name in spec.get("required_on") or ():
            if type_name not in (spec.get("source_types") or ()):
                _fail(f"{base}.required_on",
                      f"{type_name!r} is required to author {name} but is not in {base}.source_types.",
                      f"add {type_name} to {base}.source_types")

    _check_parent_relation(data)


def _check_parent_relation(data: dict[str, Any]) -> None:
    types = data["entity_types"]
    relations = data["relations"]
    name = data["parent_relation"]

    if name not in relations:
        _fail("parent_relation", f"{name!r} is not a declared relation.",
              f"use one of: {', '.join(sorted(relations))}")

    parents = sorted(n for n, t in types.items() if t.get("can_parent"))
    declared = relations[name].get("target_types")

    if declared is None:
        if not parents:
            _fail("parent_relation",
                  f"relation {name!r} derives its targets from can_parent, but no type declares it.",
                  "set can_parent: true on the type that groups work, e.g. epic")
        return

    for type_name in declared:
        if not types[type_name].get("can_parent"):
            _fail(f"relations.{name}.target_types",
                  f"{type_name!r} is a {name} target but does not declare can_parent.",
                  f"set entity_types.{type_name}.can_parent to true, "
                  f"or remove it from relations.{name}.target_types")


def _check_derived(data: dict[str, Any]) -> None:
    relations = data["relations"]
    inverses = {spec["inverse"] for spec in relations.values()}

    for name, spec in data["entity_types"].items():
        for attr, decl in (spec.get("derived") or {}).items():
            base = f"entity_types.{name}.derived.{attr}"

            if decl["relation"] not in relations:
                _fail(f"{base}.relation", f"unknown relation {decl['relation']!r}.",
                      f"use one of: {', '.join(sorted(relations))}")

            # A derived attribute that shadows a generated inverse states one
            # fact twice — exactly what this framework exists to prevent.
            if attr in inverses or attr in relations or attr in CORE_FIELDS:
                _fail(base, f"{attr!r} already names a relation, a generated inverse, or a core field.",
                      f"rename {base}, or drop it and use the existing name")
            if attr in (spec.get("fields") or {}):
                _fail(base, f"{attr!r} is also declared as a stored field of {name}.",
                      f"remove entity_types.{name}.fields.{attr}; derived attributes are never stored")


def _check_roadmaps(data: dict[str, Any]) -> None:
    types = data["entity_types"]

    for name, spec in (data.get("roadmaps") or {}).items():
        base = f"roadmaps.{name}"
        for type_name, status in (spec.get("requires_scheduling") or {}).items():
            key = f"{base}.requires_scheduling.{type_name}"

            if type_name not in types:
                _fail(key, f"unknown entity type {type_name!r}.",
                      f"use one of: {', '.join(sorted(types))}")

            entity_type = types[type_name]
            if not entity_type.get("schedulable"):
                _fail(key, f"{type_name!r} does not declare schedulable, so it cannot be placed on a roadmap.",
                      f"set entity_types.{type_name}.schedulable to true, or remove {key}")

            states = entity_type["workflow"]["states"]
            if status not in states:
                _fail(key, f"{status!r} is not a status of {type_name}.",
                      f"use one of: {', '.join(sorted(states))}")
            if (states[status] or {}).get("terminal"):
                _fail(key, f"{status!r} is terminal; finished work is never scheduled.",
                      f"use a non-terminal status of {type_name}")


def _check_outputs(data: dict[str, Any]) -> None:
    for section in ("views", "records"):
        for name, spec in (data.get(section) or {}).items():
            for slot in ("output", "template", "data"):
                value = spec.get(slot)
                if value and ".." in Path(value).parts:
                    _fail(f"{section}.{name}.{slot}", f"{value!r} escapes the project root.",
                          f"set {section}.{name}.{slot} to a path inside the project")


def check_semantics(data: dict[str, Any]) -> None:
    """Every cross-reference check, in dependency order."""
    _check_ids(data)
    _check_paths(data)
    _check_entity_types(data)
    _check_relations(data)
    _check_derived(data)
    _check_roadmaps(data)
    _check_outputs(data)


# ---------------------------------------------------------------------------
# Building the resolved objects
# ---------------------------------------------------------------------------


def _build_workflow(spec: dict[str, Any]) -> Workflow:
    states = {}
    for name, raw in spec["states"].items():
        raw = raw or {}
        states[name] = State(
            name=name,
            to=tuple(raw.get("to") or ()),
            terminal=bool(raw.get("terminal", False)),
            abandoned=bool(raw.get("abandoned", False)),
            requires_artifact=tuple(raw.get("requires_artifact") or ()),
            requires_tasks_complete=bool(raw.get("requires_tasks_complete", False)),
            description=raw.get("description"),
        )
    return Workflow(initial=spec["initial"], states=states)


def _build_entity_type(name: str, spec: dict[str, Any]) -> EntityType:
    fields = {
        field_name: Field(
            name=field_name,
            type=raw["type"],
            required=bool(raw.get("required", False)),
            enum=tuple(raw["enum"]) if raw.get("enum") is not None else None,
            default=raw.get("default"),
            description=raw.get("description"),
        )
        for field_name, raw in (spec.get("fields") or {}).items()
    }
    derived = {
        attr: Derived(
            name=attr,
            kind=raw["kind"],
            relation=raw["relation"],
            description=raw.get("description"),
        )
        for attr, raw in (spec.get("derived") or {}).items()
    }
    return EntityType(
        name=name,
        prefix=spec["prefix"],
        dir=spec["dir"],
        layout=spec["layout"],
        section_file=spec.get("section_file") or DEFAULT_SECTION_FILE,
        initial_layout=spec.get("initial_layout") or DEFAULT_INITIAL_LAYOUT,
        title=spec.get("title") or name.replace("_", " ").capitalize(),
        workflow=_build_workflow(spec["workflow"]),
        fields=fields,
        artifacts=tuple(spec.get("artifacts") or ()),
        progress_artifact=spec.get("progress_artifact"),
        journal_artifact=spec.get("journal_artifact"),
        derived=derived,
        **{flag: bool(spec.get(flag, False)) for flag in CAPABILITY_FLAGS},
    )


def _build_relation(name: str, spec: dict[str, Any], can_parent: tuple[str, ...],
                    parent_name: str) -> Relation:
    targets = spec.get("target_types")
    if targets is None:
        # Only the parent relation may leave this open; `can_parent` is where
        # that fact lives, and restating it here would be a second home for it.
        targets = can_parent if name == parent_name else ()
    return Relation(
        name=name,
        inverse=spec["inverse"],
        source_types=tuple(spec.get("source_types") or ()),
        target_types=tuple(targets),
        cardinality=spec["cardinality"],
        acyclic=bool(spec.get("acyclic", False)),
        required_on=tuple(spec.get("required_on") or ()),
        description=spec.get("description"),
    )


def build(data: dict[str, Any], root: Path, source: Path | None = None) -> Config:
    """Turn validated config data into resolved objects. Assumes checks passed."""
    entity_types = {name: _build_entity_type(name, spec)
                    for name, spec in data["entity_types"].items()}
    can_parent = tuple(name for name, t in entity_types.items() if t.can_parent)
    parent_name = data["parent_relation"]

    relations = {name: _build_relation(name, spec, can_parent, parent_name)
                 for name, spec in data["relations"].items()}

    roadmaps = {
        name: RoadmapSpec(
            name=name,
            horizons=tuple(spec["horizons"]),
            title=spec.get("title") or name,
            requires_scheduling=dict(spec.get("requires_scheduling") or {}),
        )
        for name, spec in (data.get("roadmaps") or {}).items()
    }
    views = {
        name: ViewSpec(name=name, template=spec["template"], output=spec["output"],
                       title=spec.get("title") or name)
        for name, spec in (data.get("views") or {}).items()
    }
    records = {
        name: RecordSpec(name=name, data=spec["data"], template=spec["template"],
                         output=spec["output"], title=spec.get("title") or name,
                         schema=spec.get("schema"))
        for name, spec in (data.get("records") or {}).items()
    }

    return Config(root=root, source=source, data=data, entity_types=entity_types,
                  relations=relations, roadmaps=roadmaps, views=views, records=records)


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def default_config(root: Path | str = ".") -> Config:
    """The shipped configuration alone — used by `init` and by tests."""
    data = load_defaults()
    _schema_check(data, "built-in defaults")
    check_semantics(data)
    return build(data, Path(root).resolve())


def load_data(root: Path) -> dict[str, Any]:
    """Read one project's config file and merge it over the defaults."""
    path = config_path_for(root)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"{path}: cannot be read ({exc.strerror}).",
                          fix="szsdlc init") from None

    try:
        override = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f"{path}:{mark.line + 1}" if mark else str(path)
        problem = getattr(exc, "problem", None) or "is not valid YAML"
        raise ConfigError(f"{where}: {problem}.",
                          fix=f"correct the YAML syntax in {path}") from None

    if override is None:
        override = {}
    if not isinstance(override, Mapping):
        raise ConfigError(f"{path}: the top level must be a mapping, not "
                          f"{type(override).__name__}.",
                          fix=f"make {path} a mapping of config sections")

    return deep_merge(load_defaults(), dict(override))


def load(start: Path | str | None = None) -> Config:
    """Locate, merge, validate and resolve the config for the project at `start`."""
    root = find_project_root(start)
    if root is None:
        here = Path(start or Path.cwd()).resolve()
        raise ConfigError(
            f"no szsdlc project: {CONFIG_DIRNAME}/{CONFIG_FILENAME} not found in "
            f"{here} or any parent.",
            fix="szsdlc init",
        )

    data = load_data(root)
    source = config_path_for(root)
    _schema_check(data, str(source))
    check_semantics(data)
    return build(data, root, source)
