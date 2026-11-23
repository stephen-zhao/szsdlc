"""Appending dated lines to an entity's journal artifact."""

from __future__ import annotations

import datetime as dt

from ..model import Entity


def append_journal(entity: Entity, artifact: str, message: str) -> None:
    """Append dated lines to the journal: the one writer `log` and `set --force` share."""
    path = entity.artifact_path(artifact)
    assert path is not None
    stamp = dt.date.today().isoformat()
    body = "".join(f"- {stamp} {line}\n" for line in message.splitlines() if line.strip())

    existing = path.read_bytes().decode("utf-8") if path.is_file() else ""
    if existing and not existing.endswith("\n"):
        existing += "\n"
    path.write_bytes((existing + body).encode("utf-8"))
