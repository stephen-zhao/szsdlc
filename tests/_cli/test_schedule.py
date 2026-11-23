"""szsdlc schedule and unschedule."""

from __future__ import annotations

from szsdlc.errors import EXIT_BAD_INPUT
from szsdlc.errors import EXIT_BAD_INPUT, EXIT_REFUSED


# ---------------------------------------------------------------------------
# schedule / unschedule
# ---------------------------------------------------------------------------


def test_schedule_reports_the_new_neighbours(run, ready):
    run("schedule", "EPIC-0001", "--horizon", "now")
    code, output, _ = run("schedule", "WI-0001", "--horizon", "now")
    assert code == 0
    assert output.strip() == "WI-0001: now[1] — after EPIC-0001"


def test_schedule_positions(run, ready):
    run("schedule", "EPIC-0001", "--horizon", "now")
    _, output, _ = run("schedule", "WI-0001", "--horizon", "now", "--top")
    assert output.strip() == "WI-0001: now[0] — before EPIC-0001"


def test_scheduling_below_the_ready_status_is_refused(run, seeded):
    code, _, err = run("schedule", "WI-0001", "--horizon", "now")
    assert code == EXIT_REFUSED
    assert "has not reached ready" in err
    # The *next hop*, not the destination: WI-0001 is at `idea`, and
    # `status=ready` would simply refuse again as an illegal transition.
    assert "Fix: szsdlc set WI-0001 status=groomed" in err


def test_scheduling_a_requirement_is_refused(run, project):
    run("new", "requirement", "--title", "R")
    code, _, err = run("schedule", "REQ-0001", "--horizon", "now")
    assert code == EXIT_REFUSED
    assert "not schedulable" in err


def test_unschedule_confirms(run, ready):
    run("schedule", "WI-0001", "--horizon", "now")
    code, output, _ = run("unschedule", "WI-0001")
    assert code == 0
    assert output.strip() == "WI-0001: off roadmap"


def test_unscheduling_something_absent_is_refused(run, ready):
    code, _, err = run("unschedule", "WI-0001")
    assert code == EXIT_BAD_INPUT
    assert "is not on roadmap" in err
    assert "Fix: szsdlc schedule WI-0001 --horizon later" in err


def test_rescheduling_touches_only_the_roadmap_file(run, ready, project):
    import hashlib

    run("schedule", "EPIC-0001", "--horizon", "now")
    run("schedule", "WI-0001", "--horizon", "now")

    def fingerprint():
        return {p.relative_to(project.root).as_posix():
                hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(project.root.rglob("*")) if p.is_file()}

    before = fingerprint()
    run("schedule", "WI-0001", "--horizon", "later")
    after = fingerprint()
    changed = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
    # The roadmap and the view drawn from it. Nothing else: not the work item
    # that moved, not its epic, not a view that does not show the order.
    assert changed == {"roadmaps/roadmap.yml", "views/roadmap.md"}
