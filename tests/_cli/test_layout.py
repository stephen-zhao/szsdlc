"""szsdlc layout: laying an entry out as a section, file or directory."""

from __future__ import annotations

from szsdlc.errors import EXIT_BAD_INPUT
from szsdlc.model import load_all


def reload(project, id_text: str):
    return load_all(project).by_text(id_text)


# ---------------------------------------------------------------------------
# Through the CLI
# ---------------------------------------------------------------------------
def test_layout_reports_where_it_went(run, project):
    run("capture", "drafts are lost on refresh")
    code, output, _ = run("layout", "IDEA-0001", "file")
    assert code == 0
    assert "section → file" in output
    assert "ideas/IDEA-0001" in output


def test_relaying_out_a_type_with_one_shape_is_refused(run, project):
    run("new", "requirement", "--title", "Reachable over TLS")
    code, _, err = run("layout", "REQ-0001", "file")
    assert code == EXIT_BAD_INPUT
    assert "no other layout to give it" in err
    assert "layout to dynamic" in err


def test_asking_for_the_layout_it_already_has_is_refused(run, project):
    run("capture", "a thought")
    code, _, err = run("layout", "IDEA-0001", "section")
    assert code == EXIT_BAD_INPUT
    assert "already laid out as a section" in err


def test_a_layout_with_nowhere_for_the_artifacts_is_refused(run, project):
    run("capture", "a thought")
    run("layout", "IDEA-0001", "directory")
    entity = reload(project, "IDEA-0001")
    (entity.home / "screenshot.txt").write_text("evidence\n", encoding="utf-8")

    code, _, err = run("layout", "IDEA-0001", "section")
    assert code == EXIT_BAD_INPUT
    assert "screenshot.txt" in err
    # Not "give it a directory" — it already has one. The only thing that can
    # actually be done is to deal with the files.
    assert "remove those artifacts" in err


def test_an_unknown_layout_is_refused_by_the_parser(run, project):
    run("capture", "a thought")
    code, _, err = run("layout", "IDEA-0001", "folder")
    assert code == EXIT_BAD_INPUT
    assert "folder" in err
