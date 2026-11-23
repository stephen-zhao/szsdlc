"""What every params and result dataclass shares: a JSON-safe `to_dict()`."""

from __future__ import annotations

import dataclasses
import datetime as dt
from pathlib import Path
from typing import Any


def plain(value: Any) -> Any:
    """Dates, paths and tuples as JSON has them; everything else as it is."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: plain(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [plain(v) for v in value]
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    if isinstance(value, Path):
        return value.as_posix()
    return value


class Data:
    """Mixed into every params and result dataclass."""

    def to_dict(self) -> dict[str, Any]:
        return plain(self)
