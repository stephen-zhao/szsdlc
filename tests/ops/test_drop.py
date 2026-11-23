"""The `drop` operation."""

from __future__ import annotations


from szsdlc import ops


def test_a_status_change_reports_what_it_implied(root):
    ops.call("schedule", {"ref": "WI-0002", "horizon": "now"}, root)
    dropped = ops.call("drop", {"ref": "WI-0002", "reason": "covered elsewhere"}, root)
    assert dropped.after == "dropped"
    assert any(note.startswith("off ") for note in dropped.notes)
