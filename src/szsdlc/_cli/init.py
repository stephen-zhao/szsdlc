"""szsdlc init: scaffold a project: config, directories, an empty roadmap."""

from __future__ import annotations

import argparse
from pathlib import Path

from .. import config as config_module
from ..errors import BadInput
from ._output import out


def add_parser(subparsers) -> None:
    init = subparsers.add_parser("init", prog="szsdlc init")
    init.add_argument("--name", default=None)
    init.set_defaults(run=run)


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


def run(args: argparse.Namespace) -> int:
    from .. import backend as backend_module

    # With a backend in effect the project goes wherever it keeps it, never
    # into the directory the command was run from.
    root = backend_module.project_root(args.project) or Path(args.project or Path.cwd()).resolve()
    config_path = config_module.config_path_for(root)
    if config_path.exists():
        raise BadInput(
            f"{config_path} already exists.",
            fix="szsdlc context",
        )

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_bytes(
        STARTER_CONFIG.format(name=args.name or root.name).encode("utf-8"))

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

    from ..roadmap import Roadmap

    for name in config.roadmaps:
        roadmap = Roadmap.load(config, name)
        roadmap.save()
        created.append(roadmap.path)

    for path in created:
        out(str(path.relative_to(root)).replace("\\", "/"))
    return 0
