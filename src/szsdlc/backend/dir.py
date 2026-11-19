"""The `dir` backend: the project lives in another local directory.

    SZSDLC_BACKEND=dir
    SZSDLC_BACKEND_DIR_PATH=~/trackers/{project}

The szsdlc files are written straight into that directory instead of the
working tree. There is no copy and nothing to sync: the directory *is* the
store. Useful on its own — one tracking directory for many repos, or keeping
tracking files out of a repo without running a server.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from ..errors import ConfigError
from ..settings import Setting
from . import Context, SyncReport, path_setting, required


class DirBackend:
    type: ClassVar[str] = "dir"
    keys: ClassVar[frozenset[str]] = frozenset({"PATH"})

    def __init__(self, path: Path) -> None:
        self.store = path
        self.root = path

    @classmethod
    def from_settings(cls, found: dict[str, Setting], context: Context) -> DirBackend:
        return cls(path_setting(required(found, cls.type, "PATH"), context))

    @classmethod
    def restore(cls, store: Path, state: dict[str, Any]) -> DirBackend:
        return cls(store)

    def state(self) -> dict[str, Any]:
        return {}

    def describe(self) -> list[tuple[str, str, tuple[str, ...]]]:
        return [("path", str(self.store), ("PATH",))]

    def summary(self) -> str:
        return "kept outside the working directory"

    def materialize(self) -> None:
        if self.store.exists() and not self.store.is_dir():
            raise ConfigError(f"{self.store} exists and is not a directory.",
                              fix="point SZSDLC_BACKEND_DIR_PATH at a directory")
        self.store.mkdir(parents=True, exist_ok=True)

    def has_local_changes(self) -> bool:
        return False

    def sync(self, reason: str) -> SyncReport:
        return SyncReport()
