"""Task 3 — tag normalization and the text primitives it shares with lookups.

Tags are free-form and need no declaration. That only works because the single
write path folds every spelling to one value; a normalization applied on read
instead would leave `dns`, `DNS` and ` dns ` as three tags on disk.
"""

from __future__ import annotations

import pytest

from szsdlc.text import (
    TITLE_MAX_LENGTH,
    add_tags,
    clip,
    edit_distance,
    near_duplicates,
    nearest,
    normalize_tag,
    normalize_tags,
    remove_tags,
    slugify,
)


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("dns", "dns"),
        ("DNS", "dns"),
        ("  dns  ", "dns"),
        ("Multi Word Tag", "multi-word-tag"),
        ("multi   spaced", "multi-spaced"),
        ("tab\tseparated", "tab-separated"),
        ("snake_case", "snake-case"),
        ("--edges--", "edges"),
        ("a---b", "a-b"),
        ("", ""),
        ("   ", ""),
        ("---", ""),
    ],
)
def test_tag_normalization(raw, expected):
    assert normalize_tag(raw) == expected


def test_normalization_collapses_what_would_otherwise_split():
    """The whole point: three spellings of one concept become one tag."""
    assert normalize_tags(["dns", "DNS", "  dns  "]) == ["dns"]


def test_normalization_preserves_first_appearance_order():
    # Sorting instead would rewrite hand-authored frontmatter that did not
    # actually change.
    assert normalize_tags(["tls", "dns", "tls", "acme"]) == ["tls", "dns", "acme"]


def test_normalization_drops_empties():
    assert normalize_tags(["dns", "", "   ", "-"]) == ["dns"]


def test_normalization_can_be_switched_off():
    assert normalize_tags(["DNS", " tls "], normalize=False) == ["DNS", "tls"]


def test_add_tags_folds_a_hand_edited_spelling_already_on_the_entity():
    assert add_tags(["DNS", "tls"], ["dns"]) == ["dns", "tls"]


def test_add_tags_is_idempotent():
    once = add_tags(["dns"], ["tls"])
    assert add_tags(once, ["tls", "TLS"]) == once


def test_remove_tags_matches_on_the_normalized_form():
    assert remove_tags(["dns", "tls"], ["DNS"]) == ["tls"]
    assert remove_tags(["dns", "tls"], ["  TLS  "]) == ["dns"]


def test_removing_something_absent_is_a_no_op():
    assert remove_tags(["dns"], ["valkey"]) == ["dns"]


# ---------------------------------------------------------------------------
# Slugs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Add sentinel quorum config", "add-sentinel-quorum-config"),
        ("Café façade", "cafe-facade"),
        ("  spaces  everywhere  ", "spaces-everywhere"),
        ("punctuation!!! here?", "punctuation-here"),
        ("", ""),
        ("...", ""),
    ],
)
def test_slugify(raw, expected):
    assert slugify(raw) == expected


def test_slug_is_bounded_and_breaks_on_a_word():
    slug = slugify("word " * 40)
    assert len(slug) <= 60
    assert not slug.endswith("-")


# ---------------------------------------------------------------------------
# Distance
# ---------------------------------------------------------------------------


def test_transposition_costs_one():
    """`dns` vs `dsn` is *the* typo the near-duplicate warning exists to catch.

    Plain Levenshtein scores it 2, which would make the configured distance of
    1 miss it entirely.
    """
    assert edit_distance("dns", "dsn") == 1


@pytest.mark.parametrize(
    "a,b,expected",
    [("", "", 0), ("dns", "dns", 0), ("dns", "", 3), ("", "tls", 3), ("dns", "dnss", 1)],
)
def test_edit_distance_basics(a, b, expected):
    assert edit_distance(a, b) == expected


def test_nearest_returns_one_suggestion_or_none():
    known = ["WI-0042", "WI-0043", "SPK-0003"]
    assert nearest("WI-0420", known) == "WI-0042"
    assert nearest("ZZ-9999", known) is None


def test_near_duplicates_finds_likely_typos_and_ignores_distinct_tags():
    pairs = near_duplicates(["dns", "dsn", "tls", "valkey"])
    assert ("dns", "dsn") in pairs
    assert not any("valkey" in pair for pair in pairs)


def test_near_duplicate_distance_zero_disables_the_rule():
    assert near_duplicates(["dns", "dsn"], max_distance=0) == []


# ---------------------------------------------------------------------------
# clip
# ---------------------------------------------------------------------------


def test_short_text_is_returned_unchanged():
    assert clip("search is slow") == "search is slow"


def test_long_text_is_bounded_and_says_so():
    #: The failure this exists for: capture costs one command precisely so
    #: that a paragraph can be captured, and a paragraph then arrives as a
    #: derived title in every listing that mentions the entity.
    paragraph = ("People keep pasting the same three-line disclaimer into every "
                 "export. It should be a template we own, so legal can change it "
                 "once instead of chasing twelve teams.")
    clipped = clip(paragraph)
    assert len(clipped) <= TITLE_MAX_LENGTH
    assert clipped.endswith("…")
    assert paragraph.startswith(clipped[:20])


def test_clipping_breaks_on_a_word():
    assert clip("alpha beta gamma delta", 12) == "alpha beta…"


def test_a_single_long_word_is_still_bounded():
    assert len(clip("x" * 200)) <= TITLE_MAX_LENGTH


def test_newlines_collapse_rather_than_breaking_the_row():
    # A markdown table cell containing a newline is not a table cell.
    assert "\n" not in clip("first line\nsecond line", 100)
    assert clip("first line\nsecond line", 100) == "first line second line"
