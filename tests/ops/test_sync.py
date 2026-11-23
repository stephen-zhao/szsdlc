"""The `sync` operation."""

from __future__ import annotations

import pytest

from szsdlc import ops


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
