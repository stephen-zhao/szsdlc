"""szsdlc init: scaffolding a project."""

from __future__ import annotations

from szsdlc import config as C
from szsdlc.cli import main
from szsdlc.errors import EXIT_BAD_INPUT


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------
def test_init_scaffolds_a_working_project(tmp_path, capsys):
    assert main(["-C", str(tmp_path), "init"]) == 0
    output = capsys.readouterr().out
    assert len(output.splitlines()) <= 15

    config = C.load(tmp_path)
    for entity_type in config.entity_types.values():
        assert config.dir_for(entity_type).is_dir()
    assert config.roadmap_path("roadmap").is_file()
    assert config.views_dir.is_dir() and config.standards_dir.is_dir()


def test_the_starter_config_is_almost_empty(tmp_path):
    """It has to be: everything real lives in the merged defaults."""
    main(["-C", str(tmp_path), "init"])
    text = C.config_path_for(tmp_path).read_text(encoding="utf-8")
    declared = [line for line in text.splitlines()
                if line.strip() and not line.lstrip().startswith("#")]
    assert declared == ["project:", f"  name: {tmp_path.name}"]
    assert C.load(tmp_path).data["project"]["name"] == tmp_path.name


def test_init_twice_is_refused(tmp_path, capsys):
    main(["-C", str(tmp_path), "init"])
    capsys.readouterr()
    assert main(["-C", str(tmp_path), "init"]) == EXIT_BAD_INPUT
    assert "already exists" in capsys.readouterr().err


def test_a_scaffolded_project_captures_immediately(tmp_path, capsys):
    main(["-C", str(tmp_path), "init"])
    capsys.readouterr()
    assert main(["-C", str(tmp_path), "capture", "a thought"]) == 0
    assert capsys.readouterr().out == "IDEA-0001\n"
