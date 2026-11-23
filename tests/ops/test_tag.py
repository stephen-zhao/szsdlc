"""The `tag` and `untag` operations."""

from __future__ import annotations


from szsdlc import ops


def test_tag_returns_every_tag_the_entity_has_now(root):
    assert ops.call("tag", {"ref": "WI-0002", "tags": ["dns", "edge"]}, root).tags == ("dns", "edge")
    assert ops.call("untag", {"ref": "WI-0002", "tags": ["dns"]}, root).tags == ("edge",)
