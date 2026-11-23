"""The `capture` operation."""

from __future__ import annotations


from szsdlc import ops


def test_capture_returns_the_new_id(root):
    assert ops.call("capture", {"text": "  a thought  "}, root).id == "IDEA-0002"
