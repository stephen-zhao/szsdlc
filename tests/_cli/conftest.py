"""Fixtures the CLI's tests share: one project, driven through szsdlc.cli.main."""

from __future__ import annotations

import io

import pytest

from szsdlc.cli import main
from szsdlc.ids import IdSpace
from szsdlc.model import create_entity, load_all


def entities(project):
    return load_all(project, IdSpace(project))


@pytest.fixture
def run(project, capsys):
    """Invoke the CLI against the fixture project and return (code, out, err)."""

    def invoke(*argv: str):
        code = main(["-C", str(project.root), *argv])
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    return invoke


@pytest.fixture
def stdin(monkeypatch):
    def feed(text: str):
        monkeypatch.setattr("sys.stdin", io.StringIO(text))
    return feed


@pytest.fixture
def no_stdin(monkeypatch):
    """A terminal: stdin must not be read, so $EDITOR is the fallback."""
    class Tty(io.StringIO):
        def isatty(self):
            return True
    monkeypatch.setattr("sys.stdin", Tty())


@pytest.fixture
def seeded(run, project):
    """An epic and a work item under it — the minimum coherent project."""
    run("new", "epic", "--title", "Sentinel quorum")
    run("new", "work_item", "--title", "Add sentinel config")
    run("link", "WI-0001", "parent", "EPIC-0001")
    return project


@pytest.fixture
def ready(run, seeded):
    for status in ("groomed", "ready"):
        run("set", "WI-0001", f"status={status}")
    return seeded


@pytest.fixture
def world(run, project, write_entity):
    """A small but complete project: an epic, two work items, a spike, two reqs."""
    write_entity("idea", 1, "status: inbox\ncaptured: 2026-01-01\n", "an old thought\n")
    write_entity("epic", 1, "title: Sentinel quorum\nstatus: active\nopened: 2026-08-01\n")
    write_entity("requirement", 1, "title: Quorum survives\nstatus: approved\n"
                                   "opened: 2026-08-01\n", slug="quorum")
    write_entity("requirement", 2, "title: Nobody built this\nstatus: approved\n"
                                   "opened: 2026-08-01\n", slug="uncovered")
    write_entity("spike", 1, "title: Investigate\nstatus: researching\nopened: 2026-08-01\n"
                             "relations:\n  parent: EPIC-0001\n")
    write_entity("work_item", 1, "title: Add config\nstatus: executing\nopened: 2026-08-01\n"
                                 "tags: [valkey, tls]\nrelations:\n  parent: EPIC-0001\n"
                                 "  implements: [REQ-0001]\n",
                 slug="add-config",
                 artifacts={"plan.md": "- [x] one\n- [ ] two\n- [ ] three\n",
                            "design.md": "# Design\n\nUse a three-node quorum.\n",
                            "journal.md": "- 2026-08-10 started\n- 2026-08-11 continued\n"})
    write_entity("work_item", 2, "title: Document it\nstatus: ready\nopened: 2026-08-02\n"
                                 "tags: [valkey]\nrelations:\n  parent: EPIC-0001\n"
                                 "  depends_on: [WI-0001]\n", slug="document")
    return project


@pytest.fixture
def bulk(project):
    """200 entities, every one of them unscheduled — the migration case."""

    def build(count: int = 200):
        ids = IdSpace(project)
        epic = create_entity(project, ids, project.type_for("epic"), title="Everything")
        for number in range(count):
            create_entity(project, ids, project.type_for("work_item"),
                          title=f"Work item number {number}", status="ready",
                          tags=["bulk"], relations={"parent": epic.id.text})
        return project

    return build
