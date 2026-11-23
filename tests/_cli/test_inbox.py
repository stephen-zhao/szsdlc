"""szsdlc inbox: unrefined ideas, oldest first."""

from __future__ import annotations

import json


# ---------------------------------------------------------------------------
# inbox
# ---------------------------------------------------------------------------
def test_inbox_lists_only_unrefined_ideas(run, project):
    run("capture", "first thought")
    run("capture", "second thought")
    run("capture", "third thought")
    run("refine", "IDEA-0002", "--into", "work_item", "--title", "W")
    run("drop", "IDEA-0003", "--reason", "duplicate")

    code, output, _ = run("inbox")
    assert code == 0
    assert "IDEA-0001" in output
    assert "IDEA-0002" not in output
    assert "IDEA-0003" not in output


def test_inbox_shows_id_age_and_first_line(run, project):
    run("capture", "# valkey needs a sentinel quorum story\n\nmore detail\n")
    _, output, _ = run("inbox")
    row = output.strip()
    assert row.startswith("IDEA-0001")
    assert "0d" in row
    assert row.endswith("valkey needs a sentinel quorum story")
    assert len(output.strip().splitlines()) == 1


def test_inbox_is_bounded_and_says_so(run):
    for _ in range(25):
        run("capture", "thought")
    _, output, _ = run("inbox")
    lines = output.strip().splitlines()
    # C6 — bounded by default, and the truncation is visible.
    assert len(lines) == 21
    assert lines[-1] == "showing 20 of 25 — rerun with --limit 0"


def test_inbox_json_is_opt_in_and_unbounded(run):
    for _ in range(25):
        run("capture", "thought")
    _, output, _ = run("inbox", "--json")
    payload = json.loads(output)
    assert len(payload) == 25
    assert set(payload[0]) == {"id", "status", "age_days", "title"}


def test_an_empty_inbox_prints_nothing(run):
    code, output, err = run("inbox")
    assert code == 0 and output == "" and err == ""
