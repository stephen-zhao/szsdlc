"""Writing results: lines, JSON, bounded listings, paths."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ..model import Entity
from . import _session


def out(line: str = "") -> None:
    print(line)


def emit_json(payload: Any) -> None:
    print(json.dumps(payload, indent=None, separators=(",", ":"), default=str))


def truncate(rows: list[Any], limit: int) -> tuple[list[Any], str | None]:
    """C6 — bound the output and say so. A silently capped list reads as
    "nothing more to see", which is the one thing it must never mean."""
    if limit <= 0 or len(rows) <= limit:
        return rows, None
    return rows[:limit], f"showing {limit} of {len(rows)} — rerun with --limit 0"


def id_width(rows: Sequence[Entity]) -> int:
    """Ids are `<PREFIX>-<NNNN>` and prefixes differ in length, so a listing
    that mixes types has a ragged left edge unless the column is measured.
    Padded per call rather than to a constant, so a single-type listing does
    not pay for the longest prefix the project happens to declare."""
    return max((len(entity.id.text) for entity in rows), default=0)


def relative(session: _session.Session, path: Path) -> str:
    try:
        return str(path.relative_to(session.config.root)).replace("\\", "/")
    except ValueError:  # pragma: no cover - paths are always under the root
        return str(path)


def display_path(session: _session.Session, entity: Entity) -> str:
    where = entity.home or entity.path
    try:
        return str(where.relative_to(session.config.root).as_posix())
    except ValueError:  # pragma: no cover - a project outside its own root
        return str(where)
