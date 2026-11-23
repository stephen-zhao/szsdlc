"""The operation registry: every operation by name, with its shapes and kind.

An interface reads this rather than knowing operations one by one. The CLI's
parity test checks every command against it, and IDEA-0021's HTTP routes
would be generated from it.
"""

from __future__ import annotations

import dataclasses
import typing
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from ..errors import BadInput
from ._project import Project

#: The interfaces an operation can belong to. `init` is the CLI's alone: over
#: HTTP there is no project to attach to until one exists.
INTERFACES = frozenset({"cli", "http"})


@dataclass(frozen=True)
class Operation:
    name: str
    #: `(params, project) -> result`; for an operation that does not load a
    #: project (`loads=False`), the second argument is the path to start from.
    run: Callable[[Any, Any], Any]
    params: type
    result: type
    kind: Literal["query", "mutation"]
    interfaces: frozenset[str] = INTERFACES
    #: False for the operations that act on where a project is kept rather
    #: than on a loaded project: init, sync, refresh, backend.
    loads: bool = True

    def schema(self) -> dict[str, Any]:
        """Each params field with its type, as written, and whether it is required."""
        hints = typing.get_type_hints(self.params)
        return {f.name: {"type": _type_name(hints.get(f.name, Any)),
                         "required": f.default is dataclasses.MISSING
                         and f.default_factory is dataclasses.MISSING}
                for f in dataclasses.fields(self.params)}


#: Name → operation, in the order the modules register them.
OPERATIONS: dict[str, Operation] = {}


def operation(name: str, *, params: type, result: type,
              kind: Literal["query", "mutation"],
              interfaces: frozenset[str] = INTERFACES, loads: bool = True):
    """Register the decorated function as the operation `name`."""
    def register(run: Callable[[Any, Any], Any]) -> Callable[[Any, Any], Any]:
        if name in OPERATIONS:  # pragma: no cover - a programming error
            raise RuntimeError(f"operation {name!r} registered twice")
        OPERATIONS[name] = Operation(name, run, params, result, kind,
                                     frozenset(interfaces), loads)
        return run
    return register


def build_params(op: Operation, given: Mapping[str, Any] | Any) -> Any:
    """`given` as `op`'s params dataclass, refusing unknown or missing keys."""
    if isinstance(given, op.params):
        return given
    if not isinstance(given, Mapping):
        raise BadInput(f"{op.name}: params must be a mapping or {op.params.__name__}.",
                       fix=f"pass a dict of {', '.join(op.schema()) or 'no params'}")
    fields = {f.name for f in dataclasses.fields(op.params)}
    unknown = sorted(set(given) - fields)
    if unknown:
        raise BadInput(f"{op.name}: no param {unknown[0]!r}.",
                       fix=f"use {', '.join(sorted(fields)) or 'no params'}")
    missing = [n for n, spec in op.schema().items() if spec["required"] and n not in given]
    if missing:
        raise BadInput(f"{op.name}: {missing[0]!r} is required.",
                       fix=f"pass {', '.join(missing)}")
    hints = typing.get_type_hints(op.params)
    values = {k: (tuple(v) if isinstance(v, list) and _is_tuple(hints.get(k)) else v)
              for k, v in given.items()}
    return op.params(**values)


def call(name: str, params: Mapping[str, Any] | Any | None = None,
         project: Project | str | None = None) -> Any:
    """Run the operation `name` in-process and return its typed result.

    `params` is a mapping or the operation's params dataclass. `project` is a
    loaded :class:`Project`, a path to start from, or None for the current
    directory. A refusal is raised as the `szsdlc.errors` class it is.
    """
    op = OPERATIONS.get(name)
    if op is None:
        from ..text import nearest
        suggestion = nearest(name, OPERATIONS)
        raise BadInput(f"no operation {name!r}" + (f"; did you mean {suggestion}?" if suggestion else "."),
                       fix=f"use one of: {', '.join(OPERATIONS)}")
    built = build_params(op, params or {})
    if not op.loads:
        where = str(project.config.root) if isinstance(project, Project) else project
        return op.run(built, where)
    if not isinstance(project, Project):
        project = Project(project)
    return op.run(built, project)


def _is_tuple(hint: Any) -> bool:
    return typing.get_origin(hint) is tuple or any(
        typing.get_origin(a) is tuple for a in typing.get_args(hint))


def _type_name(hint: Any) -> str:
    return getattr(hint, "__name__", None) if isinstance(hint, type) else str(hint).replace("typing.", "")
