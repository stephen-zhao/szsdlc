"""The `standards` operation."""

from __future__ import annotations

import pytest

from szsdlc import ops


def test_standards_mode_is_list_or_match(root):
    from szsdlc.errors import BadInput

    with pytest.raises(BadInput):
        ops.call("standards", {"mode": "every"}, root)
    with pytest.raises(BadInput, match="no paths"):
        ops.call("standards", {"mode": "match"}, root)
    assert ops.call("standards", {"mode": "list"}, root).total >= 0
