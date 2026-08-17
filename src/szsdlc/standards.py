"""Standards: conventions matched against a path and injected at that moment.

The problem being solved is measured, not hypothetical. The dominant token cost
in multi-agent pipelines is re-injecting the same standards into every
invocation, and the usual alternative — a conventions section in an instruction
file — loads in full every session no matter what is being touched, and grows
monotonically because nothing ever justifies deleting a line.

So a standard carries an `applies_to` glob and is returned only when an edited
path matches it. A convention that depends on the agent remembering to look it
up is not a convention, which is why the caller is a hook rather than a skill.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from . import frontmatter
from .config import Config


@dataclass(frozen=True)
class Standard:
    name: str
    path: Path
    applies_to: tuple[str, ...]
    body: str
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def _to_regex(pattern: str) -> re.Pattern[str]:
    """Translate a glob to a regex, with `**` spanning path separators.

    `fnmatch` would do for `templates/**`, but it lets a single `*` cross a
    separator too, which quietly makes `docs/*.md` match `docs/a/b.md`. The
    distinction is the whole point of writing a glob rather than a substring.
    """
    out = ["^"]
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if pattern.startswith("**/", index):
            out.append("(?:.*/)?")
            index += 3
        elif pattern.startswith("**", index):
            out.append(".*")
            index += 2
        elif char == "*":
            out.append("[^/]*")
            index += 1
        elif char == "?":
            out.append("[^/]")
            index += 1
        else:
            out.append(re.escape(char))
            index += 1
    out.append("$")
    return re.compile("".join(out))


def load(config: Config) -> list[Standard]:
    """Every standard, in name order. A broken one is represented, not raised."""
    directory = config.standards_dir
    if not (config.data.get("standards") or {}).get("enabled", True):
        return []
    if not directory.is_dir():
        return []

    found: list[Standard] = []
    for path in sorted(directory.glob("*.md")):
        document = frontmatter.parse(path.read_bytes().decode("utf-8", "replace"))
        if not document.ok:
            found.append(Standard(path.stem, path, (), "", document.error))
            continue
        raw = document.data.get("applies_to") or []
        if isinstance(raw, str):
            raw = [raw]
        found.append(Standard(
            name=str(document.data.get("name") or path.stem),
            path=path,
            applies_to=tuple(str(g) for g in raw),
            body=document.body.strip(),
        ))
    return found


def matching(config: Config, paths: list[str] | list[Path],
             standards: list[Standard] | None = None) -> list[Standard]:
    """Standards whose globs cover at least one of `paths`.

    Paths are matched project-relative, so a glob written once works whether
    the caller passes an absolute path from a hook or a relative one typed by
    hand.
    """
    standards = standards if standards is not None else load(config)
    relative = [_relative(config, path) for path in paths]

    matched: list[Standard] = []
    for standard in standards:
        if not standard.ok or not standard.applies_to:
            continue
        patterns = [_to_regex(glob) for glob in standard.applies_to]
        if any(pattern.match(candidate)
               for candidate in relative for pattern in patterns):
            matched.append(standard)
    return matched


def _relative(config: Config, path: str | Path) -> str:
    candidate = Path(path)
    try:
        if candidate.is_absolute():
            candidate = candidate.resolve().relative_to(config.root)
    except ValueError:
        # Outside the project: match against what was given rather than
        # silently matching nothing.
        return PurePosixPath(candidate.as_posix()).as_posix()
    return PurePosixPath(candidate.as_posix()).as_posix()
