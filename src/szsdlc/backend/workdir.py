"""The `workdir` backend: the project is the working tree.

    SZSDLC_BACKEND=workdir        (the default; no settings of its own)

This is what szsdlc does when nobody configures anything, and it is a backend
like the others rather than the absence of one. Before, `workdir` was a string
the rest of the package compared against, and "there is no backend" was a case
every caller had to carry — a dozen `is None` branches, and three return types
that were optional only to encode it. An abstraction with a hole in it for one
implementation is not an abstraction.

It *is* the `dir` backend with the directory already decided: the store is a
plain local directory, and there is nothing to sync because no copy
exists. Everything below is the difference between "a directory someone named"
and "the directory we are standing in":

- no settings. `dir` requires `SZSDLC_BACKEND_DIR_PATH`; this one is found by
  the same upward walk for `.szsdlc/config.yml` that discovery has always done.
- nothing is created. `dir` makes its directory on demand, because naming a
  directory that does not exist yet is a reasonable thing to do. Here, a
  missing project is not a directory to create — it is `szsdlc init`'s job, and
  guessing would plant a project wherever a command happened to run.
- no registry entry. A store outside the working tree has to be recorded to be
  found again; this one is found by walking up. Registering it would make
  `managed()` — "is this path inside a store we keep elsewhere?" — true of
  every ordinary repository, which inverts what its callers ask it.
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
    #: The project is not moved anywhere, so nothing has to remember where it
    #: is: walking up from any path inside the tree finds it, as it always did.
    #: Recording it would make every ordinary repository a managed store.
    relocates: ClassVar[bool] = False

    def __init__(self, directory: Path, root: Path | None = None) -> None:
        super().__init__(directory)
        #: Where the project actually is, or None when there is not one here.
        #: `project_root` passes that None on, so "no szsdlc project in this
        #: directory" keeps being reported by discovery, in its own words,
        #: rather than as a backend that is pointing somewhere empty.
        self.root = root

    @classmethod
    def from_settings(cls, found: dict[str, Setting], context: Context) -> WorkdirBackend:
        # Local import: `config` reaches the backend to ask where the project
        # is, so the backend cannot import it at module scope.
        from ..config import find_project_root

        directory = context.directory or Path.cwd()
        return cls(directory, find_project_root(directory))

    @classmethod
    def restore(cls, store: Path, state: dict[str, Any]) -> WorkdirBackend:
        from ..config import find_project_root

        return cls(store, find_project_root(store))

    def describe(self) -> list[tuple[str, str, tuple[str, ...]]]:
        """Nothing. There are no settings, and the path is where you are."""
        return []

    def summary(self) -> str:
        return "in the working directory"

    def materialize(self) -> None:
        """Nothing to make. The tree is there, or there is no project yet."""
