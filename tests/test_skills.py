"""The skills are prose, so nothing but a test keeps them true.

Two failure modes matter here and neither is caught by reading:

1. **Drift.** A skill tells the model to run `szsdlc groom WI-1` long after
   that command was renamed. The model then burns a turn on a usage error and
   trusts the next instruction less. Every `szsdlc …` line in every skill is
   parsed by the real parser below.
2. **Bloat.** The budget is 50 lines because a skill is read in full, by a
   model, on every invocation. Budgets that are not asserted are aspirations.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

import pytest
import yaml

from szsdlc.cli import build_parser

SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills"

#: Named in the plan's file map. Listed rather than globbed so that deleting a
#: skill is a decision someone makes here, not a silent shrinking of the set.
EXPECTED = {
    "capture", "refine", "spike", "design", "plan",
    "execute", "close", "decide", "roadmap", "trace",
}

#: Mutating skills the model must not invoke on its own initiative. Both write
#: to the project *and* to the codebase, so they are typed deliberately.
MUST_NOT_SELF_INVOKE = {"execute", "close"}

LINE_BUDGET = 50

#: `szsdlc …` as it appears in prose: fenced, inline in backticks, or bare.
COMMAND_LINE = re.compile(r"szsdlc [^`\n]+")

#: Placeholders in example commands — a real value goes here, and shlex would
#: otherwise hand the parser a literal `<tag>`.
PLACEHOLDER = re.compile(r"<[^>]+>")

#: Trailing `# what this does` in a fenced example. Stripped before parsing,
#: since the shell would not pass it either.
TRAILING_COMMENT = re.compile(r"\s+#.*$")


def skill_paths() -> list[Path]:
    return sorted(SKILLS_DIR.glob("*/SKILL.md"))


def frontmatter(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{path.parent.name}: no frontmatter"
    _, block, body = text.split("---\n", 2)
    return yaml.safe_load(block), body


@pytest.fixture(params=skill_paths(), ids=lambda p: p.parent.name)
def skill(request) -> Path:
    return request.param


# ---------------------------------------------------------------------------
# The set itself
# ---------------------------------------------------------------------------


def test_every_planned_skill_exists():
    assert {p.parent.name for p in skill_paths()} == EXPECTED


# ---------------------------------------------------------------------------
# Frontmatter
# ---------------------------------------------------------------------------


def test_name_matches_its_directory(skill):
    # The directory is what `/szsdlc:<name>` resolves through; a mismatch
    # gives one name in the listing and another in the invocation.
    meta, _ = frontmatter(skill)
    assert meta["name"] == skill.parent.name


def test_description_says_when_to_use_it(skill):
    """The description is the only part loaded before invocation.

    It is what the model matches against, so it has to name the situation
    rather than restate the title.
    """
    meta, _ = frontmatter(skill)
    description = meta["description"].lower()
    assert len(description) >= 80, "too thin to match against"
    assert "when" in description, "names no situation"


def test_only_the_mutating_skills_refuse_self_invocation(skill):
    meta, _ = frontmatter(skill)
    disabled = bool(meta.get("disable-model-invocation", False))
    assert disabled == (skill.parent.name in MUST_NOT_SELF_INVOKE)


def test_frontmatter_carries_nothing_unrecognised(skill):
    meta, _ = frontmatter(skill)
    assert set(meta) <= {"name", "description", "disable-model-invocation",
                         "allowed-tools"}


# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------


def test_within_the_line_budget(skill):
    lines = skill.read_text(encoding="utf-8").splitlines()
    assert len(lines) <= LINE_BUDGET, f"{len(lines)} lines"


def test_procedure_did_not_migrate_out_of_the_cli(skill):
    """A skill that lists ten commands has become a shell script.

    The split the whole design rests on is that the CLI holds procedure and
    the skill holds judgment. Command count is the cheapest proxy for a
    skill drifting across that line.
    """
    body = frontmatter(skill)[1]
    assert len(COMMAND_LINE.findall(body)) <= 12


# ---------------------------------------------------------------------------
# Drift against the real parser
# ---------------------------------------------------------------------------


def commands_in(path: Path) -> list[str]:
    return [TRAILING_COMMENT.sub("", line).rstrip(".")
            for line in COMMAND_LINE.findall(frontmatter(path)[1])]


def test_every_command_mentioned_actually_parses(skill):
    parser = build_parser()
    for line in commands_in(skill):
        argv = shlex.split(PLACEHOLDER.sub("PLACEHOLDER", line))[1:]
        if not argv:
            continue
        try:
            parser.parse_args(argv)
        except SystemExit:  # pragma: no cover - the failure message is the point
            pytest.fail(f"{skill.parent.name}: `{line}` does not parse")


def test_skills_are_cross_referenced_by_slash_name(skill):
    """A cross-reference to a skill that does not exist is a dead end."""
    body = frontmatter(skill)[1]
    for name in re.findall(r"/szsdlc:([a-z-]+)", body):
        assert name in EXPECTED, name


def test_no_skill_names_a_command_that_writes_without_saying_so():
    """`sync` is the hook's job, not a skill's.

    Views are regenerated on every edit already. A skill that tells the model
    to run `sync` teaches a habit that is redundant at best and, when the
    model runs it instead of `validate`, actively hides findings.
    """
    for path in skill_paths():
        for line in commands_in(path):
            assert not line.startswith("szsdlc sync"), path.parent.name
