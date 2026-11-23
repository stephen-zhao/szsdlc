"""The `context` operation."""

from __future__ import annotations


from szsdlc import ops


def test_context_gives_the_pack_and_its_data(root):
    built = ops.call("context", {}, root)
    assert isinstance(built.text, str) and isinstance(built.counters, dict)
