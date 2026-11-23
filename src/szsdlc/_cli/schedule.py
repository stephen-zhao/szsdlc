"""szsdlc schedule / unschedule: place an entity on a roadmap, or take it off."""

from __future__ import annotations

import argparse

from ..ops import Project, entities
from ..roadmap import Placement
from ._output import out


def add_parser(subparsers) -> None:
    schedule = subparsers.add_parser("schedule", prog="szsdlc schedule")
    schedule.add_argument("ref")
    schedule.add_argument("--horizon", required=True)
    schedule.add_argument("--after", default=None)
    schedule.add_argument("--before", default=None)
    schedule.add_argument("--top", action="store_true")
    schedule.add_argument("--roadmap", default=None)
    schedule.set_defaults(run=run_schedule, op="schedule", to_params=to_schedule_params)

    unschedule = subparsers.add_parser("unschedule", prog="szsdlc unschedule")
    unschedule.add_argument("ref")
    unschedule.add_argument("--roadmap", default=None)
    unschedule.set_defaults(run=run_unschedule, op="unschedule",
                            to_params=to_unschedule_params)


def to_schedule_params(args: argparse.Namespace) -> entities.ScheduleParams:
    return entities.ScheduleParams(ref=args.ref, horizon=args.horizon, after=args.after,
                                   before=args.before, top=args.top, roadmap=args.roadmap)


def to_unschedule_params(args: argparse.Namespace) -> entities.UnscheduleParams:
    return entities.UnscheduleParams(ref=args.ref, roadmap=args.roadmap)


def run_schedule(args: argparse.Namespace) -> int:
    placed = entities.schedule(to_schedule_params(args), Project(args.project))
    out(str(Placement(placed.entity, placed.roadmap, placed.horizon, placed.index,
                      placed.after, placed.before)))
    return 0


def run_unschedule(args: argparse.Namespace) -> int:
    off = entities.unschedule(to_unschedule_params(args), Project(args.project))
    out(f"{off.id}: off {off.roadmap}")
    return 0
