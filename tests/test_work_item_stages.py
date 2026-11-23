"""The default work item stages: design optional, plan required (WI-0054)."""

from __future__ import annotations

import io

import pytest

from szsdlc import config as C
from szsdlc.cli import main
from szsdlc.errors import EXIT_REFUSED
from szsdlc.model import load_all


@pytest.fixture(autouse=True)
def tty_stdin(monkeypatch):
    class Tty(io.StringIO):
        def isatty(self):
            return True
    monkeypatch.setattr("sys.stdin", Tty())


@pytest.fixture
def run(project, capsys):
    def invoke(*argv: str):
        code = main(["-C", str(project.root), *argv])
        captured = capsys.readouterr()
        return code, captured.out, captured.err
    return invoke


@pytest.fixture
def item(run, project):
    """WI-0001 at `ready`, and a writer for its artifacts."""
    run("new", "work_item", "--title", "Stages")
    for status in ("groomed", "ready"):
        assert run("set", "WI-0001", f"status={status}")[0] == 0
    assert run("schedule", "WI-0001", "--horizon", "now")[0] == 0

    def write(name, text):
        home = load_all(C.load(project.root)).by_text("WI-0001").home
        (home / name).write_text(text, encoding="utf-8")
    return write


def status(project):
    return load_all(C.load(project.root)).by_text("WI-0001").status


def walk(run, *statuses):
    for s in statuses:
        code, _, err = run("set", "WI-0001", f"status={s}")
        assert code == 0, f"{s}: {err}"


def test_through_design(run, item, project):
    item("design.md", "a design\n")
    item("plan.md", "- [x] one\n")
    walk(run, "designing", "designed", "planning", "planned", "executing", "review", "done")
    assert status(project) == "done"


def test_no_design_needed_goes_straight_to_planning(run, item, project):
    item("plan.md", "- [x] one\n")
    walk(run, "planning", "planned", "executing", "review", "done")
    assert status(project) == "done"


@pytest.mark.parametrize("path,target,document", [
    (("designing",), "designed", "design.md"),
    (("planning",), "planned", "plan.md"),
])
def test_each_stage_refuses_without_its_document(run, item, project, path, target, document):
    walk(run, *path)
    code, _, err = run("set", "WI-0001", f"status={target}")
    assert code == EXIT_REFUSED and f"{document} is missing or empty" in err
    assert status(project) == path[-1]


@pytest.mark.parametrize("back", ["designing", "planning"])
def test_building_can_go_back_to_design_or_plan(run, item, project, back):
    item("plan.md", "- [ ] one\n")
    walk(run, "planning", "planned", "executing", back)
    assert status(project) == back


def test_review_can_go_back_to_executing(run, item, project):
    item("plan.md", "- [ ] one\n")
    walk(run, "planning", "planned", "executing", "review", "executing")


def test_a_started_design_is_finished_before_planning(run, item, project):
    walk(run, "designing")
    code, _, err = run("set", "WI-0001", "status=planning")
    assert code == EXIT_REFUSED and "not a legal transition" in err


def test_an_item_left_in_the_old_planned_has_a_documented_way_forward(run, item, project):
    """Before WI-0054, `planned` meant designed: it gated design.md, not plan.md.

    Such an item is reported by validate's existing gate finding, and either
    writing plan.md or forcing it to `designed` (as docs/reference.md says)
    leaves the project clean.
    """
    item("design.md", "a design\n")
    entity = load_all(C.load(project.root)).by_text("WI-0001")
    entity.set_status("planned")  # what the old workflow would have left
    entity.save()

    code, output, _ = run("validate")
    assert code == 4
    assert "is planned but plan.md is missing or empty" in output

    code, _, err = run("set", "WI-0001", "status=designed", "--force",
                       "--reason", "stages renamed")
    assert code == 0, err
    assert run("validate")[0] == 0
