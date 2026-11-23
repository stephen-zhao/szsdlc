"""szsdlc tag and untag."""

from __future__ import annotations

from .conftest import entities


# ---------------------------------------------------------------------------
# tag / untag
# ---------------------------------------------------------------------------
def test_tagging_normalizes_on_write(run, seeded, project):
    code, output, _ = run("tag", "WI-0001", "DNS", "  Multi Word  ", "dns")
    assert code == 0
    assert output.strip() == "WI-0001: dns, multi-word"
    assert "tags: [dns, multi-word]" in \
        entities(project).by_text("WI-0001").path.read_text(encoding="utf-8")


def test_untagging_matches_the_normalized_form(run, seeded):
    run("tag", "WI-0001", "dns", "tls")
    code, output, _ = run("untag", "WI-0001", "DNS")
    assert code == 0
    assert output.strip() == "WI-0001: tls"


def test_removing_the_last_tag_drops_the_key(run, seeded, project):
    run("tag", "WI-0001", "dns")
    _, output, _ = run("untag", "WI-0001", "dns")
    assert output.strip() == "WI-0001: (no tags)"
    assert "tags:" not in entities(project).by_text("WI-0001").path.read_text(encoding="utf-8")


def test_retagging_leaves_the_id_and_path_untouched(run, seeded, project):
    before = entities(project).by_text("WI-0001").path
    run("tag", "WI-0001", "dns")
    run("untag", "WI-0001", "dns")
    run("tag", "WI-0001", "tls")
    assert entities(project).by_text("WI-0001").path == before
