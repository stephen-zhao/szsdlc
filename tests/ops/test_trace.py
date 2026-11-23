"""The `trace` operation."""

from __future__ import annotations


from szsdlc import ops


def test_trace_lists_the_edges_around_an_entity(root):
    traced = ops.call("trace", {"ref": "EPIC-0001"}, root)
    assert {(r.source, r.relation, r.target) for r in traced.rows} >= {("WI-0001", "parent", "EPIC-0001")}
