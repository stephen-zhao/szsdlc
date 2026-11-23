"""szsdlc list: bounded, filtered listings."""

from __future__ import annotations

import json


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------
def test_list_filters_compose(run, world):
    _, output, _ = run("list", "--type", "work_item", "--tag", "valkey")
    assert {line.split()[0] for line in output.strip().splitlines()} == \
        {"WI-0001", "WI-0002"}

    _, output, _ = run("list", "--type", "work_item", "--tag", "tls")
    assert output.strip().split()[0] == "WI-0001"

    _, output, _ = run("list", "--status", "approved")
    assert len(output.strip().splitlines()) == 2


def test_list_by_parent(run, world):
    _, output, _ = run("list", "--parent", "EPIC-0001")
    assert len(output.strip().splitlines()) == 3


def test_uncovered_is_a_graph_query(run, world):
    """An approved requirement nobody has implemented is impossible to lose."""
    _, output, _ = run("list", "--uncovered")
    assert output.strip().split()[0] == "REQ-0002"


def test_unscheduled_excludes_terminal_and_non_schedulable(run, world):
    _, output, _ = run("list", "--unscheduled")
    listed = {line.split()[0] for line in output.strip().splitlines()}
    assert "REQ-0001" not in listed and "IDEA-0001" not in listed
    assert "EPIC-0001" in listed and "WI-0001" in listed


def test_a_tag_filter_normalizes_its_input(run, world):
    _, output, _ = run("list", "--tag", "  VALKEY  ")
    assert len(output.strip().splitlines()) == 2


def test_list_is_bounded_and_prints_the_total(run, bulk):
    bulk()
    _, output, _ = run("list")
    lines = output.strip().splitlines()
    assert len(lines) == 21
    assert lines[-1] == "showing 20 of 201 — rerun with --limit 0"


def test_limit_zero_is_the_documented_escape(run, bulk):
    bulk()
    _, output, _ = run("list", "--limit", "0")
    assert len(output.strip().splitlines()) == 201


def test_json_is_exempt_from_the_limit(run, bulk):
    bulk()
    _, output, _ = run("list", "--json")
    assert len(json.loads(output)) == 201


def test_list_json_carries_the_declared_fields(run, world):
    _, output, _ = run("list", "--type", "requirement", "--json")
    listed = {e["id"]: e["fields"] for e in json.loads(output)}
    assert listed == {"REQ-0001": {"forced_reason": None, "opened": "2026-08-01"},
                      "REQ-0002": {"forced_reason": None, "opened": "2026-08-01"}}


def test_an_unknown_type_filter_lists_the_known_ones(run, world):
    code, _, err = run("list", "--type", "ticket")
    assert code == 5
    assert "work_item" in err
