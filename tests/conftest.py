"""Shared fixtures: a real project on disk, built through the same config the
CLI uses, so no test invents a layout the framework does not actually produce.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from szsdlc import config as C
from szsdlc.ids import IdSpace


@pytest.fixture
def make_project(tmp_path):
    """Build a project rooted at tmp_path and return its Config."""

    def build(override=None, raw=None, root: Path | None = None):
        root = root or tmp_path
        cfg_dir = root / C.CONFIG_DIRNAME
        cfg_dir.mkdir(parents=True, exist_ok=True)
        text = raw if raw is not None else yaml.safe_dump(override or {}, sort_keys=False)
        (cfg_dir / C.CONFIG_FILENAME).write_text(text, encoding="utf-8")
        cfg = C.load(root)
        for entity_type in cfg.entity_types.values():
            cfg.dir_for(entity_type).mkdir(parents=True, exist_ok=True)
        return cfg

    return build


@pytest.fixture
def project(make_project):
    return make_project()


@pytest.fixture
def write_entity(project):
    """Write one entity to disk in whatever layout its type declares.

    Returns the entity's directory for a `directory` type and its file for a
    `file` type — the same thing `IdSpace.scan` reports.
    """

    def write(type_name: str, number: int, frontmatter: str = "", body: str = "",
              *, slug: str = "thing", artifacts: dict[str, str] | None = None,
              raw: str | None = None) -> Path:
        entity_type = project.type_for(type_name)
        ids = IdSpace(project)
        entity_id = ids.make(entity_type.prefix, number)
        name = f"{entity_id.text}-{slug}" if slug else entity_id.text

        text = raw if raw is not None else (
            f"---\nid: {entity_id.text}\n{frontmatter}---\n{body}"
        )

        # Bytes, not text: `write_text` translates newlines per platform, which
        # would make every fixture CRLF on Windows and silently change what
        # these tests are asserting about. CRLF has its own coverage in
        # test_frontmatter.py, by design rather than by accident.
        if entity_type.is_directory_layout:
            home = project.dir_for(entity_type) / name
            home.mkdir(parents=True, exist_ok=True)
            (home / project.entity_filename).write_bytes(text.encode("utf-8"))
            for artifact_name, artifact_text in (artifacts or {}).items():
                (home / artifact_name).write_bytes(artifact_text.encode("utf-8"))
            return home

        path = project.dir_for(entity_type) / f"{name}.md"
        path.write_bytes(text.encode("utf-8"))
        return path

    return write
