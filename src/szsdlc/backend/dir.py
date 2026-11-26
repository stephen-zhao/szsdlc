"""The `dir` sdlc backend: the project sdlc store dir is another local directory.

    SZSDLC_BACKEND=dir
    SZSDLC_BACKEND_DIR_PATH=~/trackers/{project}

The szsdlc files are written straight into that directory instead of the
asset root. There is no copy and nothing to sync: the directory is the project
sdlc store dir and the project sdlc root. Useful on its own — one tracking
directory for many repos, or keeping tracking files out of a repo without
running a server.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from ..errors import ConfigError
from ..settings import Setting
from . import Context, RetiredNames, SyncReport, path_setting, required


class DirBackend(RetiredNames):
    type: ClassVar[str] = "dir"
    relocates: ClassVar[bool] = True
    keys: ClassVar[frozenset[str]] = frozenset({"PATH"})

    def __init__(self, path: Path) -> None:
        self.project_sdlc_store_dir = path
        self.project_sdlc_root = path

    @classmethod
    def from_settings(cls, found: dict[str, Setting], context: Context) -> DirBackend:
        return cls(path_setting(required(found, cls.type, "PATH"), context))

    @classmethod
    def restore(cls, store: Path, state: dict[str, Any]) -> DirBackend:
        return cls(store)

    def state(self) -> dict[str, Any]:
        return {}

    def describe(self) -> list[tuple[str, str, tuple[str, ...]]]:
        return [("project sdlc store dir", str(self.project_sdlc_store_dir), ("PATH",))]

    def summary(self) -> str:
        return "in a project sdlc store dir outside the asset root"

    def materialize(self) -> None:
        store_dir = self.project_sdlc_store_dir
        if store_dir.exists() and not store_dir.is_dir():
            raise ConfigError(f"{store_dir} exists and is not a directory.",
                              fix="point SZSDLC_BACKEND_DIR_PATH at a directory")
        store_dir.mkdir(parents=True, exist_ok=True)

    def has_unsent_changes(self) -> bool:
        return False

    def sync(self, reason: str) -> SyncReport:
        return SyncReport()

    def refresh(self) -> SyncReport:
        """Nothing: the directory is the only copy, so it is already the latest."""
        return SyncReport()
