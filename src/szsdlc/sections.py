"""One file, many entries: the `section` layout's storage.

A one-line thought should not cost a directory — that was already true, and the
`file` layout answered it. Ten one-line thoughts should not cost ten files
either, which is what this answers. Entries of a `section`-layout type live as
sections of one shared markdown file, and capture appends to it.

The format is deliberately not a new one. A section is **a whole entity
document** — the same `---` frontmatter and body every other layout stores —
with a heading above it carrying the same name its filename would have carried:

    ## IDEA-0001 — drafts are lost on refresh

    ---
    id: IDEA-0001
    status: inbox
    captured: 2026-08-23
    ---

    Drafts are lost on refresh.

Two things follow from that, and both are the reason for it. Moving an entry
out to its own file is a move of these bytes rather than a re-serialisation,
which is what makes the `dynamic` layout cheap. And every reader and writer of
frontmatter already works here unchanged — this module only ever hands out and
takes back a slice of text.

**A heading delimits an entry only if it parses as an id.** A `## Notes`
written inside a body therefore stays part of the entry it was written in,
rather than silently splitting it in two. The cost is that `## IDEA-0002`
written inside a body *does* split; that is the trade, and it is the one that
fails visibly.

Editing is surgical for the same reason it is in `frontmatter`: this writes to
files people also write to. Only the one section's lines are replaced, and the
file is re-read immediately before every write, so two captures in a row cannot
lose the first.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

#: A level-2 ATX heading. The level is fixed rather than configurable: a
#: delimiter that varies per project is a delimiter that has to be discovered
#: before a file can be read.
_HEADING = re.compile(r"^##[ \t]+(?P<name>\S.*?)[ \t]*$")

#: What separates the id from the human-readable remainder of a heading. The
#: remainder is cosmetic, exactly as a filename's slug is: written on every
#: save, parsed by nothing.
TITLE_SEPARATOR = " — "


@dataclass(frozen=True)
class Section:
    """One entry's slice of the shared file.

    `text` is everything from the heading to the line before the next entry —
    including the blank lines that separate them, so that reassembling a file
    from its sections is byte-exact.
    """

    name: str
    text: str
    start: int
    end: int

    @property
    def document(self) -> str:
        """The entry without its heading: a complete markdown document.

        The blank lines separating this entry from the next belong to the
        file's layout rather than to the entry, so they are dropped here and
        re-added by :func:`compose`. Without that symmetry every save would
        leave one more blank line behind than the last.
        """
        lines = self.text.splitlines(keepends=True)[1:]
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        return "".join(lines)


def heading_for(id_text: str, title: str | None = None) -> str:
    """The section's name, in the shape its filename would take."""
    trimmed = (title or "").strip()
    return f"{id_text}{TITLE_SEPARATOR}{trimmed}" if trimmed else id_text


def split(text: str, recognises: Callable[[str], bool]) -> tuple[str, list[Section]]:
    """The preamble, then every entry, in file order.

    `recognises` decides whether a heading names an entry. Everything before
    the first one it accepts is preamble and is never rewritten — a project
    puts a title and a note to its readers at the top of the file, and neither
    belongs to an entry.
    """
    lines = text.splitlines(keepends=True)
    starts: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        match = _HEADING.match(line.rstrip("\r\n"))
        if match and recognises(match.group("name")):
            starts.append((index, match.group("name")))

    if not starts:
        return text, []

    sections: list[Section] = []
    for position, (start, name) in enumerate(starts):
        end = starts[position + 1][0] if position + 1 < len(starts) else len(lines)
        sections.append(Section(name=name, text="".join(lines[start:end]),
                                start=start, end=end))
    return "".join(lines[:starts[0][0]]), sections


def newline_of(text: str) -> str:
    """The file's own line ending, which a write must not change."""
    return "\r\n" if "\r\n" in text[:4096] else "\n"


def compose(heading: str, document: str, newline: str = "\n") -> str:
    """One section's text, with the trailing blank line that separates it.

    Trailing blank lines in `document` collapse to that one separator, so that
    composing what :attr:`Section.document` returned reproduces the section it
    came from. This is the only byte of an entry that gets rewritten, and it is
    what makes saving an unchanged entity twice do nothing the second time.
    """
    body = document.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")
    text = f"## {heading}\n\n{body}\n\n"
    return text.replace("\n", newline) if newline != "\n" else text


def read(path: Path) -> str:
    """The file's text, or an empty document when it does not exist yet."""
    if not path.is_file():
        return ""
    return path.read_bytes().decode("utf-8", errors="replace")


def write(path: Path, text: str) -> None:
    """Bytes, not text: line endings are part of what stays untouched."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def _rebuild(preamble: str, sections: list[Section]) -> str:
    return preamble + "".join(section.text for section in sections)


def upsert(text: str, name_of: Callable[[str], str | None], id_text: str,
           heading: str, document: str) -> str:
    """Replace the entry named `id_text`, or append it when it is not there.

    `name_of` maps a heading to the id it names, or None when it names no
    entry — the one place this module needs to know how ids are spelled.
    """
    newline = newline_of(text) if text else "\n"
    preamble, sections = split(text, lambda n: name_of(n) is not None)
    replacement = compose(heading, document, newline)

    for position, section in enumerate(sections):
        if name_of(section.name) == id_text:
            sections[position] = Section(name=heading, text=replacement,
                                         start=section.start, end=section.end)
            return _rebuild(preamble, sections)

    body = _rebuild(preamble, sections)
    if body and not body.endswith(("\n", "\r")):
        body += newline
    return body + replacement


def remove(text: str, name_of: Callable[[str], str | None], id_text: str) -> str:
    """Drop one entry, leaving every other byte alone."""
    preamble, sections = split(text, lambda n: name_of(n) is not None)
    kept = [s for s in sections if name_of(s.name) != id_text]
    return _rebuild(preamble, kept)
