"""Task 19 — the `section` layout.

Ten one-line thoughts should not cost ten files, so a `section`-layout type
keeps its entries as sections of one shared file. Three properties carry the
design, and each is asserted below rather than reasoned about:

1. **A section is a whole entity document.** Cut the heading off and what is
   left is the same `---` frontmatter and body every other layout stores. That
   is what will make moving an entry between layouts a move of bytes.
2. **Writing one entry does not disturb another.** The file is re-read
   immediately before each write, so two captures in a row cannot lose the
   first — which is the failure mode a shared file introduces and the reason
   the other layouts never needed this file.
3. **Writing an unchanged entry changes nothing.** Without it, every `set`
   would leave a blank line behind and the file would drift a little on every
   command.
"""

from __future__ import annotations

import itertools

import pytest

from szsdlc import frontmatter, sections
from szsdlc.ids import IdSpace
from szsdlc.model import create_entity, load_all

PREAMBLE = "# Ideas\n\nEverything not yet triaged.\n\n"

ENTRY = (
    "## IDEA-0001 — drafts are lost on refresh\n\n"
    "---\nid: IDEA-0001\nstatus: inbox\ncaptured: 2026-08-16\n---\n\n"
    "Drafts are lost on refresh.\n\n"
)

SECOND = (
    "## IDEA-0002 — exports lack a timeout\n\n"
    "---\nid: IDEA-0002\nstatus: inbox\ncaptured: 2026-08-16\n---\n\n"
    "Exports lack a timeout.\n\n"
)


def names(text: str) -> str | None:
    """The id a heading names — `IDEA-<n>` and nothing else."""
    first = text.split()[0] if text.split() else ""
    return first if first.startswith("IDEA-") else None


def recognises(name: str) -> bool:
    return names(name) is not None


# ---------------------------------------------------------------------------
# Splitting
# ---------------------------------------------------------------------------


def test_the_preamble_belongs_to_the_file_not_to_an_entry():
    preamble, found = sections.split(PREAMBLE + ENTRY + SECOND, recognises)
    assert preamble == PREAMBLE
    assert [s.name for s in found] == [
        "IDEA-0001 — drafts are lost on refresh",
        "IDEA-0002 — exports lack a timeout",
    ]


def test_a_heading_that_names_no_entry_does_not_split_one():
    """`## Notes` inside a body is prose. A delimiter that fired on every
    heading would silently cut entries in half."""
    text = PREAMBLE + ENTRY.rstrip("\n") + "\n\n## Notes\n\nstill IDEA-0001.\n"
    _, found = sections.split(text, recognises)
    assert len(found) == 1
    assert "## Notes" in found[0].text


def test_splitting_and_reassembling_is_byte_exact():
    text = PREAMBLE + ENTRY + SECOND
    preamble, found = sections.split(text, recognises)
    assert preamble + "".join(s.text for s in found) == text


def test_a_file_with_no_entries_is_all_preamble():
    preamble, found = sections.split(PREAMBLE, recognises)
    assert (preamble, found) == (PREAMBLE, [])


# ---------------------------------------------------------------------------
# A section is a whole document
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("body,newline", list(itertools.product(
    ["Just a body.", "# Heading\n\n- [ ] one\n- [x] two", "---\n\nA rule."],
    ["\n", "\r\n"],
)))
def test_a_section_round_trips_through_compose(body, newline):
    """Compose what split returned and get the same bytes back.

    This is the section-layout half of the frontmatter property test, and it
    gates this layout the same way that one gated the model: if a section
    cannot survive being read and written, nothing above it can be trusted.
    """
    document = ("---\nid: IDEA-0001\nstatus: inbox\n---\n\n" + body + "\n")
    text = sections.compose("IDEA-0001 — a thought", document, newline)
    _, found = sections.split(text, recognises)
    assert len(found) == 1
    assert sections.compose(found[0].name, found[0].document, newline) == text


def test_a_sections_document_is_what_frontmatter_parses():
    _, found = sections.split(PREAMBLE + ENTRY, recognises)
    document = frontmatter.parse(found[0].document)
    assert document.ok
    assert document.data["id"] == "IDEA-0001"
    assert document.body.strip() == "Drafts are lost on refresh."


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def test_upsert_appends_an_entry_it_has_never_seen():
    text = sections.upsert(PREAMBLE + ENTRY, names, "IDEA-0002",
                           "IDEA-0002 — exports lack a timeout",
                           "---\nid: IDEA-0002\n---\n\nExports lack a timeout.\n")
    assert text.startswith(PREAMBLE + ENTRY)
    assert text.count("## IDEA-") == 2


def test_upsert_replaces_only_its_own_entry():
    text = sections.upsert(PREAMBLE + ENTRY + SECOND, names, "IDEA-0001",
                           "IDEA-0001 — drafts are lost on refresh",
                           "---\nid: IDEA-0001\nstatus: refining\n---\n\nRewritten.\n")
    assert PREAMBLE in text
    assert SECOND in text
    assert "Rewritten." in text
    assert "captured: 2026-08-16" not in text.split("## IDEA-0002")[0]


def test_writing_an_unchanged_entry_changes_nothing():
    """Every `set`, `tag` and `link` goes through this path. One stray blank
    line per write and the file drifts a little on every command."""
    text = PREAMBLE + ENTRY + SECOND
    _, found = sections.split(text, recognises)
    once = sections.upsert(text, names, "IDEA-0001", found[0].name, found[0].document)
    assert once == text


def test_remove_takes_one_entry_and_leaves_the_rest():
    text = sections.remove(PREAMBLE + ENTRY + SECOND, names, "IDEA-0001")
    assert text == PREAMBLE + SECOND


def test_an_entry_that_is_not_there_removes_nothing():
    text = PREAMBLE + ENTRY
    assert sections.remove(text, names, "IDEA-0099") == text


def test_the_files_own_line_endings_survive_a_write():
    text = (PREAMBLE + ENTRY).replace("\n", "\r\n")
    written = sections.upsert(text, names, "IDEA-0002", "IDEA-0002 — another",
                              "---\nid: IDEA-0002\n---\n\nAnother.\n")
    assert "\n" not in written.replace("\r\n", "")


# ---------------------------------------------------------------------------
# Through the model
# ---------------------------------------------------------------------------


def test_capturing_twice_keeps_both(project):
    """The failure a shared file introduces, and the reason every write
    re-reads the file first."""
    ids = IdSpace(project)
    idea = project.type_for("idea")

    first = create_entity(project, ids, idea, body="drafts are lost on refresh\n")
    second = create_entity(project, IdSpace(project), idea, body="exports lack a timeout\n")

    assert (first.id.text, second.id.text) == ("IDEA-0001", "IDEA-0002")
    store = load_all(project)
    assert [e.id.text for e in store.of_type("idea")] == ["IDEA-0001", "IDEA-0002"]
    assert store.by_text("IDEA-0001").body.strip() == "drafts are lost on refresh"


def test_ten_captures_cost_one_file(project):
    """The whole point of the layout, asserted as the count it changes."""
    for number in range(10):
        create_entity(project, IdSpace(project), project.type_for("idea"),
                      body=f"thought {number}\n")

    files = [p for p in project.dir_for("idea").iterdir() if p.is_file()]
    assert files == [project.section_path("idea")]
    assert len(load_all(project).of_type("idea")) == 10


def test_editing_one_entry_leaves_its_neighbours_byte_identical(project):
    for body in ("first thought", "second thought", "third thought"):
        create_entity(project, IdSpace(project), project.type_for("idea"),
                      body=body + "\n")

    path = project.section_path("idea")
    before = path.read_bytes().decode("utf-8")

    entity = load_all(project).by_text("IDEA-0002")
    entity.set_status("refining")
    entity.save()

    after = path.read_bytes().decode("utf-8")
    assert before.split("## IDEA-0002")[0] == after.split("## IDEA-0002")[0]
    assert before.split("## IDEA-0003")[1] == after.split("## IDEA-0003")[1]
    assert "status: refining" in after


def test_deleting_an_entry_does_not_take_the_file_with_it(project):
    """`convert` used to unlink the entity's path. For this layout that path
    is every other entry's file too."""
    for body in ("first thought", "second thought"):
        create_entity(project, IdSpace(project), project.type_for("idea"),
                      body=body + "\n")

    load_all(project).by_text("IDEA-0001").delete()

    assert project.section_path("idea").is_file()
    remaining = load_all(project).of_type("idea")
    assert [e.id.text for e in remaining] == ["IDEA-0002"]
