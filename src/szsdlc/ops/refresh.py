"""szsdlc refresh: take the latest from where the project is kept; send nothing."""

from __future__ import annotations

from dataclasses import dataclass

from ..errors import BadInput, Refused, SzsdlcError
from ._project import Project
from ._registry import operation
from ._types import Data


@dataclass(frozen=True)
class RefreshParams(Data):
    #: Replace local state that differs from the latest. Without it, that
    #: case refuses: it is the one that can lose something.
    force: bool = False


@dataclass(frozen=True)
class RefreshResult(Data):
    incoming: bool
    replaced: bool
    #: The backend's own detail, such as where replaced state was kept.
    note: str | None = None


@operation("refresh", params=RefreshParams, result=RefreshResult, kind="mutation", loads=False)
def refresh(params: RefreshParams, where: str | None) -> RefreshResult:
    """Make the latest state where the project is kept the current one.

    Sends nothing and integrates nothing. Renders afterwards, as `sync`
    does, so views match what arrived — the only local write.
    """
    from .. import backend as backend_module
    from .. import sync as sync_module

    backend_module.project_root(where)  # make the store ready, as any command does
    backend = backend_module.locate(where)
    if backend is None:  # pragma: no cover - project_root just made it
        raise BadInput("refresh: no project here.", fix="szsdlc init")
    if not params.force and backend.has_local_changes():
        raise Refused(
            "refresh: local changes differ from where the project is kept, and "
            "refreshing would replace them.",
            fix="szsdlc refresh --force",
            see="szsdlc sync sends them instead",
        )

    report = backend_module.refresh(backend)
    if not report.ok:
        raise Refused(f"refresh: {report.problem}", fix=report.fix or "szsdlc backend")
    try:
        sync_module.render(Project(where))
    except SzsdlcError:
        pass  # a project that will not load after refreshing is validate's to report
    return RefreshResult(bool(report.incoming), bool(report.replaced), report.note)
