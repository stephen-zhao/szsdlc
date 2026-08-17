"""Command-line entry point.

Deliberately minimal for now: the scaffold declares a console script, so that
script must resolve to something real. Command dispatch arrives in Task 6
onward, under the contract in docs/plan.md — mutations report their resulting
state, refusals name a runnable fix, and hook-invoked commands are silent on
success.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence

from . import __version__


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)

    if args and args[0] in {"--version", "-V"}:
        print(__version__)
        return 0

    print(f"szsdlc {__version__} — no commands implemented yet", file=sys.stderr)
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
