"""szsdlc refine: spawn a typed entity from an idea."""

from __future__ import annotations

import argparse

from ..errors import BadInput
from ..model import Entity, create_entity
from ..text import clip
from . import _session
from ._output import out


def add_parser(subparsers) -> None:
    refine = subparsers.add_parser("refine", prog="szsdlc refine")
    refine.add_argument("ref")
    refine.add_argument("--into", dest="into", required=True)
    refine.add_argument("--title", dest="title", default=None)
    refine.set_defaults(run=run)


def run(args: argparse.Namespace) -> int:
    session = _session.Session(args.project)
    idea = session.entity(args.ref)

    if not idea.type.intake:
        raise BadInput(
            f"{idea.id.text} is a {idea.type.name}, not an intake entity, so it "
            f"cannot be refined.",
            # Clipped: the title may be a whole captured paragraph, and a
            # refusal that runs to 200 characters has stopped being a fix.
            fix=f"szsdlc new {args.into} --title \"{clip(idea.title)}\"",
        )

    target = session.config.type_for(args.into)
    if target.intake:
        typed = next((t.name for t in session.config.entity_types.values()
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

    relation = _refined_from_relation(session, idea, target)
    child = create_entity(
        session.config, session.ids, target,
        title=args.title or idea.title,
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

    # C1 — the resulting state, so no confirming `show` is ever needed.
    out(f"{child.id.text}  ({idea.id.text}: {before} → {idea.status})")
    return 0


def _refined_from_relation(session: _session.Session, idea: Entity, target) -> str | None:
    """The relation the child uses to point back at its idea.

    Found by shape rather than by name: exactly one relation may be authored by
    the target type and points at the intake type. Hardcoding `refined_from`
    would make provenance a framework concept rather than a configured one.
    """
    candidates = [
        relation for relation in session.config.relations_from(target.name)
        if idea.type.name in relation.target_types and relation.is_single
    ]
    if not candidates:
        raise BadInput(
            f"no relation lets a {target.name} point at a {idea.type.name}, so the "
            f"idea could not be recorded as its provenance.",
            fix="declare one under relations: in .szsdlc/config.yml",
        )
    return candidates[0].name
