"""szsdlc backend: where the project is kept, and why. Inspection only."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ._registry import operation
from ._types import Data


@dataclass(frozen=True)
class BackendParams(Data):
    pass


@dataclass(frozen=True)
class BackendResult(Data):
    type: str
    #: Where the choice came from: a settings source, "default", or that the
    #: path is inside the backend's own store.
    source: str
    inside: bool
    #: (label, value) for each setting in effect, and the root.
    rows: tuple[tuple[str, str], ...]


def _printable(text: str) -> str:
    # Every value here came from a settings file someone else may have
    # written; none of it may reach a terminal as a control sequence.
    return re.sub(r"[\x00-\x1f\x7f-\x9f]", "?", text)


@operation("backend", params=BackendParams, result=BackendResult, kind="query", loads=False)
def backend(params: BackendParams, where: str | None) -> BackendResult:
    """Where the project is kept, and why. Inspection only."""
    from .. import backend as backend_module

    selection = backend_module.select(where)
    # Asked first, not as a fallback: standing inside a store is a fact about
    # where you are.
    inside = backend_module.managed(where or Path.cwd())
    if inside is not None:
        current = inside
        source = "this directory is inside its store"
        rows = [(label, value) for label, value, _ in current.describe()]
    else:
        current = backend_module.resolve(where)
        source = selection.source or "default"
        found = selection.found
        rows = [(label, f"{value}  ({next((found[k].source for k in keys if k in found), 'default')})")
                for label, value, keys in current.describe()]
    if current.root is not None:
        rows.append(("root", str(current.root)
                     + ("" if current.store.exists() else "  (not created yet)")))
    return BackendResult(_printable(current.type), _printable(source), inside is not None,
                         tuple((_printable(label), _printable(value)) for label, value in rows))
