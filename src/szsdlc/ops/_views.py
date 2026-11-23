"""The render every mutation ends with, so any caller gets current views."""

from __future__ import annotations

import contextlib
import contextvars
from collections.abc import Iterator

from ._project import Project

_deferred: contextvars.ContextVar[bool] = contextvars.ContextVar("szsdlc_redraw_deferred",
                                                                 default=False)


@contextlib.contextmanager
def deferred() -> Iterator[None]:
    """Skip the redraw inside the block, for a caller that renders right after it.

    The CLI's after-command sync renders before it records anything; a
    second, identical render inside the operation would only double the cost
    of every command. Nothing else should need this.
    """
    token = _deferred.set(True)
    try:
        yield
    finally:
        _deferred.reset(token)


def redraw(project: Project) -> None:
    """Redraw the views from what is on disk now, tolerantly.

    Reloaded, because the project a mutation was given predates its write.
    Tolerant, as the edit hook's render is: a template that will not draw is
    `validate`'s to report, never a reason to fail a change that was made.
    And a hand-edited view is kept, as at turn end; only an explicit `render`
    or `sync` overwrites one.
    """
    if _deferred.get():
        return
    from .. import sync as sync_module

    try:
        fresh = Project(str(project.config.root))
    except Exception:  # noqa: BLE001 - a project that will not reload is validate's
        return
    sync_module.render(fresh, keep_hand_edited=True)
