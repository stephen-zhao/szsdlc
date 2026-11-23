"""The bounds every listing shares: a limit, and an honest total."""

from __future__ import annotations

import pytest

from szsdlc import ops


@pytest.mark.parametrize("name,params,default", [
    ("list", {}, 20), ("next", {}, 10), ("inbox", {}, 20),
])
def test_a_listing_is_bounded_and_says_how_many_there_were(bulk, name, params, default):
    found = ops.call(name, params, bulk)
    if found.total > default:
        assert len(found.rows) == default
    everything = ops.call(name, {**params, "limit": 0}, bulk)
    assert len(everything.rows) == everything.total == found.total
    assert ops.call(name, {**params, "limit": 3}, bulk).rows == everything.rows[:3]
