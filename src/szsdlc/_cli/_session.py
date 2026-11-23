"""The project a command runs against: `szsdlc.ops.Project`, under its CLI name.

Kept as its own module because the CLI looks the class up here at call time,
which is what lets a test replace it (`szsdlc._cli._session.Session`).
"""

from __future__ import annotations

from ..ops._project import Project as Session

__all__ = ["Session"]
