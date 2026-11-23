"""szsdlc standards: conventions governing paths."""

from __future__ import annotations

import json
import pytest

from szsdlc.errors import EXIT_BAD_INPUT


@pytest.fixture
def standards(project):
    directory = project.standards_dir
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "templates.md").write_text(
        '---\napplies_to: ["templates/**"]\n---\n'
        "Never hardcode a version; reference the pinned digest.\n", encoding="utf-8")
    (directory / "docs.md").write_text(
        '---\napplies_to: ["docs/*.md"]\n---\nOne sentence per line.\n',
        encoding="utf-8")
    (directory / "everything.md").write_text(
        '---\napplies_to: ["**/*.py"]\n---\nType hints on public functions.\n',
        encoding="utf-8")
    return project


# ---------------------------------------------------------------------------
# standards
# ---------------------------------------------------------------------------


def test_standards_match_returns_only_what_applies(run, standards):
    code, output, _ = run("standards", "match", "templates/base.yaml")
    assert code == 0
    assert output.strip() == "templates"


def test_a_single_star_does_not_cross_a_separator(run, standards):
    """The distinction is the whole reason to write a glob rather than a substring."""
    _, shallow, _ = run("standards", "match", "docs/plan.md")
    _, deep, _ = run("standards", "match", "docs/nested/plan.md")
    assert shallow.strip() == "docs"
    assert deep.strip() == ""


def test_a_double_star_does_cross(run, standards):
    _, output, _ = run("standards", "match", "src/szsdlc/cli.py")
    assert output.strip() == "everything"


def test_matching_nothing_emits_nothing(run, standards):
    code, output, _ = run("standards", "match", "README.md")
    assert code == 0 and output == ""


def test_several_paths_are_matched_at_once(run, standards):
    _, output, _ = run("standards", "match", "templates/a.yaml", "docs/b.md")
    assert set(output.split()) == {"templates", "docs"}


def test_standards_match_json_carries_the_body(run, standards):
    _, output, _ = run("standards", "match", "templates/a.yaml", "--json")
    payload = json.loads(output)
    assert payload[0]["body"].startswith("Never hardcode a version")


def test_standards_match_without_paths_is_refused(run, standards):
    code, _, err = run("standards", "match")
    assert code == EXIT_BAD_INPUT
    assert "Fix: szsdlc standards match <path>" in err


def test_a_project_with_no_standards_is_silent(run, project):
    code, output, _ = run("standards", "match", "anything.md")
    assert code == 0 and output == ""
