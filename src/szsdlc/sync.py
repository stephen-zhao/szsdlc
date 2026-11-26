"""Sync: render, then sync the project sdlc store through its sdlc backend.

The workflow operation, as against :meth:`szsdlc.backend.Backend.sync`,
which is the mechanism one backend uses. The difference is the render, and this
module exists because that render has to happen in exactly one place.

It cannot go inside a backend. :mod:`szsdlc.backend` is about *where files
live* and knows nothing about entities, views or templates; teaching every
implementation to draw would invert that, and a second backend would either
repeat it or forget it.

It cannot go in each caller either, which is what it did before: six paths
reached a backend and two of them — `SessionStart` and `UserPromptSubmit` —
sent without rendering, so a session killed mid-turn left unrendered edits that
the next session committed and pushed. The rule is "nothing records or sends a
stale view", and a rule held by convention in six places is a rule with two
exceptions nobody noticed.

So: one function, above both layers, called by the CLI and by every hook.

Rendering is **tolerant** here, as it is in the edit hook. A project that will
not load, or a template that will not draw, is `validate`'s to report — it must
not fail the command that just worked, and must not stop the sync. Sending
a stale view is bad; refusing to send someone's work because a template is
broken is worse.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from . import backend as backend_module

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Callable

    from .backend import SyncReport


def run(session, reason: str, *, only_if_changed: bool = False,
        keep_hand_edited: bool = False,
        should_render: bool = True,
        on_render_error: Callable[[Exception], None] | None = None,
        ) -> SyncReport | None:
    """Render `session`'s project, then sync its backend.

    Args:
        session: A loaded project — `config`, `store` and `ids`.
        reason: What this sync is for; a git backend uses it as the
            commit subject, so it reads as history: "turn end", not "sync".
        only_if_changed: Skip entirely when the store holds nothing unsent.
        keep_hand_edited: Leave a generated file whose body no longer matches
            its own hash. Set by the turn-end hook, which nobody asked for and
            which cannot recover text it overwrites.
        should_render: False to sync without drawing first, for the one
            caller that is configured not to draw (`render_on_turn_end` off).
            The sync still happens: what someone chooses not to redraw is
            not work they chose not to keep.
        on_render_error: Called with the exception when the render failed.
            Tolerance is the default, not silence: a caller that can say
            something useful about a failed draw gets the chance to.

    Returns:
        The backend's report, or None when there was nothing to do.
    """
    if should_render:
        failure = render(session, keep_hand_edited=keep_hand_edited)
        if failure is not None and on_render_error is not None:
            on_render_error(failure)
    return backend_module.sync_at(session.config.root, reason,
                                  only_if_changed=only_if_changed)


def render(session, *, keep_hand_edited: bool = False) -> Exception | None:
    """The render half, for a caller that is not about to send anything.

    Returns what went wrong rather than raising it, so a caller chooses
    between reporting it and ignoring it without having to choose between
    reporting it and working.
    """
    from . import render as render_module
    from .graph import Graph

    try:
        graph = Graph(session.config, session.store, session.ids)
        render_module.render(session.config, session.store, graph,
                             should_keep_hand_edited=keep_hand_edited)
    except Exception as exc:  # noqa: BLE001 - never break a caller over a redraw
        return exc
    return None
