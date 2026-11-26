"""What a location names: the sdlc backend that claims it, and where it would keep the project.

A query, for an interface where a person types a location (the viewer's Open
project). Not the CLI's: a command already works from a folder and its settings.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..errors import BadInput
from ._registry import operation
from ._types import Data


@dataclass(frozen=True)
class LocationParams(Data):
    #: A repository URL such as `https://host/org/repo.git` or `git@host:org/repo.git#main`.
    location: str


@dataclass(frozen=True)
class LocationResult(Data):
    #: The sdlc backend type that claims the location.
    type: str
    #: The location, fit to show: credentials and query strings removed.
    location: str
    project_sdlc_store_dir: str
    project_sdlc_root: str
    #: Whether the project sdlc store dir exists on this computer yet, as a directory.
    materialized: bool


@operation("location", params=LocationParams, result=LocationResult, kind="query",
           interfaces=frozenset({"http"}), loads=False)
def location(params: LocationParams, where: str | None) -> LocationResult:
    """Nothing is created: whether to materialize the store dir is the caller's call."""
    from .. import backend as backend_module

    shown = backend_module.redact(params.location.strip())
    if params.location.strip() and backend_module.is_folder(params.location.strip()):
        raise BadInput(f"{shown} is a folder on this computer, not a location an sdlc backend keeps.",
                       fix="open the folder itself; its settings say which sdlc backend keeps it")
    found = backend_module.from_location(params.location)
    if found is None:
        raise BadInput(f"no sdlc backend reads {shown!r} as a location.",
                       fix="name a repository URL such as https://host/org/repo.git, or a folder on this computer")
    return LocationResult(found.type, shown, str(found.project_sdlc_store_dir),
                          str(found.project_sdlc_root), Path(found.project_sdlc_store_dir).is_dir())
