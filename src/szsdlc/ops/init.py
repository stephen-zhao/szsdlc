"""szsdlc init: a new project, here or wherever the backend keeps it."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .. import config as config_module
from ..errors import BadInput, ConfigError
from ._paths import relative
from ._registry import operation
from ._types import Data


STARTER_CONFIG = """\
# szsdlc project configuration.
#
# This file is deep-merged over the built-in defaults, so everything below is
# optional and an empty file is already a valid project. Adjust one flag:
#
#   entity_types: {{spike: {{persistent: true}}}}
#
# drop a type you do not want (`entity_types: {{spike: null}}`), or replace a
# whole section outright (`entity_types: {{_replace: true, ...}}`).
project:
  name: {name}
"""


STARTER_CONFIG = """\
# szsdlc project configuration.
#
# This file is deep-merged over the built-in defaults, so everything below is
# optional and an empty file is already a valid project. Adjust one flag:
#
#   entity_types: {{spike: {{persistent: true}}}}
#
# drop a type you do not want (`entity_types: {{spike: null}}`), or replace a
# whole section outright (`entity_types: {{_replace: true, ...}}`).
project:
  name: {name}
"""


@dataclass(frozen=True)
class InitParams(Data):
    name: str | None = None


@dataclass(frozen=True)
class InitResult(Data):
    root: str
    #: What was made, relative to the root: the config, then each directory.
    created: tuple[str, ...]


@operation("init", params=InitParams, result=InitResult, kind="mutation",
           interfaces=frozenset({"cli"}), loads=False)
def init(params: InitParams, where: str | None) -> InitResult:
    from .. import backend as backend_module
    from ..roadmap import Roadmap

    # With a backend in effect the project goes wherever it keeps it, never
    # into the directory the command was run from.
    root = backend_module.project_sdlc_root(where) or Path(where or Path.cwd()).resolve()
    config_path = config_module.config_path_for(root)
    if config_path.exists():
        raise BadInput(
            f"{config_path} already exists.",
            fix="szsdlc context",
        )

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_bytes(STARTER_CONFIG.format(name=params.name or root.name).encode("utf-8"))
    # Recorded now, before anything reads the project: a store is recorded
    # once it holds a real project (WI-0067), and from inside it the record
    # is what finds it.
    try:
        backend_module.project_sdlc_root(where)
    except (ConfigError, OSError):
        pass  # the config exists now: the next command records it, and reports the record

    config = config_module.load(root)
    created = [config_path]
    for entity_type in config.entity_types.values():
        directory = config.dir_for(entity_type)
        directory.mkdir(parents=True, exist_ok=True)
        created.append(directory)
    for directory in (config.roadmaps_dir, config.views_dir, config.records_dir,
                      config.standards_dir, config.templates_dir):
        directory.mkdir(parents=True, exist_ok=True)
        created.append(directory)
    for name in config.roadmaps:
        roadmap = Roadmap.load(config, name)
        roadmap.save()
        created.append(roadmap.path)
    return InitResult(str(root), tuple(relative(root, path) for path in created))
