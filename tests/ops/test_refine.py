"""The `refine` operation."""

from __future__ import annotations


from szsdlc import ops


def test_refine_returns_the_child_and_the_ideas_move(root):
    refined = ops.call("refine", {"ref": "IDEA-0001", "into": "work_item", "title": "W"}, root)
    assert (refined.source, refined.before, refined.after) == ("IDEA-0001", "inbox", "refined")
