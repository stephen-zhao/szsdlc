"""The parser, the help table and the shared listing bounds."""

from __future__ import annotations

import argparse
from typing import Any

from ..errors import BadInput
#: C6 — every listing is bounded; the bounds are the operations' own defaults.
from ..ops._listing import DEFAULT_LIMIT, NEXT_LIMIT, STANDARDS_LIMIT  # noqa: F401


class Parser(argparse.ArgumentParser):
    """An argparse parser that never prints a usage dump.

    A 30-line usage block is the classic way a single typo costs a thousand
    tokens, so a parse error becomes an ordinary three-line refusal instead.
    """

    def error(self, message: str) -> Any:  # type: ignore[override]
        raise BadInput(f"{self.prog}: {message}.", fix=f"{self.prog} --help")

    def exit(self, status: int = 0, message: str | None = None) -> Any:  # type: ignore[override]
        if status:
            raise BadInput(f"{self.prog}: {(message or 'bad arguments').strip()}",
                           fix=f"{self.prog} --help")
        raise SystemExit(0)


#: (invocation, one-line blurb). Paired verbs share a row, as the plan's own
#: audit table does — `tag`/`untag` are one idea, and spending two lines of a
#: 25-line budget saying so twice is exactly the waste this budget exists for.
COMMANDS: list[tuple[str, str]] = [
    ("init", "scaffold a project: config, directories, an empty roadmap"),
    ("capture [text]", "capture an idea from an argument, stdin or $EDITOR"),
    ("refine <ref> --into T", "spawn a typed entity from an idea"),
    ("inbox", "unrefined ideas, oldest first"),
    ("drop <ref> --reason ..", "close an idea that went nowhere, with a reason"),
    ("new <TYPE> --title ..", "create a typed entity directly"),
    ("set <ref> field=value", "set scalar fields; enforces the workflow"),
    ("tag|untag <ref> <tag>", "add or remove tags, normalized on write"),
    ("link|unlink a <r> b", "author or remove one edge; inverses are generated"),
    ("convert <ref> <TYPE>", "reclassify, leaving a resolving tombstone"),
    ("layout <ref> <LAYOUT>", "lay an entry out as a section, file or directory"),
    ("log <ref> [msg]", "append a dated line to the journal artifact"),
    ("schedule <ref> -H h", "place on a roadmap; --after/--before/--top"),
    ("unschedule <ref>", "take off the roadmap"),
    ("next", "actionable work, in roadmap order, unblocked"),
    ("show <ref> [--context]", "one entity, or a budgeted context bundle"),
    ("context", "in-flight work and the counters block"),
    ("list [filters]", "by type, status, tag, parent, coverage, placement"),
    ("trace <ref> [--depth]", "relations both ways, back to the idea"),
    ("standards match <path>", "conventions governing these paths"),
    ("render", "regenerate every view and record; silent, never validates"),
    ("validate", "every consistency rule; silent when clean"),
    ("sync", "render, then put the project in step with where it is kept"),
    ("refresh [--force]", "make the latest where it is kept current; sends nothing"),
    ("backend", "where the project is kept, and why"),
]


#: Registered but not listed: invoked by hooks.json, never typed by anyone.
#: Spending a line of the help budget on it would cost tokens in every
#: session to document something no reader can use.
HIDDEN_COMMANDS = {"hook"}


def compact_help() -> str:
    """One line per command; deep help lives behind `<command> --help`.

    A 30-line usage block is the classic way a single typo costs a thousand
    tokens, and this is the block a mistyped command would otherwise print.
    """
    width = max(len(invocation) for invocation, _ in COMMANDS)
    lines = ["usage: szsdlc [-C PATH] <command> [options]", ""]
    lines += [f"  {invocation:<{width}}  {blurb}" for invocation, blurb in COMMANDS]
    lines += ["", "szsdlc <command> --help for details."]
    return "\n".join(lines)
