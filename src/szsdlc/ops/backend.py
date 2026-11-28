"""szsdlc backend: the sdlc backend in effect, its project sdlc store dir and root. Inspection only."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ..errors import ConfigError
from ._registry import operation
from ._types import Data


@dataclass(frozen=True)
class BackendParams(Data):
    pass


@dataclass(frozen=True)
class BackendResult(Data):
    type: str
    #: Where the choice came from: a settings source, "default", or that the
    #: path is inside a recorded project sdlc store dir.
    source: str
    inside: bool
    #: (label, value) for each setting in effect, and the project sdlc root.
    rows: tuple[tuple[str, str], ...]
    #: What in the record of project sdlc stores no longer holds (WI-0067);
    #: `szsdlc backend prune` removes it.
    stale: tuple[str, ...] = ()


def _printable(text: str) -> str:
    # Every value here came from a settings file someone else may have
    # written; none of it may reach a terminal as a control sequence.
    return re.sub(r"[\x00-\x1f\x7f-\x9f]", "?", text)


@operation("backend", params=BackendParams, result=BackendResult, kind="query", loads=False)
def backend(params: BackendParams, where: str | None) -> BackendResult:
    """The sdlc backend in effect, and why. Inspection only."""
    from .. import backend as backend_module

    selection = backend_module.select(where)
    # Asked first, not as a fallback: standing inside a project sdlc store
    # dir kept away from its asset roots is a fact about where you are.
    # Anywhere else (a `workdir` project, an asset root) the settings answer.
    inside = backend_module._record_decides(where or Path.cwd())
    if inside is not None:
        current = inside
        source = "this directory is inside its project sdlc store dir"
        rows = [(label, value) for label, value, _ in current.describe()]
    else:
        current = backend_module.resolve(where)
        source = selection.source or "default"
        found = selection.found
        rows = [(label, f"{value}  ({next((found[k].source for k in keys if k in found), 'default')})")
                for label, value, keys in current.describe()]
    if current.project_sdlc_root is not None:
        rows.append(("project sdlc root", str(current.project_sdlc_root)
                     + ("" if current.project_sdlc_store_dir.exists() else "  (not created yet)")))
    try:
        backend_module._read_record(strict=True)
    except ConfigError as exc:  # recording skips it rather than block work; here it is said
        rows.append(("record", f"{exc.problem} Fix: {exc.fix}"))
    stale = tuple(_printable(problem) for _, _, problem in backend_module.stale_project_sdlc_stores())
    if stale:
        rows.append(("stale records", f"{len(stale)} (szsdlc backend prune)"))
    return BackendResult(_printable(current.type), _printable(source), inside is not None,
                         tuple((_printable(label), _printable(value)) for label, value in rows), stale)


@dataclass(frozen=True)
class BackendPruneParams(Data):
    pass


@dataclass(frozen=True)
class BackendPruneResult(Data):
    #: One line per entry removed from the record of project sdlc stores.
    removed: tuple[str, ...]


@operation("backend_prune", params=BackendPruneParams, result=BackendPruneResult, kind="mutation",
           loads=False)
def backend_prune(params: BackendPruneParams, where: str | None) -> BackendPruneResult:
    """Remove what no longer holds from the record of project sdlc stores (WI-0067).

    Only on request, never silently: a drive that is not mounted must not lose
    its entry. Removes stores whose project sdlc root or dir is gone, asset
    roots that are gone, and lists and links that disagree.
    """
    from .. import backend as backend_module

    return BackendPruneResult(tuple(_printable(line) for line in backend_module.prune_project_sdlc_stores()))


@dataclass(frozen=True)
class ProjectSdlcStoresParams(Data):
    pass


@dataclass(frozen=True)
class ProjectSdlcStoresResult(Data):
    #: Every recorded project sdlc store, sorted by project sdlc root
    #: (`szsdlc.backend.ProjectSdlcStore`).
    stores: tuple


@operation("project_sdlc_stores", params=ProjectSdlcStoresParams, result=ProjectSdlcStoresResult,
           kind="query", loads=False)
def project_sdlc_stores(params: ProjectSdlcStoresParams, where: str | None) -> ProjectSdlcStoresResult:
    """Every project sdlc store szsdlc has recorded, whatever its backend type (WI-0063). Reads only."""
    from .. import backend as backend_module

    return ProjectSdlcStoresResult(tuple(backend_module.project_sdlc_stores()))
