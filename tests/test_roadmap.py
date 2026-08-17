"""Task 6 — roadmap scheduling.

Placement is a fact about a set, so it lives in one record rather than as a
field smeared across every entity. The tests that matter are the ones that
would fail if that principle slipped: rescheduling touches exactly one file,
an entity can never appear twice, and every refusal names a runnable fix.
"""

from __future__ import annotations

import hashlib

import pytest

from szsdlc.errors import EXIT_REFUSED, BadInput, Refused
from szsdlc.ids import IdSpace
from szsdlc.model import load_all
from szsdlc.roadmap import Roadmap, Scheduler, has_reached, scheduling_findings


@pytest.fixture
def scheduling(project, write_entity):
    """Three schedulable entities at their scheduling thresholds."""
    write_entity("epic", 1, "title: E\nstatus: open\nopened: 2026-08-16\n")
    write_entity("spike", 3, "title: S\nstatus: open\nopened: 2026-08-16\n"
                             "relations:\n  parent: EPIC-0001\n")
    for number, status in ((42, "ready"), (51, "ready")):
        write_entity("work_item", number,
                     f"title: W{number}\nstatus: {status}\nopened: 2026-08-16\n"
                     "relations:\n  parent: EPIC-0001\n", slug=f"w{number}")
    return project


def scheduler(project, roadmap=None):
    ids = IdSpace(project)
    return Scheduler(project, load_all(project, ids), roadmap, ids)


def fingerprint(root):
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*")) if path.is_file()
    }


# ---------------------------------------------------------------------------
# The record
# ---------------------------------------------------------------------------


def test_a_missing_record_loads_as_empty_horizons(project):
    roadmap = Roadmap.load(project, "roadmap")
    assert roadmap.buckets == {"now": [], "next": [], "later": []}
    assert roadmap.all_ids() == []
    assert roadmap.error is None


def test_rendering_is_deterministic_and_one_entity_per_line(project):
    roadmap = Roadmap.load(project, "roadmap")
    roadmap.place("WI-0042", "now")
    roadmap.place("SPK-0003", "now")
    roadmap.place("EPIC-0011", "next")

    rendered = roadmap.render()
    assert "now:\n  - WI-0042\n  - SPK-0003\n" in rendered
    assert "next:\n  - EPIC-0011\n" in rendered
    # An empty horizon is still printed, so the roadmap's shape is visible
    # without consulting the config.
    assert "later: []\n" in rendered
    # Horizons appear in configured order.
    assert rendered.index("now:") < rendered.index("next:") < rendered.index("later:")


def test_the_record_round_trips_through_disk(project):
    roadmap = Roadmap.load(project, "roadmap")
    roadmap.place("WI-0042", "now")
    roadmap.place("WI-0051", "later")
    roadmap.save()

    reloaded = Roadmap.load(project, "roadmap")
    assert reloaded.buckets == {"now": ["WI-0042"], "next": [], "later": ["WI-0051"]}
    assert reloaded.render() == roadmap.render()


def test_position_lookups(project):
    roadmap = Roadmap.load(project, "roadmap")
    for entry in ("WI-0001", "WI-0002", "WI-0003"):
        roadmap.place(entry, "now")
    assert roadmap.position("WI-0002") == ("now", 1)
    assert roadmap.horizon_of("WI-0003") == "now"
    assert roadmap.position("WI-9999") is None
    assert "WI-0001" in roadmap and "WI-9999" not in roadmap


# ---------------------------------------------------------------------------
# Placement
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kwargs,expected", [
    ({}, ["a", "b", "c", "NEW"]),
    ({"top": True}, ["NEW", "a", "b", "c"]),
    ({"after": "a"}, ["a", "NEW", "b", "c"]),
    ({"before": "c"}, ["a", "b", "NEW", "c"]),
    ({"after": "c"}, ["a", "b", "c", "NEW"]),
    ({"before": "a"}, ["NEW", "a", "b", "c"]),
])
def test_insertion_at_every_position(project, kwargs, expected):
    roadmap = Roadmap.load(project, "roadmap")
    for entry in ("a", "b", "c"):
        roadmap.place(entry, "now")
    roadmap.place("NEW", "now", **kwargs)
    assert roadmap.entries("now") == expected


def test_scheduling_something_already_placed_moves_it(project):
    """One verb covers schedule and move, so appearing twice is impossible."""
    roadmap = Roadmap.load(project, "roadmap")
    for entry in ("a", "b", "c"):
        roadmap.place(entry, "now")

    roadmap.place("c", "now", top=True)
    assert roadmap.entries("now") == ["c", "a", "b"]

    roadmap.place("a", "later")
    assert roadmap.entries("now") == ["c", "b"]
    assert roadmap.entries("later") == ["a"]
    assert roadmap.repeated() == []


def test_placement_reports_its_neighbours(project):
    roadmap = Roadmap.load(project, "roadmap")
    for entry in ("a", "b", "c"):
        roadmap.place(entry, "now")
    placement = roadmap.place("NEW", "now", after="a")
    assert (placement.after, placement.before) == ("a", "b")
    assert str(placement) == "NEW: now[1] — after a before b"


def test_an_unknown_horizon_is_refused_with_the_legal_ones(project):
    with pytest.raises(BadInput) as excinfo:
        Roadmap.load(project, "roadmap").place("WI-0001", "someday")
    assert "now" in excinfo.value.fix and "later" in excinfo.value.fix


def test_an_anchor_on_another_horizon_names_that_horizon(project):
    roadmap = Roadmap.load(project, "roadmap")
    roadmap.place("WI-0001", "later")
    with pytest.raises(BadInput) as excinfo:
        roadmap.place("WI-0002", "now", after="WI-0001")
    assert excinfo.value.fix == "use --horizon later"


def test_an_absent_anchor_suggests_scheduling_it(project):
    with pytest.raises(BadInput) as excinfo:
        Roadmap.load(project, "roadmap").place("WI-0002", "now", after="WI-0001")
    assert excinfo.value.fix == "szsdlc schedule WI-0001 --horizon now"


def test_position_flags_are_mutually_exclusive(project):
    with pytest.raises(BadInput) as excinfo:
        Roadmap.load(project, "roadmap").place("WI-0001", "now", top=True, after="WI-0002")
    assert "mutually exclusive" in excinfo.value.problem


def test_unknown_horizons_in_the_file_are_preserved_not_dropped(project):
    """Rewriting the record must never silently discard scheduled work."""
    project.roadmap_path("roadmap").parent.mkdir(parents=True, exist_ok=True)
    project.roadmap_path("roadmap").write_text(
        "now:\n  - WI-0001\nsomeday:\n  - WI-0099\n", encoding="utf-8")

    roadmap = Roadmap.load(project, "roadmap")
    assert roadmap.unknown == {"someday": ["WI-0099"]}
    roadmap.place("WI-0002", "now")
    assert "WI-0099" in roadmap.render()

    finding = next(f for f in scheduling_findings(project, load_all(project))
                   if f.kind == "roadmap-horizon")
    assert "someday" in finding.message


def test_a_broken_record_is_represented_and_refuses_writes(project):
    path = project.roadmap_path("roadmap")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("now: [unclosed\n", encoding="utf-8")

    roadmap = Roadmap.load(project, "roadmap")
    assert roadmap.error
    with pytest.raises(BadInput):
        roadmap.place("WI-0001", "now")

    # It is a finding, so `validate` reports it rather than `sync` dying on it.
    assert any(f.kind == "roadmap-unparseable"
               for f in scheduling_findings(project, load_all(project)))


# ---------------------------------------------------------------------------
# has_reached
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("current,expected", [
    ("idea", False),
    ("groomed", False),
    ("ready", True),
    ("designing", True),
    ("executing", True),
    ("review", True),
    ("done", True),
    ("marinating", None),
])
def test_reaching_a_status_is_answered_by_reachability(project, current, expected):
    """Not by declaration order, which quietly gets branching workflows wrong."""
    workflow = project.type_for("work_item").workflow
    assert has_reached(workflow, current, "ready") is expected


# ---------------------------------------------------------------------------
# The guards
# ---------------------------------------------------------------------------


def test_scheduling_writes_only_the_roadmap_file(scheduling):
    before = fingerprint(scheduling.root)
    scheduler(scheduling).schedule("WI-0042", "now")
    after = fingerprint(scheduling.root)

    changed = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
    assert changed == {str(scheduling.roadmap_path("roadmap").relative_to(scheduling.root))}


def test_reordering_twice_still_touches_only_the_roadmap(scheduling):
    scheduler(scheduling).schedule("WI-0042", "now")
    scheduler(scheduling).schedule("WI-0051", "now")
    before = fingerprint(scheduling.root)

    scheduler(scheduling).schedule("WI-0051", "now", top=True)
    scheduler(scheduling).schedule("WI-0042", "later")

    after = fingerprint(scheduling.root)
    changed = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
    assert len(changed) == 1


def test_a_non_schedulable_type_is_refused(project, write_entity):
    write_entity("requirement", 1, "title: R\nstatus: approved\nopened: 2026-08-16\n")
    with pytest.raises(Refused) as excinfo:
        scheduler(project).schedule("REQ-0001", "now")
    err = excinfo.value
    assert "not schedulable" in err.problem
    assert err.exit_code == EXIT_REFUSED
    assert len(err.render().splitlines()) <= 3


def test_an_entity_below_the_ready_status_is_refused_with_the_fix(project, write_entity):
    write_entity("epic", 1, "title: E\nstatus: open\nopened: 2026-08-16\n")
    write_entity("work_item", 1, "title: W\nstatus: groomed\nopened: 2026-08-16\n"
                                 "relations:\n  parent: EPIC-0001\n")
    with pytest.raises(Refused) as excinfo:
        scheduler(project).schedule("WI-0001", "now")
    assert "has not reached ready" in excinfo.value.problem
    assert excinfo.value.fix == "szsdlc set WI-0001 status=ready"


def test_a_terminal_entity_is_refused(project, write_entity):
    write_entity("epic", 1, "title: E\nstatus: open\nopened: 2026-08-16\n")
    write_entity("work_item", 1, "title: W\nstatus: done\nopened: 2026-08-16\n"
                                 "relations:\n  parent: EPIC-0001\n")
    with pytest.raises(Refused) as excinfo:
        scheduler(project).schedule("WI-0001", "now")
    assert "terminal" in excinfo.value.problem
    assert excinfo.value.fix == "szsdlc unschedule WI-0001"


def test_a_status_outside_the_workflow_is_refused_rather_than_guessed(project, write_entity):
    write_entity("epic", 1, "title: E\nstatus: open\nopened: 2026-08-16\n")
    write_entity("work_item", 1, "title: W\nstatus: marinating\nopened: 2026-08-16\n"
                                 "relations:\n  parent: EPIC-0001\n")
    with pytest.raises(Refused) as excinfo:
        scheduler(project).schedule("WI-0001", "now")
    assert "not in the work_item workflow" in excinfo.value.problem


def test_an_unknown_reference_is_refused_before_anything_is_written(scheduling):
    before = fingerprint(scheduling.root)
    with pytest.raises(BadInput):
        scheduler(scheduling).schedule("WI-9999", "now")
    assert fingerprint(scheduling.root) == before


def test_unschedule_is_idempotent(scheduling):
    board = scheduler(scheduling)
    board.schedule("WI-0042", "now")
    assert scheduler(scheduling).unschedule("WI-0042") is True
    assert scheduler(scheduling).unschedule("WI-0042") is False


def test_the_roadmap_defaults_when_only_one_exists(scheduling):
    placement = scheduler(scheduling).schedule("SPK-0003", "next")
    assert placement.roadmap == "roadmap"


def test_a_second_roadmap_makes_the_flag_required(make_project, write_entity):
    project = make_project({"roadmaps": {"per-epic": {"horizons": ["now", "later"]}}})
    from szsdlc.errors import ConfigError

    with pytest.raises(ConfigError):
        Scheduler(project, load_all(project))
    # Naming one is enough; no positional roadmap argument exists.
    assert Scheduler(project, load_all(project), "per-epic").roadmap.name == "per-epic"


def test_a_per_epic_roadmap_can_demand_nothing(make_project, write_entity):
    project = make_project({"roadmaps": {"per-epic": {"horizons": ["now", "later"]}}})
    write_entity("epic", 1, "title: E\nstatus: open\nopened: 2026-08-16\n")
    write_entity("work_item", 1, "title: W\nstatus: ready\nopened: 2026-08-16\n"
                                 "relations:\n  parent: EPIC-0001\n")

    roadmaps = {"per-epic": Roadmap.load(project, "per-epic")}
    assert not [f for f in scheduling_findings(project, load_all(project), roadmaps)
                if f.kind == "roadmap-missing"]


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------


def test_a_clean_roadmap_has_no_findings(scheduling):
    board = scheduler(scheduling)
    for ref, horizon in (("WI-0042", "now"), ("WI-0051", "next"),
                         ("SPK-0003", "later"), ("EPIC-0001", "later")):
        scheduler(scheduling).schedule(ref, horizon)
    assert scheduling_findings(scheduling, board.store) == []


def test_a_ready_entity_on_no_roadmap_is_reported(scheduling):
    """What a prose priority list could never enforce."""
    findings = [f for f in scheduling_findings(scheduling, load_all(scheduling))
                if f.kind == "roadmap-missing"]
    assert {f.ref for f in findings} == {"WI-0042", "WI-0051", "SPK-0003", "EPIC-0001"}
    assert findings[0].fix.startswith("szsdlc schedule ")


def test_an_entity_scheduled_twice_is_reported(project):
    path = project.roadmap_path("roadmap")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("now:\n  - WI-0001\nlater:\n  - WI-0001\n", encoding="utf-8")
    finding = next(f for f in scheduling_findings(project, load_all(project))
                   if f.kind == "roadmap-duplicate")
    assert finding.ref == "WI-0001"


def test_a_dangling_scheduled_id_is_reported(project):
    path = project.roadmap_path("roadmap")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("now:\n  - WI-0420\n", encoding="utf-8")
    finding = next(f for f in scheduling_findings(project, load_all(project))
                   if f.kind == "roadmap-dangling")
    assert finding.fix == "szsdlc unschedule WI-0420"


def test_a_non_schedulable_type_that_leaked_in_is_reported(project, write_entity):
    write_entity("requirement", 1, "title: R\nstatus: approved\nopened: 2026-08-16\n")
    path = project.roadmap_path("roadmap")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("now:\n  - REQ-0001\n", encoding="utf-8")
    finding = next(f for f in scheduling_findings(project, load_all(project))
                   if f.kind == "roadmap-type")
    assert "not schedulable" in finding.message


def test_a_terminal_entity_left_on_the_roadmap_is_reported(project, write_entity):
    write_entity("epic", 1, "title: E\nstatus: open\nopened: 2026-08-16\n")
    write_entity("work_item", 1, "title: W\nstatus: done\nopened: 2026-08-16\n"
                                 "relations:\n  parent: EPIC-0001\n")
    path = project.roadmap_path("roadmap")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("now:\n  - WI-0001\n", encoding="utf-8")
    assert any(f.kind == "roadmap-terminal"
               for f in scheduling_findings(project, load_all(project)))
