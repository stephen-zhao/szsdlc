"""The operations that change entities.

Each one checks everything before it writes, so a refusal never leaves half a
change; honours `expect` (a content hash from `show`) when given; returns the
post-state, so no confirming read is needed (C1); and ends by redrawing the
views. `szsdlc._cli` puts them on the command line; nothing here prints or
reads stdin.
"""

from __future__ import annotations

import datetime as dt
import shlex
import shutil
from dataclasses import dataclass
from typing import Any

from ..config import LAYOUTS
from ..errors import BadInput
from ..model import Entity, create_entity, relayout
from ..text import add_tags, clip, nearest, remove_tags
from . import _journal, _precondition, _status, _views
from ._project import Project
from ._registry import operation
from ._types import Data


def _relative(project: Project, path) -> str:
    try:
        return str(path.relative_to(project.config.root)).replace("\\", "/")
    except ValueError:  # pragma: no cover - paths are always under the root
        return str(path)


def _display_path(project: Project, entity: Entity) -> str:
    where = entity.home or entity.path
    try:
        return str(where.relative_to(project.config.root).as_posix())
    except ValueError:  # pragma: no cover - a project outside its own root
        return str(where)


def _entity(project: Project, ref: str, expect: str | None) -> Entity:
    entity = project.entity(ref)
    _precondition.check(entity, expect)
    return entity


# ------------------------------------------------------------------ capture

@dataclass(frozen=True)
class CaptureParams(Data):
    text: str
    #: The intake type, when a project declares more than one.
    type: str | None = None


@dataclass(frozen=True)
class CaptureResult(Data):
    id: str


@operation("capture", params=CaptureParams, result=CaptureResult, kind="mutation")
def capture(params: CaptureParams, project: Project) -> CaptureResult:
    entity_type = project.intake_type(params.type)
    body = params.text.strip()
    if not body:
        raise BadInput(
            "nothing to capture: the input was empty.",
            fix='szsdlc capture "the thought"',
        )
    entity = create_entity(project.config, project.ids, entity_type, body=body + "\n")
    _views.redraw(project)
    return CaptureResult(entity.id.text)


# ---------------------------------------------------------------------- new

@dataclass(frozen=True)
class NewParams(Data):
    type: str
    title: str
    #: The import path for existing history; checked against the workflow,
    #: but its entry gates are not (a new entity has no artifacts).
    status: str | None = None


@dataclass(frozen=True)
class NewResult(Data):
    id: str
    path: str
    status: str | None
    #: The gates `status` has not met yet, which `validate` will report.
    owes: tuple[str, ...] = ()
    #: Whether `status` was given, as against left to the workflow.
    imported: bool = False


@operation("new", params=NewParams, result=NewResult, kind="mutation")
def new(params: NewParams, project: Project) -> NewResult:
    from ..workflow import unmet_gates

    entity_type = project.config.type_for(params.type)
    status = params.status
    if status is not None:
        _check_import_status(entity_type, params.title, status)
    entity = create_entity(project.config, project.ids, entity_type,
                           title=params.title, status=status)
    owes = tuple(gate.reason for gate in unmet_gates(entity, status)) if status is not None else ()
    _views.redraw(project)
    return NewResult(entity.id.text, _relative(project, entity.path), entity.status, owes,
                     imported=status is not None)


def _check_import_status(entity_type: Any, title: str, status: str) -> None:
    """Refuse a `new --status` this type cannot be created at."""
    # Every value in a fix is quoted, and the title is passed so argparse can
    # never read it as an option (`--title=…`, `-- text`): the fix is meant to
    # be run as printed, whatever the caller typed.
    quoted = shlex.quote(title if title.strip() else "<text>")
    name = shlex.quote(entity_type.name)
    if entity_type.intake:
        raise BadInput(
            f"new: {entity_type.name} is an intake type; its status is written "
            f"by capture, refine and drop, never by --status.",
            fix=f"szsdlc capture --type {name} -- {quoted}",
        )
    workflow = entity_type.workflow
    if status not in workflow.states:
        terminal = workflow.completed_status or workflow.initial
        raise BadInput(
            f"new: {status!r} is not a status of {entity_type.name}.",
            fix=f"szsdlc new {name} --title={quoted} --status {shlex.quote(terminal)}",
            see=f"statuses: {', '.join(workflow.states)}",
        )


# ---------------------------------------------------------------------- set

#: C3 — one job per command. These have their own verbs, and composing them on
#: a command line is exactly the input that takes an agent three attempts.
DELEGATED_FIELDS = {
    "relations": "szsdlc link {ref} <relation> <ref>",
    "tags": "szsdlc tag {ref} <tag>",
    "id": "szsdlc convert {ref} <TYPE>",
}


@dataclass(frozen=True)
class SetParams(Data):
    ref: str
    #: `field=value`, as typed, in order.
    assignments: tuple[str, ...]
    #: Override the workflow for a status= assignment; needs `reason`.
    force: bool = False
    reason: str | None = None
    expect: str | None = None


@dataclass(frozen=True)
class Change(Data):
    field: str
    before: Any
    after: Any
    #: Set when a status move overrode the workflow.
    forced: str | None = None
    #: What the change implied: "off main", "blocks again: WI-0012", …
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class SetResult(Data):
    id: str
    changes: tuple[Change, ...]


def _coerce(entity: Entity, field: str, raw: str) -> Any:
    spec = entity.type.fields.get(field)
    if spec is None:
        return raw
    try:
        if spec.type == "date":
            return dt.date.fromisoformat(raw)
        if spec.type == "integer":
            return int(raw)
        if spec.type == "number":
            return float(raw)
        if spec.type == "boolean":
            if raw.lower() not in {"true", "false"}:
                raise ValueError(raw)
            return raw.lower() == "true"
    except ValueError:
        raise BadInput(
            f"{entity.id.text}: {field}={raw!r} is not a valid {spec.type}.",
            fix=f"szsdlc set {entity.id.text} {field}=<{spec.type}>",
        ) from None
    if spec.type == "array":
        raise BadInput(
            f"set: {field!r} is list-valued and is never edited through `set`.",
            fix=f"edit {field} in the entity's frontmatter directly",
        )
    if spec.enum and raw not in [str(v) for v in spec.enum]:
        raise BadInput(
            f"{entity.id.text}: {field}={raw!r} is not one of "
            f"{', '.join(str(v) for v in spec.enum)}.",
            fix=f"szsdlc set {entity.id.text} {field}={spec.enum[0]}",
        )
    return raw


@operation("set", params=SetParams, result=SetResult, kind="mutation")
def set_(params: SetParams, project: Project) -> SetResult:
    from .. import workflow as workflow_module

    entity = _entity(project, params.ref, params.expect)
    settable = ["title", "status", *entity.type.fields]
    changes: list[Change] = []
    reason = _status.force_reason(entity, params.assignments, params.force, params.reason)

    for assignment in params.assignments:
        field, separator, raw = assignment.partition("=")
        if not separator:
            raise BadInput(
                f"set: {assignment!r} is not field=value.",
                fix=f"szsdlc set {entity.id.text} "
                    f"status={workflow_module.suggested_next(entity)}",
            )

        field = field.strip()
        if field in DELEGATED_FIELDS:
            raise BadInput(
                f"set: {field!r} is not settable.",
                fix=DELEGATED_FIELDS[field].format(ref=entity.id.text),
            )
        if field not in settable:
            suggestion = nearest(field, settable)
            raise BadInput(
                f"set: {entity.type.name} has no field {field!r}"
                + (f"; did you mean {suggestion}?" if suggestion else "."),
                fix=f"szsdlc set {entity.id.text} {suggestion or settable[0]}=<value>",
            )

        if field == "status":
            was_terminal = entity.is_terminal
            transition = workflow_module.move(entity, raw, force=params.force)
            if transition.forced:
                assert reason is not None
                _status.record_forced_move(entity, transition, reason)
            notes = _status.after_status_change(project, entity, was_terminal)
            changes.append(Change("status", transition.before, transition.after,
                                  reason if transition.forced else None, tuple(notes)))
        else:
            before = entity.field(field)
            entity.set_field(field, _coerce(entity, field, raw))
            changes.append(Change(field, before, entity.field(field)))

    entity.save()
    _views.redraw(project)
    return SetResult(entity.id.text, tuple(changes))


# -------------------------------------------------------------- tag / untag

@dataclass(frozen=True)
class TagParams(Data):
    ref: str
    tags: tuple[str, ...]
    expect: str | None = None


@dataclass(frozen=True)
class TagResult(Data):
    id: str
    #: Every tag the entity has now.
    tags: tuple[str, ...]


def _apply_tags(params: TagParams, project: Project, adding: bool) -> TagResult:
    entity = _entity(project, params.ref, params.expect)
    before = entity.tags
    entity.set_tags(add_tags(before, params.tags) if adding else remove_tags(before, params.tags))
    entity.save()
    _views.redraw(project)
    return TagResult(entity.id.text, tuple(entity.tags))


@operation("tag", params=TagParams, result=TagResult, kind="mutation")
def tag(params: TagParams, project: Project) -> TagResult:
    return _apply_tags(params, project, adding=True)


@operation("untag", params=TagParams, result=TagResult, kind="mutation")
def untag(params: TagParams, project: Project) -> TagResult:
    return _apply_tags(params, project, adding=False)


# ------------------------------------------------------------ link / unlink

@dataclass(frozen=True)
class LinkParams(Data):
    ref: str
    relation: str
    target: str
    expect: str | None = None


@dataclass(frozen=True)
class LinkResult(Data):
    id: str
    relation: str
    target: str
    #: The generated inverse, so the back-link never has to be looked up.
    inverse: str | None


@dataclass(frozen=True)
class UnlinkResult(Data):
    id: str
    relation: str
    #: The relation's targets after the removal.
    remaining: tuple[str, ...]


def _relation_for(project: Project, entity: Entity, name: str):
    allowed = [r.name for r in project.config.relations_from(entity.type.name)]
    if name in allowed:
        return project.config.relations[name]
    inverse = project.config.relation_for_inverse(name)
    if inverse is not None:
        raise BadInput(
            f"link: {name!r} is a generated inverse and is never authored.",
            fix=f"szsdlc link <ref> {inverse.name} {entity.id.text}",
        )
    suggestion = nearest(name, allowed)
    raise BadInput(
        f"link: a {entity.type.name} cannot author {name!r}"
        + (f"; did you mean {suggestion}?" if suggestion else "."),
        fix=f"szsdlc link {entity.id.text} {suggestion or (allowed or ['<relation>'])[0]} <ref>",
    )


@operation("link", params=LinkParams, result=LinkResult, kind="mutation")
def link(params: LinkParams, project: Project) -> LinkResult:
    entity = _entity(project, params.ref, params.expect)
    relation = _relation_for(project, entity, params.relation)
    target = project.entity(params.target)

    if relation.target_types and target.type.name not in relation.target_types:
        raise BadInput(
            f"link: {relation.name} may not point at a {target.type.name}.",
            fix=f"szsdlc list --type {sorted(relation.target_types)[0]}",
            see=f"allowed: {', '.join(sorted(relation.target_types))}",
        )
    if target.id == entity.id:
        raise BadInput(
            f"link: {entity.id.text} cannot {relation.name} itself.",
            fix=f"szsdlc show {entity.id.text}",
        )
    existing = entity.targets(relation.name)
    if target.id.text in existing:
        raise BadInput(
            f"link: {entity.id.text} already {relation.name} {target.id.text}.",
            fix=f"szsdlc show {entity.id.text}",
        )

    updated = [target.id.text] if relation.is_single else [*existing, target.id.text]
    entity.set_relation(relation.name, updated)
    entity.save()
    _views.redraw(project)
    return LinkResult(entity.id.text, relation.name, target.id.text, relation.inverse)


@operation("unlink", params=LinkParams, result=UnlinkResult, kind="mutation")
def unlink(params: LinkParams, project: Project) -> UnlinkResult:
    entity = _entity(project, params.ref, params.expect)
    relation = _relation_for(project, entity, params.relation)
    existing = entity.targets(relation.name)

    # Resolve tolerantly, but fall back to the literal text: unlinking a
    # dangling edge is exactly when the target cannot be resolved.
    try:
        wanted = project.ids.parse(params.target).text
    except BadInput:
        wanted = params.target
    if wanted not in existing:
        raise BadInput(
            f"unlink: {entity.id.text} does not {relation.name} {wanted}.",
            fix=f"szsdlc show {entity.id.text}",
            see=f"{relation.name}: {', '.join(existing) or '(none)'}",
        )

    entity.set_relation(relation.name, [t for t in existing if t != wanted])
    entity.save()
    _views.redraw(project)
    return UnlinkResult(entity.id.text, relation.name, tuple(entity.targets(relation.name)))


# ------------------------------------------------------------------ convert

@dataclass(frozen=True)
class ConvertParams(Data):
    ref: str
    type: str
    expect: str | None = None


@dataclass(frozen=True)
class ConvertResult(Data):
    #: The new entity's id; the old one is tombstoned and still resolves.
    id: str
    was: str
    status: str


@operation("convert", params=ConvertParams, result=ConvertResult, kind="mutation")
def convert(params: ConvertParams, project: Project) -> ConvertResult:
    entity = _entity(project, params.ref, params.expect)
    target_type = project.config.type_for(params.type)
    if target_type.name == entity.type.name:
        raise BadInput(
            f"convert: {entity.id.text} is already a {target_type.name}.",
            fix=f"szsdlc show {entity.id.text}",
        )

    extra = [p.name for p in entity.artifact_files()] if entity.home else []
    if extra and not target_type.carries_artifacts:
        raise BadInput(
            f"convert: a {target_type.name} has nowhere to put an artifact, "
            f"but {entity.id.text} carries {', '.join(extra)}.",
            fix=f"remove those artifacts, then rerun",
        )
    # Artifacts decide the child's layout when the type leaves it open. The
    # alternative is creating it in the cheapest one and dropping them.
    landing = "directory" if extra and target_type.is_dynamic_layout else None

    new_id = project.ids.next_id(target_type)
    # A status that exists in both workflows is kept; otherwise the entity
    # restarts, because a status from another workflow means nothing here.
    status = (entity.status if entity.status in target_type.workflow.states
              else target_type.workflow.initial)
    child = create_entity(
        project.config, project.ids, target_type,
        title=entity.title, body=entity.body, status=status,
        tags=entity.tags,
        relations={k: (v[0] if project.config.relations[k].is_single else v)
                   for k, v in entity.relations.items()
                   if k in {r.name for r in project.config.relations_from(target_type.name)}},
        layout=landing,
    )
    assert child.id == new_id
    if entity.home is not None and child.home is not None:
        for artifact in entity.artifact_files():
            shutil.copy2(artifact, child.home / artifact.name)
    entity.delete()

    tombstones = project.ids.tombstones
    tombstones.record(entity.id.text, child.id.text)
    tombstones.save()
    _views.redraw(project)
    return ConvertResult(child.id.text, entity.id.text, status)


# ------------------------------------------------------------------- layout
#
# Named for the thing it changes, not the thing it does to the disk. `layout`
# is already the word in the config key, in the schema and on the entity. It is
# `convert` for the other axis: convert changes an entry's type, layout
# changes what it occupies.

@dataclass(frozen=True)
class LayoutParams(Data):
    ref: str
    layout: str
    expect: str | None = None


@dataclass(frozen=True)
class LayoutResult(Data):
    id: str
    before: str
    after: str
    path: str


@operation("layout", params=LayoutParams, result=LayoutResult, kind="mutation")
def layout(params: LayoutParams, project: Project) -> LayoutResult:
    entity = _entity(project, params.ref, params.expect)
    target = params.layout
    if target not in LAYOUTS:
        raise BadInput(f"layout: {target!r} is not one of {', '.join(LAYOUTS)}.",
                       fix=f"szsdlc layout {entity.id.text} directory")
    if not entity.type.is_dynamic_layout:
        raise BadInput(
            f"layout: every {entity.type.name} is laid out as a "
            f"{entity.type.layout}, so there is no other layout to give it.",
            fix=f"set entity_types.{entity.type.name}.layout to dynamic",
        )
    if target == entity.layout:
        raise BadInput(
            f"layout: {entity.id.text} is already laid out as a {target}.",
            fix=f"szsdlc show {entity.id.text}",
        )
    carried = [p.name for p in entity.artifact_files()]
    if carried and target != "directory":
        raise BadInput(
            f"layout: a {target} has nowhere to put an artifact, but "
            f"{entity.id.text} carries {', '.join(carried)}.",
            fix="remove those artifacts, then rerun",
        )
    before = entity.layout
    laid = relayout(project.config, project.ids, entity, target)
    _views.redraw(project)
    return LayoutResult(laid.id.text, before, target, _display_path(project, laid))


# ---------------------------------------------------------------------- log

@dataclass(frozen=True)
class LogParams(Data):
    ref: str
    message: str
    expect: str | None = None


@dataclass(frozen=True)
class LogResult(Data):
    id: str
    #: The journal written to.
    path: str


@operation("log", params=LogParams, result=LogResult, kind="mutation")
def log(params: LogParams, project: Project) -> LogResult:
    entity = _entity(project, params.ref, params.expect)
    artifact = entity.type.journal_artifact
    if not artifact:
        raise BadInput(
            f"{entity.type.name} declares no journal artifact, so there is "
            f"nowhere to log to.",
            fix=f"set entity_types.{entity.type.name}.journal_artifact",
        )
    if entity.home is None and entity.type.is_dynamic_layout:
        # The only fix for the refusal would have been `layout … directory`,
        # and a refusal whose fix is one unconditional command is a step nobody
        # should have to type. Wanting a journal *is* earning the directory.
        entity = relayout(project.config, project.ids, entity, "directory")
    if entity.home is None:
        raise BadInput(
            f"a {entity.type.name} is stored as a {entity.layout}, which has "
            f"nowhere to keep {artifact}.",
            fix=f"set entity_types.{entity.type.name}.layout to directory",
        )
    message = params.message.strip()
    if not message:
        raise BadInput(
            f"log: nothing to write.",
            fix=f'szsdlc log {entity.id.text} "what happened"',
        )
    _journal.append_journal(entity, artifact, message)
    _views.redraw(project)
    return LogResult(entity.id.text, _relative(project, entity.artifact_path(artifact)))


# ------------------------------------------------------ schedule / unschedule

@dataclass(frozen=True)
class ScheduleParams(Data):
    ref: str
    horizon: str
    after: str | None = None
    before: str | None = None
    top: bool = False
    roadmap: str | None = None
    expect: str | None = None


@dataclass(frozen=True)
class ScheduleResult(Data):
    """Where the entity landed, and what it landed between."""

    entity: str
    roadmap: str
    horizon: str
    index: int
    after: str | None = None
    before: str | None = None


@dataclass(frozen=True)
class UnscheduleParams(Data):
    ref: str
    roadmap: str | None = None
    expect: str | None = None


@dataclass(frozen=True)
class UnscheduleResult(Data):
    id: str
    roadmap: str


@operation("schedule", params=ScheduleParams, result=ScheduleResult, kind="mutation")
def schedule(params: ScheduleParams, project: Project) -> ScheduleResult:
    from ..roadmap import Scheduler

    scheduler = Scheduler(project.config, project.store, params.roadmap, project.ids)
    if params.expect is not None:
        _entity(project, params.ref, params.expect)
    placement = scheduler.schedule(params.ref, params.horizon, after=params.after,
                                   before=params.before, top=params.top)
    _views.redraw(project)
    return ScheduleResult(placement.entity, placement.roadmap, placement.horizon,
                          placement.index, placement.after, placement.before)


@operation("unschedule", params=UnscheduleParams, result=UnscheduleResult, kind="mutation")
def unschedule(params: UnscheduleParams, project: Project) -> UnscheduleResult:
    from ..roadmap import Scheduler

    scheduler = Scheduler(project.config, project.store, params.roadmap, project.ids)
    entity_id = project.ids.resolve(params.ref)
    if params.expect is not None:
        _entity(project, params.ref, params.expect)
    if not scheduler.unschedule(entity_id):
        raise BadInput(
            f"{entity_id.text} is not on roadmap {scheduler.roadmap.name!r}.",
            fix=f"szsdlc schedule {entity_id.text} --horizon "
                f"{scheduler.roadmap.spec.horizons[-1]}",
        )
    _views.redraw(project)
    return UnscheduleResult(entity_id.text, scheduler.roadmap.name)


# ------------------------------------------------------------------- refine

@dataclass(frozen=True)
class RefineParams(Data):
    ref: str
    into: str
    title: str | None = None
    expect: str | None = None


@dataclass(frozen=True)
class RefineResult(Data):
    #: The new entity.
    id: str
    #: The idea it came from, and that idea's status before and after.
    source: str
    before: str | None
    after: str | None


@operation("refine", params=RefineParams, result=RefineResult, kind="mutation")
def refine(params: RefineParams, project: Project) -> RefineResult:
    idea = _entity(project, params.ref, params.expect)
    if not idea.type.intake:
        raise BadInput(
            f"{idea.id.text} is a {idea.type.name}, not an intake entity, so it "
            f"cannot be refined.",
            # Clipped: the title may be a whole captured paragraph, and a
            # refusal that runs to 200 characters has stopped being a fix.
            fix=f"szsdlc new {params.into} --title \"{clip(idea.title)}\"",
        )
    target = project.config.type_for(params.into)
    if target.intake:
        typed = next((t.name for t in project.config.entity_types.values()
                      if not t.intake), "work_item")
        raise BadInput(
            f"{target.name} is itself an intake type, so refining into it would "
            f"only make another idea.",
            fix=f"szsdlc refine {idea.id.text} --into {typed}",
        )
    workflow = idea.type.workflow
    if idea.status == workflow.abandoned_status:
        # Dropping is somebody deciding this goes nowhere. Reviving it is a
        # deliberate act, not a side effect of refining.
        raise BadInput(
            f"{idea.id.text} is {idea.status}; a dropped idea is not refined further.",
            fix=f"szsdlc set {idea.id.text} status={workflow.initial}",
        )

    relation = _refined_from_relation(project, idea, target)
    child = create_entity(
        project.config, project.ids, target,
        title=params.title or idea.title,
        relations={relation: idea.id.text} if relation else None,
    )

    # One idea routinely yields several entities, so this is deliberately not a
    # one-shot: refining again from an already-refined idea adds another child.
    # The idea itself is provenance and stays exactly where it is.
    before = idea.status
    after = workflow.completed_status
    if after and before != after:
        idea.set_status(after)
        idea.save()
    _views.redraw(project)
    return RefineResult(child.id.text, idea.id.text, before, idea.status)


def _refined_from_relation(project: Project, idea: Entity, target) -> str | None:
    """The relation the child uses to point back at its idea.

    Found by shape rather than by name: exactly one relation may be authored by
    the target type and points at the intake type. Hardcoding `refined_from`
    would make provenance a framework concept rather than a configured one.
    """
    candidates = [
        relation for relation in project.config.relations_from(target.name)
        if idea.type.name in relation.target_types and relation.is_single
    ]
    if not candidates:
        raise BadInput(
            f"no relation lets a {target.name} point at a {idea.type.name}, so the "
            f"idea could not be recorded as its provenance.",
            fix="declare one under relations: in .szsdlc/config.yml",
        )
    return candidates[0].name


# --------------------------------------------------------------------- drop

@dataclass(frozen=True)
class DropParams(Data):
    ref: str
    reason: str
    expect: str | None = None


@dataclass(frozen=True)
class DropResult(Data):
    id: str
    before: str | None
    after: str
    notes: tuple[str, ...] = ()


@operation("drop", params=DropParams, result=DropResult, kind="mutation")
def drop(params: DropParams, project: Project) -> DropResult:
    from .. import workflow as workflow_module

    entity = _entity(project, params.ref, params.expect)
    reason = (params.reason or "").strip()
    if not reason:
        raise BadInput(
            f"{entity.id.text}: dropping needs a reason, so the same thought "
            f"arriving twice is recognisable.",
            fix=f'szsdlc drop {entity.id.text} --reason "already covered by WI-0001"',
        )
    status = entity.type.workflow.abandoned_status
    if status is None:
        terminal = ", ".join(entity.type.workflow.terminal_states) or "none"
        raise BadInput(
            f"{entity.type.name} declares no abandoned status, so there is "
            f"nowhere to drop it to.",
            fix=f"mark one terminal state of {entity.type.name} `abandoned: true`",
            see=f"terminal statuses: {terminal}",
        )
    before = entity.status
    if before == status:
        raise BadInput(
            f"{entity.id.text} is already {status}.",
            fix=f"szsdlc show {entity.id.text}",
        )

    # Through the same checked move as `set`: the workflow's edges and the
    # target's gates apply, and so do the effects of reaching a terminal state.
    was_terminal = entity.is_terminal
    workflow_module.move(entity, status)
    if "dropped_reason" in entity.type.fields:
        entity.set_field("dropped_reason", reason)
    notes = _status.after_status_change(project, entity, was_terminal)
    entity.save()
    _views.redraw(project)
    return DropResult(entity.id.text, before, status, tuple(notes))
