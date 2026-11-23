"""Operations: szsdlc's supported in-process API.

Every command is an operation here first. An operation takes typed params
and returns a typed result, or raises a typed refusal; it never prints,
never reads argv or stdin, and knows nothing of HTTP. The CLI (`szsdlc._cli`)
is one interface on top; anything else is another.

    from szsdlc import ops

    shown = ops.call("show", {"ref": "WI-0042"}, "path/to/project")
    ops.call("set", {"ref": "WI-0042", "assignments": ["status=ready"],
                     "expect": shown.hash}, "path/to/project")

`OPERATIONS` names every operation with its params and result types, its
kind (query or mutation) and the interfaces it belongs to. A refusal is one
of `szsdlc.errors`' classes; `refusal_to_data` gives it as
`{class, problem, fix: {op, params} | None, see}`. See docs/reference.md.
"""

from __future__ import annotations

from ..errors import (BadInput, ConfigError, InternalError, Refused, StalePrecondition,
                      SzsdlcError, UnknownReference, ValidationFailed)
from ._project import Project
from ._refusal import Call, fix_call, refusal_to_data
from ._registry import OPERATIONS, Operation, call
# Imported for their registrations, in the CLI's order.
from . import entities, queries, store  # noqa: F401  isort: skip

__all__ = [
    "OPERATIONS", "Operation", "Project", "call", "Call", "fix_call", "refusal_to_data",
    "SzsdlcError", "BadInput", "UnknownReference", "Refused", "StalePrecondition",
    "ValidationFailed", "ConfigError", "InternalError",
    "entities", "queries", "store",
]
