"""The `init` operation."""

from __future__ import annotations

import os
import stat

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
    lines = (tmp_path / ".gitattributes").read_text(encoding="utf-8").splitlines()
    assert ".gitattributes" in made.created
    # A directory each: outputs configured after init are covered too.
    assert "views/** text eol=lf" in lines and "records/** text eol=lf" in lines


def test_init_appends_to_an_existing_gitattributes_and_never_rewrites_it(tmp_path):
    original = b"*.png binary\nviews/** text eol=lf\n"
    (tmp_path / ".gitattributes").write_bytes(original)
    ops.call("init", {}, str(tmp_path))
    data = (tmp_path / ".gitattributes").read_bytes()
    assert data.startswith(original)  # appended to, byte for byte
    lines = data.decode("utf-8").splitlines()
    assert lines.count("views/** text eol=lf") == 1 and lines.count("records/** text eol=lf") == 1


def test_appending_keeps_the_files_line_endings_and_mode(tmp_path):
    attributes = tmp_path / ".gitattributes"
    attributes.write_bytes(b"*.png binary\r\n*.jpg binary")  # CRLF, and no final newline
    attributes.chmod(0o600)
    ops.call("init", {}, str(tmp_path))
    data = attributes.read_bytes()
    assert data.startswith(b"*.png binary\r\n*.jpg binary\r\n")
    assert b"\n" not in data.replace(b"\r\n", b"")  # every line ends CRLF, as the file did
    if os.name == "posix":
        assert stat.S_IMODE(attributes.stat().st_mode) == 0o600


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
    """It may point outside the project: init never reads or writes through it."""
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


def test_a_gitattributes_that_cannot_be_opened_never_fails_init(tmp_path, monkeypatch):
    """A failed init cannot be rerun (its config exists), so the lines are best effort."""
    real_open = os.open

    def refuse(path, *args, **kwargs):
        if str(path).endswith(".gitattributes"):
            raise PermissionError("read-only")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(init_module.os, "open", refuse)
    made = ops.call("init", {}, str(tmp_path))
    assert ".gitattributes" not in made.created
    assert not (tmp_path / ".gitattributes").exists()


def test_attribute_patterns_are_quoted_or_escaped_as_git_needs():
    assert init_module.attribute_pattern("records/hosts.md") == "records/hosts.md"
    assert init_module.attribute_pattern("my hosts.md") == '"my hosts.md"'
    assert init_module.attribute_pattern("a*b.md") == "a\\*b.md"
    assert init_module.attribute_pattern("#x.md") == '"#x.md"'


def test_views_kept_at_the_root_get_a_line_each(make_project):
    """`./**` matches nothing in git, and `**` would claim every file at the root."""
    config = make_project({"paths": {"views": "."}})
    lines = init_module.gitattributes_lines(config)
    assert not any(line.startswith(("./", "**")) for line in lines)
    for name in config.views:
        assert f"{config.view_path(name).name} text eol=lf" in lines
    assert "records/** text eol=lf" in lines


def test_a_path_that_cannot_be_one_line_gets_none():
    assert not init_module._nameable("records/a\nb.md")
    assert not init_module._nameable(".") and init_module._nameable("views")
