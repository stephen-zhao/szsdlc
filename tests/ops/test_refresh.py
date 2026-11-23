"""The `refresh` operation."""

from __future__ import annotations


from szsdlc import ops


def test_refresh_on_the_default_backend_takes_nothing(root):
    refreshed = ops.call("refresh", {}, root)
    assert (refreshed.incoming, refreshed.replaced) == (False, False)
