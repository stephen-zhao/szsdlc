"""Reading free text from an argument, stdin or $EDITOR."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from ..errors import BadInput


def read_text_input(given: str | None) -> str:
    """An argument, or stdin, or `$EDITOR` — never a prompt.

    Capture cost is the whole point: a thought that has to be quoted, escaped
    or confirmed is a thought that does not get captured.
    """
    if given is not None:
        return given
    if not sys.stdin.isatty():
        return sys.stdin.read()
    return read_from_editor()


def read_from_editor() -> str:
    editor = os.environ.get("SZSDLC_EDITOR") or os.environ.get("EDITOR")
    if not editor:
        raise BadInput(
            "nothing to capture: no text argument, no stdin, and $EDITOR is unset.",
            fix='szsdlc capture "the thought"',
        )
    handle, name = tempfile.mkstemp(suffix=".md", prefix="szsdlc-capture-")
    os.close(handle)
    try:
        subprocess.run([*editor.split(), name], check=False)
        return Path(name).read_text(encoding="utf-8")
    finally:
        os.unlink(name)
