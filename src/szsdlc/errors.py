"""The single error channel for everything a user or agent can get wrong.

Two rules from the command surface contract (docs/plan.md) shape this module,
and both exist because every message here is read by an agent that pays tokens
for it:

**C2 — a refusal names the fix.** Every error carries a runnable ``fix``, so the
second call *is* the correction rather than a guess. Where no single fix exists,
``fix`` names the diagnostic command that reveals the answer.

**C7 — stderr is bounded and machine-shaped.** At most three lines, never a
traceback, and a distinct exit code per class so a caller can branch without
parsing text.

The distinction the plan draws between *project* invalidity and *model*
invalidity lives here too: :class:`SzsdlcError` describes something the user
did, while :class:`InternalError` describes a broken model invariant — a bug in
this program, never a finding about the project.
"""

from __future__ import annotations

# Exit codes are part of the contract: a caller branches on these, not on text.
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_BAD_INPUT = 2
EXIT_REFUSED = 3
EXIT_INVALID = 4
EXIT_CONFIG = 5


class SzsdlcError(Exception):
    """A refusal aimed at whoever made the call. Rendered in the C2 shape."""

    exit_code = EXIT_BAD_INPUT

    def __init__(self, problem: str, fix: str | None = None, see: str | None = None):
        self.problem = problem
        self.fix = fix
        self.see = see
        super().__init__(problem)

    def render(self) -> str:
        """The stderr text: problem, then Fix, then an optional See. Max 3 lines."""
        lines = [self.problem]
        if self.fix:
            lines.append(f"Fix: {self.fix}")
        if self.see:
            lines.append(f"See: {self.see}")
        return "\n".join(lines)


class BadInput(SzsdlcError):
    """A malformed argument, an unknown reference, an unparseable value."""

    exit_code = EXIT_BAD_INPUT


class Refused(SzsdlcError):
    """A well-formed request a gate or workflow rule declines to perform."""

    exit_code = EXIT_REFUSED


class ValidationFailed(SzsdlcError):
    """`validate` found errors. Reserved for that command's own exit path."""

    exit_code = EXIT_INVALID


class ConfigError(SzsdlcError):
    """`.szsdlc/config.yml` is missing, unparseable, or internally inconsistent.

    Its own exit code, because a caller that can retry a bad reference cannot
    retry a broken project configuration.
    """

    exit_code = EXIT_CONFIG


class InternalError(Exception):
    """A model invariant was violated — a szsdlc bug, not a project finding.

    Kept off the :class:`SzsdlcError` hierarchy on purpose: the plan requires
    that these two never be conflated in output, and inheritance is exactly how
    they would get conflated.
    """
