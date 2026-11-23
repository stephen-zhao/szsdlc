"""The `list` operation."""

from __future__ import annotations


from szsdlc import ops


def test_list_filters(bulk):
    assert ops.call("list", {"tags": ["bulk"], "limit": 0}, bulk).total == 30
    assert [r.id for r in ops.call("list", {"type": "epic"}, bulk).rows] == ["EPIC-0001"]
    assert ops.call("list", {"status": "idea"}, bulk).rows[0].id == "WI-0001"
