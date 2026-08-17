"""Surgical frontmatter editing: change one field, leave every other byte alone.

The whole framework rests on frontmatter being the *only* hand-authored state,
which means the CLI writes to files people also write to. A round-trip through
`yaml.safe_load` and `yaml.safe_dump` would silently reorder keys, drop every
comment, refold long strings and rewrite the body. Doing that on every `set`
would teach users not to hand-edit — and hand-editing is the point.

So this module does not round-trip the document. It locates the *block of lines*
belonging to one top-level key and replaces exactly those lines, leaving the
rest of the file — including its line endings, its comments and its body —
untouched at the byte level.

Two deliberate limits:

- Only top-level keys are addressable. Frontmatter is one level deep apart from
  `relations`, which is replaced as a whole block.
- Comments *inside* a replaced block are lost. Comments above a key are not:
  trailing blank and comment lines are attributed to the key that follows,
  which is what a reader assumes they belong to.
"""

from __future__ import annotations

import datetime as _dt
import re
from typing import Any

import yaml

_DELIMITER = re.compile(r"^(---|\.\.\.)[ \t]*$")
_TOP_LEVEL_KEY = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*)[ \t]*:(?:[ \t]|$)")
_BLANK_OR_COMMENT = re.compile(r"^[ \t]*(#.*)?$")


class _IndentedDumper(yaml.SafeDumper):
    """Indent list items under their key, which PyYAML declines to do."""

    def increase_indent(self, flow: bool = False, indentless: bool = False):  # noqa: D102
        return super().increase_indent(flow, False)


def _dump_value(value: Any, *, flow: bool) -> str:
    return yaml.dump(
        value,
        Dumper=_IndentedDumper,
        default_flow_style=flow,
        allow_unicode=True,
        sort_keys=False,
        width=10**6,
    ).strip()


def _dump_pair(key: str, value: Any, *, flow: bool) -> str:
    """One `key: value` entry, block or flow, without a trailing newline."""
    if isinstance(value, (list, tuple)) and flow:
        return f"{key}: {_dump_value(list(value), flow=True)}"
    if isinstance(value, (list, tuple)) and not value:
        return f"{key}: []"
    if isinstance(value, dict) and not value:
        return f"{key}: {{}}"
    text = yaml.dump(
        {key: value},
        Dumper=_IndentedDumper,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
        width=10**6,
    )
    return text.rstrip("\n")


def jsonify(value: Any) -> Any:
    """Dates become ISO strings so JSON Schema can see them.

    YAML parses `2026-08-16` into a `date` object, which is the right thing for
    logic and the wrong thing for a validator that only knows JSON types.
    """
    if isinstance(value, dict):
        return {str(k): jsonify(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonify(v) for v in value]
    if isinstance(value, (_dt.date, _dt.datetime)):
        return value.isoformat()
    return value


class FrontmatterError(Exception):
    """A document whose frontmatter cannot be read. Represented, never raised
    past the model: one corrupt file must not stop the other 199 loading."""


class Document:
    """A markdown file split into frontmatter and body, editable in place."""

    def __init__(self, text: str):
        self.original = text
        self.newline = "\r\n" if "\r\n" in text[:4096] else "\n"
        self.error: str | None = None
        self._lines: list[str] = []
        self._open = f"---{self.newline}"
        self._close = f"---{self.newline}"
        self.body = ""
        self.data: dict[str, Any] = {}
        self._parse(text)

    # -- parsing ------------------------------------------------------------

    def _parse(self, text: str) -> None:
        lines = text.splitlines(keepends=True)
        if not lines or not _DELIMITER.match(lines[0].rstrip("\r\n")):
            self.error = "no YAML frontmatter: the file must open with ---"
            self.body = text
            return

        close_at = None
        for index in range(1, len(lines)):
            if _DELIMITER.match(lines[index].rstrip("\r\n")):
                close_at = index
                break
        if close_at is None:
            self.error = "frontmatter is never closed: no matching --- delimiter"
            self.body = text
            return

        self._open = lines[0]
        self._close = lines[close_at]
        self._lines = lines[1:close_at]
        self.body = "".join(lines[close_at + 1:])

        try:
            loaded = yaml.safe_load("".join(self._lines))
        except yaml.YAMLError as exc:
            mark = getattr(exc, "problem_mark", None)
            where = f" at line {mark.line + 2}" if mark else ""
            problem = getattr(exc, "problem", None) or "invalid YAML"
            self.error = f"frontmatter{where}: {problem}"
            return

        if loaded is None:
            loaded = {}
        if not isinstance(loaded, dict):
            self.error = (
                f"frontmatter must be a mapping, not {type(loaded).__name__}"
            )
            return
        self.data = loaded

    @property
    def ok(self) -> bool:
        return self.error is None

    # -- block index --------------------------------------------------------

    def _blocks(self) -> dict[str, tuple[int, int]]:
        """key -> [start, end) over the frontmatter lines."""
        starts: list[tuple[str, int]] = []
        for index, line in enumerate(self._lines):
            # Strip the line ending first: `relations:\r\n` has a carriage
            # return where the pattern expects whitespace or end-of-line, so a
            # valueless key would go unrecognised in every CRLF file.
            match = _TOP_LEVEL_KEY.match(line.rstrip("\r\n"))
            if match:
                starts.append((match.group(1), index))

        blocks: dict[str, tuple[int, int]] = {}
        for position, (key, start) in enumerate(starts):
            hard_end = starts[position + 1][1] if position + 1 < len(starts) else len(self._lines)
            # Blank and comment lines immediately before the next key introduce
            # it; they are not a trailing part of this block.
            end = hard_end
            while end > start + 1 and _BLANK_OR_COMMENT.match(self._lines[end - 1].rstrip("\r\n")):
                end -= 1
            blocks[key] = (start, end)
        return blocks

    def _is_flow(self, key: str) -> bool:
        blocks = self._blocks()
        if key not in blocks:
            return False
        start, end = blocks[key]
        if end - start != 1:
            return False
        _, _, rest = self._lines[start].partition(":")
        return rest.lstrip().startswith("[")

    def _as_lines(self, text: str) -> list[str]:
        return [line + self.newline for line in text.split("\n")]

    # -- editing ------------------------------------------------------------

    def set(self, key: str, value: Any, *, flow: bool | None = None) -> None:
        """Set one top-level key, rewriting only its own lines."""
        if not self.ok:
            raise FrontmatterError(self.error or "unparseable")

        if flow is None:
            flow = self._is_flow(key) if key in self.data else isinstance(value, (list, tuple))

        rendered = self._as_lines(_dump_pair(key, value, flow=flow))
        blocks = self._blocks()
        if key in blocks:
            start, end = blocks[key]
            self._lines[start:end] = rendered
        else:
            self._lines.extend(rendered)
        self.data[key] = value

    def unset(self, key: str) -> None:
        if not self.ok:
            raise FrontmatterError(self.error or "unparseable")
        blocks = self._blocks()
        if key in blocks:
            start, end = blocks[key]
            del self._lines[start:end]
        self.data.pop(key, None)

    # -- rendering ----------------------------------------------------------

    def render(self) -> str:
        if not self.ok:
            return self.original
        return self._open + "".join(self._lines) + self._close + self.body


def parse(text: str) -> Document:
    return Document(text)
