"""The `workdir` sdlc backend: the project sdlc store dir is the asset root.

    SZSDLC_BACKEND=workdir        (the default; no settings of its own)

This is what szsdlc does when nobody configures anything, and it is a backend
like the others rather than the absence of one. Before, `workdir` was a string
the rest of the package compared against, and "there is no backend" was a case
every caller had to carry — a dozen `is None` branches, and three return types
that were optional only to encode it. An abstraction with a hole in it for one
implementation is not an abstraction.

It *is* the `dir` backend with the directory already decided: the project sdlc
store dir is a plain local directory, and there is nothing to sync because no
copy exists. Everything below is the difference between "a directory someone
named" and "the directory we are standing in":

- no settings. `dir` requires `SZSDLC_BACKEND_DIR_PATH`; this one is found by
  the same upward walk for `.szsdlc/config.yml` that discovery has always done.
- nothing is created. `dir` makes its directory on demand, because naming a
  directory that does not exist yet is a reasonable thing to do. Here, a
  missing project is not a directory to create — it is `szsdlc init`'s job, and
  guessing would plant a project wherever a command happened to run.
- no recorded entry (yet: WI-0067 records every store). A project sdlc store
  dir outside the asset root has to be recorded to be found again; this one is
  found by walking up.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from ..settings import Setting
from . import Context
from .dir import DirBackend


class WorkdirBackend(DirBackend):
    type: ClassVar[str] = "workdir"
    keys: ClassVar[frozenset[str]] = frozenset()
    #: The project sdlc store dir is the asset root, so nothing has to remember
    #: where it is: walking up from any path inside it finds the project sdlc
    #: root, as it always did.
    relocates: ClassVar[bool] = False

    def __init__(self, directory: Path, root: Path | None = None) -> None:
        super().__init__(directory)
        #: The project sdlc root, or None when there is no project here.
        #: `project_sdlc_root()` passes that None on, so "no szsdlc project in
        #: this directory" keeps being reported by discovery, in its own words,
        #: rather than as a backend that is pointing somewhere empty.
        self.project_sdlc_root = root

    @classmethod
    def from_settings(cls, found: dict[str, Setting], context: Context) -> WorkdirBackend:
        # Local import: `config` reaches the backend to ask where the project
        # is, so the backend cannot import it at module scope.
        from ..config import find_project_sdlc_root

        directory = context.project_asset_root or Path.cwd()
        return cls(directory, find_project_sdlc_root(directory))

    @classmethod
    def restore(cls, store: Path, state: dict[str, Any]) -> WorkdirBackend:
        from ..config import find_project_sdlc_root

        return cls(store, find_project_sdlc_root(store))

    def describe(self) -> list[tuple[str, str, tuple[str, ...]]]:
        """Nothing. There are no settings, and the path is where you are."""
        return []

    def summary(self) -> str:
        return "in the asset root"

    def materialize(self) -> None:
        """Nothing to make. The asset root is there, or there is no project yet."""
