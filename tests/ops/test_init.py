"""The `init` operation."""

from __future__ import annotations

import pytest

from szsdlc import config as config_module
from szsdlc import ops
from szsdlc.ops import init as init_module

from szsdlc.errors import BadInput

def test_init_makes_a_project_and_refuses_a_second(tmp_path):
    made = ops.call("init", {"name": "demo"}, str(tmp_path))
    assert made.created[0] == ".szsdlc/config.yml"
    assert ops.call("list", {}, str(tmp_path)).total == 0
    with pytest.raises(BadInput, match="already exists"):
        ops.call("init", {}, str(tmp_path))


def test_init_keeps_generated_files_lf_with_gitattributes(tmp_path):
    """WI-0074: git for Windows converts to CRLF on checkout unless told not to."""
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
    config = config_module.load(tmp_path)
    for name in config.records:  # and the record lines it lacked were added
        record = config.record_path(name).relative_to(tmp_path).as_posix()
        assert lines.count(f"{record} text eol=lf") == 1


def test_an_existing_gitattributes_with_every_line_is_not_reported_as_created(tmp_path):
    config_path = tmp_path / ".szsdlc" / "config.yml"
    config_path.parent.mkdir()
    config_path.write_text("project: {name: demo}\n", encoding="utf-8")
    config = config_module.load(tmp_path)
    full = "\n".join(init_module.gitattributes_lines(config)) + "\n"
    (tmp_path / ".gitattributes").write_text(full, encoding="utf-8")
    assert init_module.write_gitattributes(config) is None  # nothing to add, nothing written
    assert (tmp_path / ".gitattributes").read_text(encoding="utf-8") == full


def test_a_gitattributes_symlink_is_left_alone(tmp_path):
    """It may point outside the project: init never writes through it."""
    outside = tmp_path / "outside.txt"
    outside.write_text("theirs\n", encoding="utf-8")
    project = tmp_path / "proj"
    project.mkdir()
    try:
        (project / ".gitattributes").symlink_to(outside)
    except OSError:  # pragma: no cover - no symlinks on this platform
        pytest.skip("symlinks are not available")
    made = ops.call("init", {}, str(project))
    assert ".gitattributes" not in made.created
    assert outside.read_text(encoding="utf-8") == "theirs\n"


def test_a_gitattributes_that_cannot_be_written_never_fails_init(tmp_path, monkeypatch):
    """A failed init cannot be rerun (its config exists), so the lines are best effort."""
    def refuse(*_args):
        raise PermissionError("read-only")

    monkeypatch.setattr(init_module.os, "replace", refuse)
    made = ops.call("init", {}, str(tmp_path))
    assert ".gitattributes" not in made.created
    assert not (tmp_path / ".gitattributes").exists()
    assert not (tmp_path / ".gitattributes.szsdlc-tmp").exists()  # nothing left behind


def test_record_outputs_are_quoted_or_escaped_as_git_needs(make_project):
    config = make_project({"records": {
        "spaced": {"title": "S", "data": "records/s.yml", "template": "records/s.md.j2",
                   "output": "my hosts.md"},
        "starred": {"title": "W", "data": "records/w.yml", "template": "records/w.md.j2",
                    "output": "a*b.md"},
    }})
    lines = init_module.gitattributes_lines(config)
    assert '"records/my hosts.md" text eol=lf' in lines
    assert "records/a\\*b.md text eol=lf" in lines
