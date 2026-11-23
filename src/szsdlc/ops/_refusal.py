"""Refusals as data: `{class, problem, fix: {op, params} | None, see}`.

The refusal classes are `szsdlc.errors`' own (each names its
``refusal_class``), so every refusal the model raises is already typed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..errors import SzsdlcError
from ._types import Data


@dataclass(frozen=True)
class Call(Data):
    """An operation and its params: a fix a caller can run as it stands."""

    op: str
    params: dict[str, Any] = field(default_factory=dict)


def fix_call(text: str | None) -> Call | None:
    """The fix `text` as a call, or None when it is not one an operation can run.

    None when the fix is not an szsdlc command (``edit … in
    .szsdlc/config.yml``) or holds a ``<placeholder>`` the caller must fill.

    Fixes are still authored as CLI text, and REQ-0012 holds that text byte
    for byte, so the call is parsed from it — through the CLI's own parser,
    which is the one place that grammar lives. That is this layer's single
    reach into `szsdlc._cli`, imported here and nowhere else; IDEA-0023 is to
    author fixes as calls and render the text from them instead.
    """
    if not text:
        return None
    from .._cli import parse_call

    parsed = parse_call(text)
    return None if parsed is None else Call(*parsed)


def refusal_to_data(exc: BaseException) -> dict[str, Any]:
    """A refusal, or any other exception, in the interface-neutral shape."""
    if isinstance(exc, SzsdlcError):
        call = fix_call(exc.fix)
        return {"class": exc.refusal_class, "problem": exc.problem,
                "fix": call.to_dict() if call else None, "see": exc.see}
    return {"class": "internal", "problem": str(exc), "fix": None, "see": None}
