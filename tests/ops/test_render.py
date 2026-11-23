"""The `render` operation."""

from __future__ import annotations


from szsdlc import ops

from pathlib import Path

def test_render_reports_what_it_rewrote(root):
    first = ops.call("render", {}, root)
    assert first.changed and all(not Path(p).is_absolute() for p in first.changed)
    assert ops.call("render", {}, root).changed == ()
