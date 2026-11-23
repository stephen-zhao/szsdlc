"""The sync that follows a command, and the streams it writes to."""

from __future__ import annotations

import argparse
import sys

from ..errors import SzsdlcError
from . import _session


def sync_project(args: argparse.Namespace, reason: str, *,
                  only_if_changed: bool = False,
                  should_render: bool = True):
    """Render, then sync — through the one composite every caller uses.

    Loading the project can fail for reasons the command itself will report
    properly, and a backend can fail on the filesystem; neither may turn a
    command that worked into one that did not.
    """
    from .. import sync as sync_module

    try:
        session = _session.Session(args.project)
    except SzsdlcError:
        return None  # the command itself will say it properly
    try:
        return sync_module.run(session, reason, only_if_changed=only_if_changed,
                               should_render=should_render)
    except OSError as exc:
        warn_backend(exc)
        return None


def warn_backend(exc: OSError) -> None:
    """A backend that failed on the filesystem never fails a command that worked."""
    print(f"szsdlc sync: {exc}; changes are kept locally.\n"
          "Fix: szsdlc sync", file=sys.stderr)


#: Commands that sync for themselves, or not at all: `hook` because each
#: handler decides when, `backend` because it only inspects — wrapping the
#: inspector in the operation it reports on is how the two got confused — and
#: `refresh` because its whole promise is that nothing is sent.
BACKEND_EXEMPT = {"hook", "backend", "refresh"}


def force_utf8_streams() -> None:
    """Make output encodable everywhere before anything tries to write.

    On Windows the console encoding is the ANSI codepage, not UTF-8, so a
    single `->` arrow in a transition line raises UnicodeEncodeError from
    inside `print` -- after the mutation has already been written to disk.
    That reports failure for work that succeeded, which is the worst shape a
    failure can take. Python 3.15 defaults to UTF-8 mode; until then, ask.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:  # a pipe someone replaced; nothing to do
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):  # pragma: no cover - already detached
            pass
