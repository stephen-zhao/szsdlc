"""szsdlc set: set scalar fields; enforces the workflow."""

from __future__ import annotations

import argparse
import datetime as dt
from typing import Any

from ..errors import BadInput
from ..model import Entity
from ..text import nearest
from . import _session, _status
from ._output import out


def add_parser(subparsers) -> None:
    setter = subparsers.add_parser("set", prog="szsdlc set")
    setter.add_argument("ref")
    setter.add_argument("assignments", nargs="+", metavar="field=value")
    setter.add_argument("--force", action="store_true")
    setter.add_argument("--reason", default=None)
    setter.set_defaults(run=run)


#: C3 — one job per command. These have their own verbs, and composing them on
#: a command line is exactly the input that takes an agent three attempts.
DELEGATED_FIELDS = {
    "relations": "szsdlc link {ref} <relation> <ref>",
    "tags": "szsdlc tag {ref} <tag>",
    "id": "szsdlc convert {ref} <TYPE>",
}


def _coerce(session: _session.Session, entity: Entity, field: str, raw: str) -> Any:
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


def run(args: argparse.Namespace) -> int:
    from .. import workflow as workflow_module

    session = _session.Session(args.project)
    entity = session.entity(args.ref)
    settable = ["title", "status", *entity.type.fields]
    reported: list[str] = []
    reason = _status.force_reason(args, entity)

    for assignment in args.assignments:
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
            transition = workflow_module.move(entity, raw, force=args.force)
            if transition.forced:
                assert reason is not None
                _status.record_forced_move(entity, transition, reason)
                reported.append(f"status {transition.before} → {transition.after} "
                                f"(forced: {reason})")
            else:
                reported.append(f"status {transition.before} → {transition.after}")
            reported += _status.after_status_change(session, entity, was_terminal)
        else:
            before = entity.field(field)
            entity.set_field(field, _coerce(session, entity, field, raw))
            reported.append(f"{field} {before} → {entity.field(field)}")

    entity.save()
    # C1 — the resulting state, one line, so no confirming `show` is needed.
    out(f"{entity.id.text}: " + "; ".join(reported))
    return 0
