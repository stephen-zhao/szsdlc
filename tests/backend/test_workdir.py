"""szsdlc.backend.workdir: the project in the working tree."""

from __future__ import annotations

from szsdlc import backend

from .conftest import run


# ---------------------------------------------------------------------------
# Selecting a backend
# ---------------------------------------------------------------------------


def test_the_workdir_backend_has_nothing_to_say_and_nothing_to_do(workdir):
    """The degenerate implementation, pinned.

    Every one of these is a protocol obligation with no work behind it, and
    two are reached by nothing today — `restore` because this backend is never
    recorded, `summary` because it is only said when the project is elsewhere.
    They are asserted so that the next backend, written by copying this one,
    does not inherit an answer nobody ever read.
    """
    assert run("init") == 0
    chosen = backend.resolve(workdir)
    assert chosen.describe() == []            # no settings, and you are here
    assert chosen.summary() == "in the asset root"
    assert chosen.materialize() is None       # the tree already exists
    assert backend.sync(chosen, "x").ok  # nothing to bring into agreement

    restored = backend.backend_class("workdir").restore(workdir, {})
    assert restored.project_sdlc_root == workdir  # the project, not an empty directory
