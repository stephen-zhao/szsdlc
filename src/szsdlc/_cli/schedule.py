"""szsdlc schedule: place on a roadmap; --after/--before/--top.
szsdlc unschedule: take off the roadmap."""

from __future__ import annotations

import argparse

from ..errors import BadInput
from . import _session
from ._output import out


def add_parser(subparsers) -> None:
    schedule = subparsers.add_parser("schedule", prog="szsdlc schedule")
    schedule.add_argument("ref")
    schedule.add_argument("--horizon", required=True)
    schedule.add_argument("--after", default=None)
    schedule.add_argument("--before", default=None)
    schedule.add_argument("--top", action="store_true")
    schedule.add_argument("--roadmap", default=None)
    schedule.set_defaults(run=run_schedule)

    unschedule = subparsers.add_parser("unschedule", prog="szsdlc unschedule")
    unschedule.add_argument("ref")
    unschedule.add_argument("--roadmap", default=None)
    unschedule.set_defaults(run=run_unschedule)


def run_schedule(args: argparse.Namespace) -> int:
    from ..roadmap import Scheduler

    session = _session.Session(args.project)
    scheduler = Scheduler(session.config, session.store, args.roadmap, session.ids)
    placement = scheduler.schedule(args.ref, args.horizon, after=args.after,
                                   before=args.before, top=args.top)
    out(str(placement))
    return 0


def run_unschedule(args: argparse.Namespace) -> int:
    from ..roadmap import Scheduler

    session = _session.Session(args.project)
    scheduler = Scheduler(session.config, session.store, args.roadmap, session.ids)
    entity_id = session.ids.resolve(args.ref)
    if not scheduler.unschedule(entity_id):
        raise BadInput(
            f"{entity_id.text} is not on roadmap {scheduler.roadmap.name!r}.",
            fix=f"szsdlc schedule {entity_id.text} --horizon "
                f"{scheduler.roadmap.spec.horizons[-1]}",
        )
    out(f"{entity_id.text}: off {scheduler.roadmap.name}")
    return 0
