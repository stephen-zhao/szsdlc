"""Bounds every listing shares (C6): a default limit, and an honest total."""

from __future__ import annotations

from typing import Any

#: Every listing is bounded, and the bound is the same everywhere.
DEFAULT_LIMIT = 20
#: `next` is tighter still: it answers "what now", and twenty things is not an answer.
NEXT_LIMIT = 10
STANDARDS_LIMIT = 10


def bounded(rows: list[Any], limit: int) -> tuple[tuple[Any, ...], int]:
    """The first `limit` rows (all of them for 0) and how many there were."""
    shown = rows if limit <= 0 else rows[:limit]
    return tuple(shown), len(rows)
