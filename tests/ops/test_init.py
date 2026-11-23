"""The `init` operation."""

from __future__ import annotations

import pytest

from szsdlc import ops

from szsdlc.errors import BadInput

def test_init_makes_a_project_and_refuses_a_second(tmp_path):
    made = ops.call("init", {"name": "demo"}, str(tmp_path))
    assert made.created[0] == ".szsdlc/config.yml"
    assert ops.call("list", {}, str(tmp_path)).total == 0
    with pytest.raises(BadInput, match="already exists"):
        ops.call("init", {}, str(tmp_path))
