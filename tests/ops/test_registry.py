"""The registry, `call`, and the shapes every operation shares."""

from __future__ import annotations

import dataclasses
import json

import pytest

from szsdlc import ops
from szsdlc.errors import BadInput


def test_every_operation_is_registered_with_its_shapes():
    assert set(ops.OPERATIONS) == {
        "capture", "new", "set", "tag", "untag", "link", "unlink", "convert", "layout",
        "log", "schedule", "unschedule", "refine", "drop", "show", "list", "next",
        "inbox", "trace", "context", "standards", "validate", "render", "sync",
        "refresh", "backend", "backend_prune", "project_sdlc_stores", "init", "location"}
    for op in ops.OPERATIONS.values():
        assert dataclasses.is_dataclass(op.params) and dataclasses.is_dataclass(op.result)
        assert op.kind in ("query", "mutation")
        assert op.interfaces and op.interfaces <= {"cli", "http"}
        assert op.schema() is not None


def test_init_is_the_clis_alone_and_location_is_https():
    """`init` needs no project to attach to; `location` serves an interface where a person types one."""
    assert ops.OPERATIONS["init"].interfaces == {"cli"}
    assert ops.OPERATIONS["location"].interfaces == {"http"}
    assert all(op.interfaces == {"cli", "http"}
               for name, op in ops.OPERATIONS.items() if name not in ("init", "location"))


def test_queries_are_queries_and_mutations_are_mutations():
    queries = {n for n, op in ops.OPERATIONS.items() if op.kind == "query"}
    assert queries == {"show", "list", "next", "inbox", "trace", "context", "standards",
                       "validate", "backend", "project_sdlc_stores", "location"}


def test_a_mutation_that_changes_an_entity_takes_a_precondition():
    for name in ("set", "tag", "untag", "link", "unlink", "convert", "layout", "log",
                 "schedule", "unschedule", "refine", "drop"):
        assert "expect" in ops.OPERATIONS[name].schema(), name


def test_every_listing_takes_a_limit():
    for name in ("list", "next", "inbox", "trace", "standards", "validate"):
        schema = ops.OPERATIONS[name].schema()
        assert not schema["limit"]["required"]
        assert "total" in {f.name for f in dataclasses.fields(ops.OPERATIONS[name].result)}


def test_call_takes_a_mapping_and_a_path(root):
    shown = ops.call("show", {"ref": "WI-0001"}, root)
    assert isinstance(shown, ops.show.ShowResult)
    assert (shown.id, shown.status, shown.title) == ("WI-0001", "idea", "Work item one")


def test_call_takes_the_params_type_and_a_loaded_project(root):
    project = ops.Project(root)
    listed = ops.call("list", ops.list_.ListParams(type="work_item"), project)
    assert [r.id for r in listed.rows] == ["WI-0001", "WI-0002"]


def test_call_turns_a_list_into_a_tuple_param(root):
    changed = ops.call("set", {"ref": "WI-0001", "assignments": ["status=groomed"]}, root)
    assert changed.changes[0].after == "groomed"


@pytest.mark.parametrize("name,params,problem", [
    ("shwo", {}, "did you mean show?"),
    ("show", {}, "'ref' is required"),
    ("show", {"ref": "WI-0001", "colour": "red"}, "no param 'colour'"),
    ("show", ["WI-0001"], "params must be a mapping"),
])
def test_call_refuses_what_it_cannot_run(root, name, params, problem):
    with pytest.raises(BadInput, match=problem.replace("?", r"\?")):
        ops.call(name, params, root)


def test_results_are_json_safe(root):
    """`to_dict` is the shape any interface sends: dates, paths and tuples as JSON has them."""
    ops.call("set", {"ref": "WI-0001", "assignments": ["opened=2026-01-02"]}, root)
    for name, params in [("show", {"ref": "WI-0001"}), ("list", {}), ("next", {}),
                         ("inbox", {}), ("trace", {"ref": "EPIC-0001"}), ("context", {}),
                         ("validate", {}), ("standards", {}), ("backend", {})]:
        data = ops.call(name, params, root).to_dict()
        assert json.loads(json.dumps(data)) == data, name
    assert ops.call("show", {"ref": "WI-0001"}, root).to_dict()["fields"]["opened"] == "2026-01-02"
