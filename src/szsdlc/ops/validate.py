"""szsdlc validate: every consistency rule, as findings."""

from __future__ import annotations

from dataclasses import dataclass

from . import _queries
from ._listing import DEFAULT_LIMIT, bounded
from ._project import Project
from ._registry import operation
from ._types import Data


@dataclass(frozen=True)
class FindingRow(Data):
    level: str
    kind: str
    ref: str
    message: str
    fix: str | None


@dataclass(frozen=True)
class ValidateParams(Data):
    limit: int = DEFAULT_LIMIT


@dataclass(frozen=True)
class ValidateResult(Data):
    """Findings, most severe first. Errors and warnings count all of them, not just those shown."""

    rows: tuple[FindingRow, ...]
    total: int
    errors: int
    warnings: int

    @property
    def ok(self) -> bool:
        return self.errors == 0


@operation("validate", params=ValidateParams, result=ValidateResult, kind="query")
def validate(params: ValidateParams, project: Project) -> ValidateResult:
    from .. import validate as validate_module

    findings = validate_module.run(project.config, project.store, _queries.graph(project),
                                   _queries.roadmaps(project))
    errors, warnings = validate_module.summary(findings)
    rows, total = bounded([FindingRow(f.level, f.kind, f.ref, f.message, f.fix) for f in findings],
                          params.limit)
    return ValidateResult(rows, total, errors, warnings)
