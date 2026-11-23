"""The `validate` operation."""

from __future__ import annotations


from szsdlc import ops


def test_validate_counts_every_finding_not_only_those_shown(root, tmp_path):
    found = ops.call("validate", {"limit": 1}, root)
    assert found.total >= len(found.rows)
    assert found.errors + found.warnings == found.total
    assert found.ok == (found.errors == 0)
