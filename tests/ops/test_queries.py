"""The queries: data, bounded honestly."""

from __future__ import annotations

import pytest

from szsdlc import ops

from _cli.test_contract import build_project


@pytest.fixture
def bulk(tmp_path):
    return str(build_project(tmp_path, entities=30).root)


@pytest.mark.parametrize("name,params,default", [
    ("list", {}, 20), ("next", {}, 10), ("inbox", {}, 20),
])
def test_a_listing_is_bounded_and_says_how_many_there_were(bulk, name, params, default):
    found = ops.call(name, params, bulk)
    if found.total > default:
        assert len(found.rows) == default
    everything = ops.call(name, {**params, "limit": 0}, bulk)
    assert len(everything.rows) == everything.total == found.total
    assert ops.call(name, {**params, "limit": 3}, bulk).rows == everything.rows[:3]


def test_list_filters(bulk):
    assert ops.call("list", {"tags": ["bulk"], "limit": 0}, bulk).total == 30
    assert [r.id for r in ops.call("list", {"type": "epic"}, bulk).rows] == ["EPIC-0001"]
    assert ops.call("list", {"status": "idea"}, bulk).rows[0].id == "WI-0001"


def test_show_carries_the_file_and_the_bundle(root):
    shown = ops.call("show", {"ref": "WI-0001", "context": True}, root)
    assert shown.text.startswith("---\n")
    assert shown.bundle and "WI-0001" in shown.bundle
    assert ops.call("show", {"ref": "WI-0001"}, root).bundle is None


def test_validate_counts_every_finding_not_only_those_shown(root, tmp_path):
    found = ops.call("validate", {"limit": 1}, root)
    assert found.total >= len(found.rows)
    assert found.errors + found.warnings == found.total
    assert found.ok == (found.errors == 0)


def test_standards_mode_is_list_or_match(root):
    from szsdlc.errors import BadInput

    with pytest.raises(BadInput):
        ops.call("standards", {"mode": "every"}, root)
    with pytest.raises(BadInput, match="no paths"):
        ops.call("standards", {"mode": "match"}, root)
    assert ops.call("standards", {"mode": "list"}, root).total >= 0


def test_trace_and_context(root):
    traced = ops.call("trace", {"ref": "EPIC-0001"}, root)
    assert {(r.source, r.relation, r.target) for r in traced.rows} >= {("WI-0001", "parent", "EPIC-0001")}
    built = ops.call("context", {}, root)
    assert isinstance(built.text, str) and isinstance(built.counters, dict)
