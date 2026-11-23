"""Paths as an operation reports them: relative to the project, with forward slashes."""

from __future__ import annotations

from pathlib import Path

from ..model import Entity
from ._project import Project


def relative(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root)).replace("\\", "/")
    except ValueError:  # pragma: no cover - paths are always under the root
        return str(path)


def display_path(project: Project, entity: Entity) -> str:
    """Where an entity lives: its own directory when it has one, else its file."""
    where = entity.home or entity.path
    try:
        return str(where.relative_to(project.config.root).as_posix())
    except ValueError:  # pragma: no cover - a project outside its own root
        return str(where)
