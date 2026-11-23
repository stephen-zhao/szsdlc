"""The `set` operation."""

from __future__ import annotations


from szsdlc import ops


def test_set_returns_each_change_in_order(root):
    changed = ops.call("set", {"ref": "WI-0002", "assignments": ["status=designing", "title=T"]}, root)
    assert changed.id == "WI-0002"
    assert [(c.field, c.before, c.after) for c in changed.changes] == [
        ("status", "ready", "designing"), ("title", "Work item two", "T")]


def test_a_forced_move_says_so(root):
    changed = ops.call("set", {"ref": "WI-0002", "assignments": ["status=groomed"],
                               "force": True, "reason": "  not   next "}, root)
    assert changed.changes[0].forced == "not next"
