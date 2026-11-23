"""The `szsdlc` command: the public surface of `szsdlc._cli`.

The commands live in `szsdlc._cli`, one module per command. This module keeps
the names other code imports, the `szsdlc = szsdlc.cli:main` entry point, and
`python -m szsdlc.cli`, which the hooks run.
"""

from __future__ import annotations

import sys

from ._cli import build_parser, main, run
from ._cli._parser import COMMANDS, HIDDEN_COMMANDS, compact_help

__all__ = ["COMMANDS", "HIDDEN_COMMANDS", "build_parser", "compact_help", "main", "run"]


if __name__ == "__main__":  # pragma: no cover - exercised through the hooks
    sys.exit(main())
