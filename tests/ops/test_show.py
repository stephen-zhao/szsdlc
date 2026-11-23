"""The `show` operation."""

from __future__ import annotations


from szsdlc import ops


def test_show_carries_the_file_and_the_bundle(root):
    shown = ops.call("show", {"ref": "WI-0001", "context": True}, root)
    assert shown.text.startswith("---\n")
    assert shown.bundle and "WI-0001" in shown.bundle
    assert ops.call("show", {"ref": "WI-0001"}, root).bundle is None
