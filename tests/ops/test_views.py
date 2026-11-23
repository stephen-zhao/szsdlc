"""The redraw every mutation ends with."""

from __future__ import annotations

import pytest

from szsdlc import ops

from pathlib import Path

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
