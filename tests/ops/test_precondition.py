"""Preconditions: `show`'s hash, and every mutation's `expect`."""

from __future__ import annotations

import pytest

from szsdlc import ops

from pathlib import Path

from szsdlc.errors import StalePrecondition


def file_of(root, ref):
    return Path(root) / ops.call("show", {"ref": ref}, root).path

def test_show_gives_the_hash_a_write_passes(root):
    shown = ops.call("show", {"ref": "WI-0001"}, root)
    changed = ops.call("set", {"ref": "WI-0001", "assignments": ["status=groomed"],
                               "expect": shown.hash}, root)
    assert changed.changes[0].after == "groomed"
    assert ops.call("show", {"ref": "WI-0001"}, root).hash != shown.hash


@pytest.mark.parametrize("name,params", [
    ("set", {"assignments": ["status=groomed"]}),
    ("tag", {"tags": ["dns"]}),
    ("untag", {"tags": ["dns"]}),
    ("link", {"relation": "implements", "target": "REQ-0001"}),
    ("unlink", {"relation": "parent", "target": "EPIC-0001"}),
    ("convert", {"type": "spike"}),
    ("layout", {"layout": "directory"}),
    ("log", {"message": "a note"}),
    ("schedule", {"horizon": "now"}),
    ("unschedule", {}),
    ("drop", {"reason": "covered"}),
])
def test_a_stale_precondition_refuses_and_writes_nothing(root, name, params):
    ref = "WI-0002"
    path = file_of(root, ref)
    stale = ops.call("show", {"ref": ref}, root).hash
    path.write_text(path.read_text(encoding="utf-8") + "\nEdited by someone else.\n", encoding="utf-8")
    before = {p: p.read_bytes() for p in Path(root).rglob("*") if p.is_file()}

    with pytest.raises(StalePrecondition) as refused:
        ops.call(name, {"ref": ref, "expect": stale, **params}, root)

    assert ops.refusal_to_data(refused.value)["fix"] == {
        "op": "show", "params": {"ref": ref, "context": False}}
    after = {p: p.read_bytes() for p in Path(root).rglob("*") if p.is_file()}
    assert after == before


def test_refine_checks_the_idea(root):
    path = file_of(root, "IDEA-0001")
    stale = ops.call("show", {"ref": "IDEA-0001"}, root).hash
    path.write_text(path.read_text(encoding="utf-8") + "more\n", encoding="utf-8")
    with pytest.raises(StalePrecondition):
        ops.call("refine", {"ref": "IDEA-0001", "into": "work_item", "expect": stale}, root)


def test_a_current_precondition_is_no_obstacle(root):
    for name, params in [("tag", {"tags": ["dns"]}), ("log", {"message": "a note"}),
                         ("schedule", {"horizon": "now"}), ("unschedule", {})]:
        expect = ops.call("show", {"ref": "WI-0002"}, root).hash
        ops.call(name, {"ref": "WI-0002", "expect": expect, **params}, root)
