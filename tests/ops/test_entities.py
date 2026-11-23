"""The mutations: post-state results, preconditions, and views redrawn inside."""

from __future__ import annotations

from pathlib import Path

import pytest

from szsdlc import ops
from szsdlc.errors import StalePrecondition


def file_of(root, ref):
    return Path(root) / ops.call("show", {"ref": ref}, root).path


def test_a_mutation_returns_its_post_state(root):
    changed = ops.call("set", {"ref": "WI-0002", "assignments": ["status=designing", "title=T"]}, root)
    assert changed.id == "WI-0002"
    assert [(c.field, c.before, c.after) for c in changed.changes] == [
        ("status", "ready", "designing"), ("title", "Work item two", "T")]
    assert ops.call("tag", {"ref": "WI-0002", "tags": ["dns", "edge"]}, root).tags == ("dns", "edge")
    linked = ops.call("link", {"ref": "WI-0001", "relation": "implements", "target": "REQ-0001"}, root)
    assert (linked.relation, linked.target, linked.inverse) == ("implements", "REQ-0001", "implemented_by")
    assert ops.call("capture", {"text": "  a thought  "}, root).id == "IDEA-0002"
    refined = ops.call("refine", {"ref": "IDEA-0001", "into": "work_item", "title": "W"}, root)
    assert (refined.source, refined.before, refined.after) == ("IDEA-0001", "inbox", "refined")


def test_a_forced_move_says_so(root):
    changed = ops.call("set", {"ref": "WI-0002", "assignments": ["status=groomed"],
                               "force": True, "reason": "  not   next "}, root)
    assert changed.changes[0].forced == "not next"


def test_a_status_change_reports_what_it_implied(root):
    ops.call("schedule", {"ref": "WI-0002", "horizon": "now"}, root)
    dropped = ops.call("drop", {"ref": "WI-0002", "reason": "covered elsewhere"}, root)
    assert dropped.after == "dropped"
    assert any(note.startswith("off ") for note in dropped.notes)


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


@pytest.mark.parametrize("name,params", [
    ("capture", {"text": "a thought"}),
    ("new", {"type": "spike", "title": "Investigate"}),
    ("set", {"ref": "WI-0001", "assignments": ["status=groomed"]}),
    ("tag", {"ref": "WI-0001", "tags": ["dns"]}),
    ("schedule", {"ref": "WI-0002", "horizon": "now"}),
    ("drop", {"ref": "IDEA-0001", "reason": "covered"}),
])
def test_a_mutation_leaves_the_views_current(root, name, params):
    """Any caller gets current views, without a hook to redraw them."""
    ops.call("render", {}, root)
    ops.call(name, params, root)
    assert ops.call("render", {}, root).changed == ()


def test_the_redraw_keeps_a_hand_edited_view(root):
    """Only an explicit render or sync overwrites one, as at turn end."""
    ops.call("render", {}, root)
    view = next((Path(root) / "views").glob("*.md"))
    view.write_text(view.read_text(encoding="utf-8") + "\nmy own note\n", encoding="utf-8")
    ops.call("tag", {"ref": "WI-0001", "tags": ["dns"]}, root)
    assert "my own note" in view.read_text(encoding="utf-8")
