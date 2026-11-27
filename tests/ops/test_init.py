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


def test_init_keeps_generated_files_lf_with_gitattributes(tmp_path):
    """WI-0074: git for Windows converts to CRLF on checkout unless told not to."""
    from szsdlc import config as config_module

    made = ops.call("init", {"name": "demo"}, str(tmp_path))
    attributes = (tmp_path / ".gitattributes").read_text(encoding="utf-8")
    assert ".gitattributes" in made.created
    assert "views/** text eol=lf" in attributes.splitlines()
    config = config_module.load(tmp_path)
    for name in config.records:
        record = config.record_path(name).relative_to(tmp_path).as_posix()
        assert f"{record} text eol=lf" in attributes.splitlines()


def test_init_extends_an_existing_gitattributes_and_keeps_its_lines(tmp_path):
    (tmp_path / ".gitattributes").write_text("*.png binary\nviews/** text eol=lf\n", encoding="utf-8")
    ops.call("init", {}, str(tmp_path))
    lines = (tmp_path / ".gitattributes").read_text(encoding="utf-8").splitlines()
    assert lines[:2] == ["*.png binary", "views/** text eol=lf"]
    assert lines.count("views/** text eol=lf") == 1  # nothing written twice
