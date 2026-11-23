"""The `backend` operation."""

from __future__ import annotations


from szsdlc import ops


def test_backend_says_where_the_project_is_kept(root):
    shown = ops.call("backend", {}, root)
    assert shown.type == "workdir" and not shown.inside


def test_a_store_operation_takes_a_loaded_project_too(root):
    assert ops.call("backend", {}, ops.Project(root)).type == "workdir"
