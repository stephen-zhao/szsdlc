"""szsdlc refresh: replace local state with the sdlc backend's latest; send nothing."""

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
    """Replace local state with the sdlc backend's latest.

    Sends nothing and integrates nothing. Renders afterwards, as `sync`
    does, so views match what arrived — the only local write.
    """
    from .. import backend as backend_module
    from .. import sync as sync_module

    backend_module.project_sdlc_root(where)  # make the store dir ready, as any command does
    backend = backend_module.locate(where)
    if backend is None:  # pragma: no cover - project_root just made it
        raise BadInput("refresh: no project here.", fix="szsdlc init")
    if not params.force and backend.has_unsent_changes():
        raise Refused(
            "refresh: there are unsent changes, and refreshing would replace them.",
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
