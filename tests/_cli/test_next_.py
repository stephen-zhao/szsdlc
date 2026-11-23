"""szsdlc next: actionable work, in roadmap order, unblocked."""

from __future__ import annotations

from szsdlc.roadmap import Roadmap

from .conftest import entities


# ---------------------------------------------------------------------------
# next
# ---------------------------------------------------------------------------
def test_next_draws_only_from_actionable_types(run, world):
    _, output, _ = run("next")
    listed = {line.split()[0] for line in output.strip().splitlines()}
    # Epics, requirements and ideas are not work.
    assert listed <= {"SPK-0001", "WI-0001", "WI-0002"}
    assert "EPIC-0001" not in listed and "REQ-0001" not in listed


def test_next_hides_blocked_work(run, world):
    """WI-0002 depends on WI-0001, which is still executing."""
    _, output, _ = run("next")
    assert "WI-0002" not in output
    assert "WI-0001" in output


def test_next_shows_work_once_its_dependency_is_terminal(run, world, project):
    entity = entities(project).by_text("WI-0001")
    entity.set_status("dropped")
    entity.save()
    _, output, _ = run("next")
    assert "WI-0002" in output


def test_next_follows_roadmap_order_not_a_per_entity_field(run, world, project):
    roadmap = Roadmap.load(project, "roadmap")
    roadmap.place("WI-0001", "later")
    roadmap.place("SPK-0001", "now")
    roadmap.save()

    _, output, _ = run("next")
    listed = [line.split()[0] for line in output.strip().splitlines()]
    assert listed.index("SPK-0001") < listed.index("WI-0001")


def test_next_can_be_filtered_by_horizon_and_parent(run, world, project):
    roadmap = Roadmap.load(project, "roadmap")
    roadmap.place("SPK-0001", "now")
    roadmap.place("WI-0001", "later")
    roadmap.save()

    _, output, _ = run("next", "--horizon", "now")
    assert output.strip().split()[0] == "SPK-0001"
    assert "WI-0001" not in output

    _, output, _ = run("next", "--parent", "EPIC-0001")
    assert "SPK-0001" in output and "WI-0001" in output


def test_next_stays_under_the_cap_at_two_hundred_entities(run, bulk):
    bulk()
    _, output, _ = run("next")
    lines = output.strip().splitlines()
    assert len(lines) == 11
    assert lines[-1] == "showing 10 of 200 — rerun with --limit 0"
    assert len(output) < 900
