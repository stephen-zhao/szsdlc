"""The operations on a project as a whole: render, sync, refresh, backend, init."""

from __future__ import annotations

from pathlib import Path

import pytest

from szsdlc import ops
from szsdlc.errors import BadInput


def test_render_reports_what_it_rewrote(root):
    first = ops.call("render", {}, root)
    assert first.changed and all(not Path(p).is_absolute() for p in first.changed)
    assert ops.call("render", {}, root).changed == ()


def test_sync_renders_then_syncs(root):
    """On the default backend the sync is a no-op, and the render is the whole of it."""
    synced = ops.call("sync", {}, root)
    assert synced.changed and not synced.incoming
    assert ops.call("sync", {}, root).changed == ()


def test_sync_refuses_what_the_backend_could_not_do(root, monkeypatch):
    from szsdlc.backend import SyncReport
    from szsdlc.errors import Refused

    monkeypatch.setattr("szsdlc.sync.backend_module.sync_at",
                        lambda *a, **k: SyncReport(problem="remote unreachable", fix="szsdlc backend"))
    with pytest.raises(Refused, match="remote unreachable") as refused:
        ops.call("sync", {}, root)
    assert ops.refusal_to_data(refused.value)["fix"] == {"op": "backend", "params": {}}


def test_sync_redraws_what_arrived(root, monkeypatch):
    from szsdlc.backend import SyncReport

    reports = iter([SyncReport(incoming=True), SyncReport()])
    reasons = []
    monkeypatch.setattr("szsdlc.sync.backend_module.sync_at",
                        lambda start, reason, **k: reasons.append(reason) or next(reports))
    assert ops.call("sync", {}, root).incoming
    assert reasons == ["sync", "redraw after sync"]


def test_refresh_on_the_default_backend_takes_nothing(root):
    refreshed = ops.call("refresh", {}, root)
    assert (refreshed.incoming, refreshed.replaced) == (False, False)


def test_backend_says_where_the_project_is_kept(root):
    shown = ops.call("backend", {}, root)
    assert shown.type == "workdir" and not shown.inside


def test_init_makes_a_project_and_refuses_a_second(tmp_path):
    made = ops.call("init", {"name": "demo"}, str(tmp_path))
    assert made.created[0] == ".szsdlc/config.yml"
    assert ops.call("list", {}, str(tmp_path)).total == 0
    with pytest.raises(BadInput, match="already exists"):
        ops.call("init", {}, str(tmp_path))


def test_a_store_operation_takes_a_loaded_project_too(root):
    assert ops.call("backend", {}, ops.Project(root)).type == "workdir"
