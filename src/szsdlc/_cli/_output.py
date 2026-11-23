"""Writing results: lines, JSON, bounded listings."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any


def out(line: str = "") -> None:
    print(line)


def emit_json(payload: Any) -> None:
    print(json.dumps(payload, indent=None, separators=(",", ":"), default=str))


def bound_note(total: int, limit: int) -> str | None:
    """C6 — say so when a listing was cut. A silently capped list reads as
    "nothing more to see", which is the one thing it must never mean."""
    if limit <= 0 or total <= limit:
        return None
    return f"showing {limit} of {total} — rerun with --limit 0"


def id_width(rows: Sequence[Any]) -> int:
    """Ids are `<PREFIX>-<NNNN>` and prefixes differ in length, so a listing
    that mixes types has a ragged left edge unless the column is measured.
    Padded per call rather than to a constant, so a single-type listing does
    not pay for the longest prefix the project happens to declare."""
    return max((len(row.id) for row in rows), default=0)
