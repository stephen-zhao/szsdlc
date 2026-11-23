"""szsdlc context: in-flight work and the counters block."""

from __future__ import annotations

import json

from .conftest import entities


# ---------------------------------------------------------------------------
# context
# ---------------------------------------------------------------------------
def test_context_reports_in_flight_work_and_the_current_task(run, world):
    code, output, _ = run("context")
    assert code == 0
    assert "in flight:" in output
    assert "WI-0001" in output and "executing" in output and "Add config" in output
    assert "current task (WI-0001): two" in output
    # WI-0002 is `ready` — the front of the workflow, so backlog, not in flight.
    assert "WI-0002" not in output


def test_in_flight_ids_share_a_column(run, world):
    """`SPK-0001` and `WI-0001` differ in width; the status column must not.

    Ragged columns are cheap to produce and expensive to read, and this block
    is prepended to a model's context at the start of every single session.
    """
    _, output, _ = run("context")
    rows = [line for line in output.splitlines() if line.startswith("  ")]
    assert len(rows) >= 2
    starts = {line.index(line.strip().split()[1]) for line in rows}
    assert len(starts) == 1, rows


def test_context_ends_in_a_counters_line_of_scalars(run, world):
    _, output, _ = run("context")
    counters = output.strip().splitlines()[-1]
    assert "inbox 1" in counters
    assert "uncovered reqs 1" in counters
    assert "unscheduled 4" in counters
    assert "entities 7" in counters


def test_context_replaces_a_two_hundred_line_listing_with_one_number(run, bulk):
    """The migration case: everything unscheduled, and it costs one scalar."""
    bulk()
    _, context_output, _ = run("context")
    _, listing_output, _ = run("list", "--unscheduled", "--limit", "0")

    assert "unscheduled 201" in context_output
    assert len(context_output) <= 1800
    # The listing it replaces is an order of magnitude larger.
    assert len(listing_output) > 10 * len(context_output)


def test_context_is_capped_regardless_of_project_size(run, bulk, project):
    bulk()
    for number in range(1, 30):
        entity = entities(project).by_text(f"WI-{number:04d}")
        for status in ("designing",):
            entity.set_status(status)
        entity.save()
    _, output, _ = run("context")
    assert len(output) <= 1800
    assert "more — szsdlc next" in output


def test_context_json_exposes_the_same_counters(run, world):
    _, output, _ = run("context", "--json")
    payload = json.loads(output)
    assert payload["counters"]["inbox"] == 1
    assert payload["current_entity"] == "WI-0001"


def test_an_empty_project_still_reports_its_counters(run, project):
    code, output, _ = run("context")
    assert code == 0
    assert "entities 0" in output
    assert "in flight" not in output
