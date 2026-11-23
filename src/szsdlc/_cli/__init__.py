"""Argument parsing and dispatch.

Every command here is consumed by an agent, so both halves cost tokens: a
hard-to-produce input causes retry loops, and a verbose output pollutes context
for the rest of the session. The command surface contract (REQ-0012 in
szsdlc-szsdlc) governs the whole
surface, and three of its rules are enforced right here rather than per command:

**C2/C7** — no traceback ever reaches stderr, argparse usage dumps are
suppressed, and every refusal renders as at most three lines ending in
something the caller can run.

**C5** — human output is the compact default; `--json` is opt-in, because JSON
is the more verbose encoding and must never be what an agent falls into.

**C6** — no listing is unbounded and no truncation is silent.

One module per command; shared helpers live in the `_` modules. The public
surface is `szsdlc.cli`.
"""

from __future__ import annotations

import argparse
import contextlib
import re
import shlex
import sys
from collections.abc import Sequence
from typing import Callable

from .. import __version__
from ..errors import EXIT_ERROR, InternalError, SzsdlcError
from ..ops._views import deferred as deferred_redraw
from . import _sync
from ._output import out
from ._parser import Parser, compact_help
from . import (
    capture,
    refine,
    inbox,
    drop,
    init,
    new,
    set_,
    tag,
    link,
    convert,
    layout,
    log,
    schedule,
    next_,
    show,
    context,
    list_,
    trace,
    standards,
    render,
    sync,
    refresh,
    validate,
    backend,
    hook,
)

#: Registration order is argparse's order, exactly as it was in one file. The
#: modules for `set`, `next` and `list` end in an underscore: a submodule is
#: bound in this namespace on import, and would shadow the builtin.
COMMAND_MODULES = (
    capture,
    refine,
    inbox,
    drop,
    init,
    new,
    set_,
    tag,
    link,
    convert,
    layout,
    log,
    schedule,
    next_,
    show,
    context,
    list_,
    trace,
    standards,
    render,
    sync,
    refresh,
    validate,
    backend,
    hook,
)


def build_parser() -> Parser:
    parser = Parser(prog="szsdlc", add_help=False)
    parser.format_help = compact_help  # type: ignore[assignment]
    parser.add_argument("-h", "--help", action="store_true", dest="show_help")
    parser.add_argument("-V", "--version", action="store_true", dest="show_version")
    parser.add_argument("-C", "--project", metavar="PATH", default=None)

    subparsers = parser.add_subparsers(dest="command", parser_class=Parser)

    for module in COMMAND_MODULES:
        module.add_parser(subparsers)

    return parser


#: A fix the caller fills in before running it: `szsdlc set WI-0001 title=<value>`.
PLACEHOLDER = re.compile(r"<[^>]+>")


def parse_call(text: str) -> tuple[str, dict] | None:
    """A `Fix:` command as the operation it runs: `(op, params)`, or None.

    None when `text` is not an szsdlc command, holds a placeholder, does not
    parse, or names a command that is not an operation (`hook`). This is how
    a refusal's fix becomes structured (`szsdlc.ops.fix_call`): the CLI's
    parser is the one place the command grammar lives.
    """
    if not text.startswith("szsdlc ") or PLACEHOLDER.search(text):
        return None
    try:
        words = shlex.split(text)
        args = build_parser().parse_args(words[1:])
    except (SzsdlcError, SystemExit, ValueError):
        return None
    to_params = getattr(args, "to_params", None)
    if args.command is None or to_params is None:
        return None
    return args.op, to_params(args).to_dict()


def run(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))

    if args.show_version:
        out(__version__)
        return 0
    if args.show_help:
        out(compact_help())
        return 0
    if not args.command:
        # Diagnostics go to stderr, never stdout, even when they are just help.
        print(compact_help(), file=sys.stderr)
        return 2

    runner: Callable[[argparse.Namespace], int] = args.run
    command = args.command

    if command in _sync.BACKEND_EXEMPT:
        return runner(args)

    from .. import backend as backend_module

    # Outside a turn, the sync below renders before it records anything, so
    # the redraw a mutation ends with would be the same render twice.
    terminal = command != "render" and not backend_module.in_claude_turn()
    with (deferred_redraw() if terminal else contextlib.nullcontext()):
        status = runner(args)
    try:
        if command == "render":
            # `render` is local by definition. Syncing what it wrote would
            # make the one command documented not to reach anywhere reach
            # somewhere; the next sync sweeps the rendered files in.
            report = None
        elif backend_module.in_claude_turn():
            # Nothing is recorded here. The turn-end hook renders, syncs
            # once and validates; a commit per command made a turn's history a
            # list of the tool calls that produced it rather than of what it
            # changed. Nothing is at risk: `has_local_changes` reads the store,
            # so the next sync picks up whatever is sitting there.
            report = None
        else:
            # A terminal command has no turn end to wait for, so this is its
            # boundary — and it renders first for the same reason the turn-end
            # hook does: nothing records or sends a stale view. True of every
            # backend, `workdir` included, where the sync is a no-op and
            # the render is the whole of it.
            report = _sync.sync_project(args, args.command, only_if_changed=True)
    except SzsdlcError as exc:  # pragma: no cover - settings went bad mid-command
        print(exc.render(), file=sys.stderr)
    except OSError as exc:
        _sync.warn_backend(exc)
    else:
        backend_module.warn(report)
    return status


def main(argv: Sequence[str] | None = None) -> int:
    """The single place an exception becomes an exit code.

    C7: exit codes are distinct per class so a caller can branch without
    parsing text, and no internal exception escapes as a traceback.
    """
    _sync.force_utf8_streams()
    try:
        return run(argv)
    except SzsdlcError as exc:
        print(exc.render(), file=sys.stderr)
        return exc.exit_code
    except SystemExit as exc:  # --help
        return int(exc.code or 0)
    except InternalError as exc:
        # A broken model invariant is a szsdlc bug, never a project finding.
        print(f"internal error: {exc}", file=sys.stderr)
        print("Fix: report this with the command you ran", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:  # pragma: no cover
        return 130
    except Exception as exc:  # noqa: BLE001 - C7: nothing escapes as a traceback
        print(f"internal error: {type(exc).__name__}: {exc}", file=sys.stderr)
        print("Fix: report this with the command you ran", file=sys.stderr)
        return EXIT_ERROR
