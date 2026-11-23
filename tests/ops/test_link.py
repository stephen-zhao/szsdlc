"""The `link` and `unlink` operations."""

from __future__ import annotations


from szsdlc import ops


def test_link_returns_the_generated_inverse(root):
    linked = ops.call("link", {"ref": "WI-0001", "relation": "implements", "target": "REQ-0001"}, root)
    assert (linked.relation, linked.target, linked.inverse) == ("implements", "REQ-0001", "implemented_by")
    unlinked = ops.call("unlink", {"ref": "WI-0001", "relation": "implements", "target": "REQ-0001"}, root)
    assert unlinked.remaining == ()
