"""Task 4 — the frontmatter round-trip, which gates the whole design.

Frontmatter is the only hand-authored state, and the CLI writes to the same
files people do. If a `set` reorders keys, eats comments or reflows the body,
users learn not to hand-edit — and hand-editing is the point. So the property
under test is severe: changing one field leaves every other byte identical.
"""

from __future__ import annotations

import datetime as dt
import itertools

import pytest

from szsdlc import frontmatter


SAMPLE = """\
---
id: WI-0042
title: Add sentinel quorum config
# which concerns this touches
tags: [dns, tls]
status: executing

relations:
  parent: EPIC-0011      # exactly one epic
  implements:
    - REQ-0007
    - REQ-0008
opened: 2026-08-16
---

# Add sentinel quorum config

Body text that must not move.

    indented block

- [ ] a task
"""


def test_parses_frontmatter_and_body():
    doc = frontmatter.parse(SAMPLE)
    assert doc.ok
    assert doc.data["id"] == "WI-0042"
    assert doc.data["tags"] == ["dns", "tls"]
    assert doc.data["relations"]["implements"] == ["REQ-0007", "REQ-0008"]
    assert doc.data["opened"] == dt.date(2026, 8, 16)
    assert doc.body.startswith("\n# Add sentinel quorum config")


def test_untouched_document_renders_byte_for_byte():
    assert frontmatter.parse(SAMPLE).render() == SAMPLE


def test_changing_one_field_changes_exactly_one_line():
    doc = frontmatter.parse(SAMPLE)
    doc.set("status", "review")

    before = SAMPLE.splitlines(keepends=True)
    after = doc.render().splitlines(keepends=True)
    assert len(before) == len(after)
    differing = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
    assert len(differing) == 1
    assert after[differing[0]] == "status: review\n"


@pytest.mark.parametrize("key,value", [
    ("status", "review"),
    ("title", "A different title"),
    ("opened", dt.date(2026, 12, 25)),
])
def test_a_single_change_preserves_comments_key_order_and_body(key, value):
    doc = frontmatter.parse(SAMPLE)
    doc.set(key, value)
    rendered = doc.render()

    assert "# which concerns this touches" in rendered
    assert "# exactly one epic" in rendered
    # Key order is the author's, not the serializer's.
    assert rendered.index("id:") < rendered.index("title:") < rendered.index("tags:")
    # The body is not the serializer's business at all.
    assert rendered.split("---\n", 2)[2] == SAMPLE.split("---\n", 2)[2]


def test_setting_a_new_key_appends_without_disturbing_the_rest():
    doc = frontmatter.parse(SAMPLE)
    doc.set("closed", dt.date(2026, 9, 1))
    rendered = doc.render()
    assert "closed: 2026-09-01\n---\n" in rendered
    assert "# which concerns this touches" in rendered


def test_unset_removes_only_its_own_lines():
    doc = frontmatter.parse(SAMPLE)
    doc.unset("relations")
    rendered = doc.render()
    assert "relations:" not in rendered
    assert "REQ-0007" not in rendered
    assert "opened: 2026-08-16" in rendered
    assert "status: executing" in rendered


def test_a_comment_above_a_key_belongs_to_that_key():
    """Replacing `status` must not eat the comment introducing `relations`."""
    doc = frontmatter.parse(SAMPLE)
    doc.set("status", "review")
    assert "# which concerns this touches" in doc.render()

    doc = frontmatter.parse(SAMPLE)
    doc.unset("tags")
    rendered = doc.render()
    assert "tags:" not in rendered
    assert "status: executing" in rendered
    # A comment survives its key's removal, left for a human to clear. The
    # alternative — treating it as part of the following block — would mean
    # every `set` silently deleted the comment above the field it touched,
    # which is the far commoner operation and the far worse loss.
    assert "# which concerns this touches" in rendered


def test_list_style_is_preserved():
    doc = frontmatter.parse(SAMPLE)
    doc.set("tags", ["dns", "tls", "valkey"])
    assert "tags: [dns, tls, valkey]\n" in doc.render()


def test_block_list_style_is_preserved():
    text = "---\nid: WI-0001\ntags:\n  - dns\n  - tls\nstatus: idea\n---\nbody\n"
    doc = frontmatter.parse(text)
    doc.set("tags", ["dns", "tls", "acme"])
    rendered = doc.render()
    assert "tags:\n  - dns\n  - tls\n  - acme\n" in rendered


def test_nested_mapping_is_written_indented():
    doc = frontmatter.parse("---\nid: WI-0001\nstatus: idea\n---\n")
    doc.set("relations", {"parent": "EPIC-0001", "implements": ["REQ-0001"]})
    rendered = doc.render()
    assert "relations:\n  parent: EPIC-0001\n  implements:\n    - REQ-0001\n" in rendered


def test_crlf_line_endings_survive():
    text = SAMPLE.replace("\n", "\r\n")
    doc = frontmatter.parse(text)
    assert doc.render() == text

    doc.set("status", "review")
    rendered = doc.render()
    assert "status: review\r\n" in rendered
    assert "\n\r\n" not in rendered.replace("\r\n", "")


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_a_key_with_no_value_on_its_own_line_is_addressable(newline):
    """`relations:` has nothing after the colon, and under CRLF the carriage
    return sits exactly where the key pattern expects whitespace. Getting this
    wrong makes every nested block unaddressable on Windows and nowhere else."""
    text = ("---\nid: WI-0001\nrelations:\n  parent: EPIC-0001\n"
            "status: idea\n---\nbody\n").replace("\n", newline)
    doc = frontmatter.parse(text)

    doc.unset("relations")
    rendered = doc.render()
    assert "relations:" not in rendered
    assert "EPIC-0001" not in rendered
    assert f"status: idea{newline}" in rendered


def test_unicode_survives_the_round_trip():
    text = "---\nid: WI-0001\ntitle: Café façade — naïve\nstatus: idea\n---\nBody ✅\n"
    doc = frontmatter.parse(text)
    assert doc.render() == text
    doc.set("status", "groomed")
    assert "Café façade — naïve" in doc.render()
    assert "Body ✅" in doc.render()


# ---------------------------------------------------------------------------
# Property test
# ---------------------------------------------------------------------------


BODIES = [
    "",
    "\nJust a body.\n",
    "\n# Heading\n\n- [ ] one\n- [x] two\n",
    "\n```yaml\nid: not-frontmatter\n```\n",
    "\n---\n\nA horizontal rule below the frontmatter.\n",
]

HEADS = [
    "---\nid: WI-0001\nstatus: idea\n---",
    "---\nid: WI-0001\ntitle: T\ntags: [a, b]\nstatus: idea\n---",
    "---\n# leading comment\nid: WI-0001\nstatus: idea\n\n# trailing comment\ntitle: T\n---",
    "---\nid: WI-0001\nstatus: idea\nrelations:\n  parent: EPIC-0001\n---",
]


@pytest.mark.parametrize("head,body,newline", list(itertools.product(HEADS, BODIES, ["\n", "\r\n"])))
def test_property_untouched_documents_are_byte_identical(head, body, newline):
    text = (head + "\n" + body).replace("\n", newline)
    assert frontmatter.parse(text).render() == text


@pytest.mark.parametrize("head,body,newline", list(itertools.product(HEADS, BODIES, ["\n", "\r\n"])))
def test_property_one_change_leaves_the_body_untouched(head, body, newline):
    text = (head + "\n" + body).replace("\n", newline)
    doc = frontmatter.parse(text)
    original_body = doc.body

    doc.set("status", "groomed")
    reparsed = frontmatter.parse(doc.render())

    assert reparsed.body == original_body
    assert reparsed.data["status"] == "groomed"
    assert reparsed.data["id"] == "WI-0001"


# ---------------------------------------------------------------------------
# Invalidity is represented, not raised
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text,fragment", [
    ("no frontmatter at all\n", "must open with ---"),
    ("---\nid: WI-0001\nstatus: idea\n", "never closed"),
    ("---\nid: [unclosed\n---\nbody\n", "frontmatter"),
    ("---\n- a list, not a mapping\n---\n", "must be a mapping"),
])
def test_broken_documents_carry_their_error(text, fragment):
    doc = frontmatter.parse(text)
    assert not doc.ok
    assert fragment in doc.error


def test_a_broken_document_renders_unchanged():
    """Never rewrite what you could not read."""
    text = "---\nid: [unclosed\n---\nbody\n"
    assert frontmatter.parse(text).render() == text


def test_editing_a_broken_document_is_refused():
    doc = frontmatter.parse("---\nid: [unclosed\n---\n")
    with pytest.raises(frontmatter.FrontmatterError):
        doc.set("status", "idea")


def test_empty_frontmatter_is_an_empty_mapping():
    doc = frontmatter.parse("---\n---\nbody\n")
    assert doc.ok and doc.data == {}
    doc.set("id", "WI-0001")
    assert doc.render() == "---\nid: WI-0001\n---\nbody\n"
