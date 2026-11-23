"""szsdlc show: one entity, or a budgeted context bundle."""

from __future__ import annotations

import datetime as dt
import json
import pytest

from szsdlc.cli import main
from szsdlc.ids import IdSpace
from szsdlc.model import create_entity


@pytest.fixture
def typed(make_project):
    """A requirement carrying a field this project declared, as szmde does."""
    return make_project({"entity_types": {"requirement": {
        "fields": {"test_type": {"type": "text"}}}}})


def written(project, **fields):
    """One requirement, in whatever layout its type declares."""
    return create_entity(project, IdSpace(project), project.type_for("requirement"),
                         title="Quorum survives", fields=fields)


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------
def test_show_prints_the_file(run, world):
    code, output, _ = run("show", "WI-0001")
    assert code == 0
    assert output.startswith("---\n")
    assert "title: Add config" in output


def test_show_accepts_a_sloppy_reference(run, world):
    assert run("show", "wi-1")[0] == 0


def test_show_context_is_a_budgeted_bundle_not_the_files(run, world):
    code, output, _ = run("show", "WI-0001", "--context")
    assert code == 0
    assert "WI-0001  executing  Add config" in output
    assert "progress: 1/3 (33%)" in output
    assert "next task: two" in output
    assert "parent: EPIC-0001" in output
    assert "implements: REQ-0001" in output
    # A design summary, not the design.
    assert "design.md: Use a three-node quorum." in output
    assert "# Design" not in output
    # The journal tail, not the journal.
    assert "2026-08-11 continued" in output
    assert len(output) <= 2400


def test_show_context_includes_generated_inverses(run, world):
    _, output, _ = run("show", "EPIC-0001", "--context")
    assert "children: SPK-0001, WI-0001, WI-0002" in output


def test_show_json_carries_the_derived_attributes(run, world):
    _, output, _ = run("show", "REQ-0001", "--json")
    payload = json.loads(output)
    assert payload["derived"] == {"covered": "True", "delivered": "False"}
    assert payload["relations"] == {}


def test_show_json_carries_the_declared_fields(run, world):
    """A type's own fields travel in `--json`.

    Without them a programmatic caller has to re-parse the frontmatter it just
    asked for, which is the one copy of the file it should never need to read.
    """
    _, output, _ = run("show", "REQ-0001", "--json")
    assert json.loads(output)["fields"] == {"forced_reason": None, "opened": "2026-08-01"}


def test_json_fields_carry_a_field_the_project_declared(typed, capsys):
    """The point of the key: a field this project declared, not a shipped one.

    A consuming project adds fields to a type and then needs them by script —
    reading them off the entity is what `--json` is for.
    """
    written(typed, opened="2026-08-01", test_type="unit")

    main(["-C", str(typed.root), "show", "REQ-0001", "--json"])
    assert json.loads(capsys.readouterr().out)["fields"] == {
        "forced_reason": None, "opened": "2026-08-01", "test_type": "unit"}


def test_json_fields_hold_a_declared_field_nobody_filled_in(typed, capsys):
    """An optional field that was never written reads as null, not as absent.

    A caller branching on the value should not also have to branch on whether
    the key is there.
    """
    written(typed, opened="2026-08-01")

    main(["-C", str(typed.root), "show", "REQ-0001", "--json"])
    assert json.loads(capsys.readouterr().out)["fields"]["test_type"] is None


def test_json_fields_date_survives_as_an_iso_string(typed, capsys):
    """A date field crosses as ISO, the same encoding the schema is shown.

    YAML hands back a `date` — or a `datetime` for a timestamp — and only
    `jsonify` renders both the way every other date at a machine boundary is
    rendered. `str()` would put a space where a timestamp needs its `T`.
    """
    written(typed, opened=dt.datetime(2026, 8, 1, 10, 0))

    main(["-C", str(typed.root), "show", "REQ-0001", "--json"])
    assert json.loads(capsys.readouterr().out)["fields"]["opened"] == "2026-08-01T10:00:00"
