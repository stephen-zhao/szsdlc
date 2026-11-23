"""szsdlc sync: render, then bring the project into step with where it is kept."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass

from ..errors import Refused
from ._project import Project
from ._registry import operation
from ._types import Data
from .render import RenderParams, render


@dataclass(frozen=True)
class SyncParams(Data):
    pass


@dataclass(frozen=True)
class SyncResult(Data):
    changed: tuple[str, ...]
    #: State arrived, and the views were redrawn from it.
    incoming: bool = False
    #: Local state was sent.
    outgoing: bool = False


def _send(where: str | None, reason: str = "sync"):
    """The legs of a sync that reach the backend, after the render.

    What arrives is drawn by a second pass, because views cannot be rendered
    from entities that have not arrived yet. Returns the backend's report, or
    None when there was nothing to do.
    """
    from .. import sync as sync_module

    report = sync_module.run(Project(where), reason, should_render=False)
    if report is not None and report.incoming:
        redraw = sync_module.run(Project(where), "redraw after sync", only_if_changed=True)
        if redraw is not None:
            # What arrived and what was sent are the first pass's; a problem
            # is whichever pass had one.
            report = dataclasses.replace(redraw, incoming=True,
                                         outgoing=report.outgoing or redraw.outgoing)
    return report


@operation("sync", params=SyncParams, result=SyncResult, kind="mutation", loads=False)
def sync(params: SyncParams, where: str | None) -> SyncResult:
    """Render, then bring the project into step with where it is kept.

    Explicitly asked for, so a failure refuses, where the sync after another
    command only warns: a backend that reports a problem, and one that fails
    on the filesystem. Either way the changes stay local, to send next time. `sync` stays explicit: a turn is the caller's to
    define, and REQ-0014's one record per turn holds by the caller syncing
    once per turn.
    """
    from .. import backend as backend_module

    backend_module.project_root(where)  # create the store if needed
    rendered = render(RenderParams(), Project(where))
    try:
        report = _send(where)
    except OSError as exc:
        raise Refused(f"sync: {exc}; changes are kept locally.", fix="szsdlc sync") from exc
    if report is not None and not report.ok:
        raise Refused(report.problem, fix=report.fix)
    return SyncResult(rendered.changed, bool(report and report.incoming),
                      bool(report and report.outgoing))
