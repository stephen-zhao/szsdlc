"""The suite does not depend on the developer's szsdlc settings (WI-0052).

Checked from outside, in a subprocess: inside this session the conftest has
already cleaned the environment, so an in-process test could not fail.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_the_example_loads_as_a_directory_whatever_the_developer_selected(tmp_path):
    hostile = {
        "SZSDLC_BACKEND": "git",
        # A file URL that does not exist: nothing can reach a network.
        "SZSDLC_BACKEND_GIT_URL": (tmp_path / "nowhere.git").as_uri(),
        "SZSDLC_BACKEND_GIT_BRANCH": "main",
    }
    claude_dir = tmp_path / "claude"
    claude_dir.mkdir()
    (claude_dir / "settings.json").write_text(json.dumps({"env": hostile}), encoding="utf-8")

    env = dict(os.environ, **hostile, CLAUDE_CONFIG_DIR=str(claude_dir))
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         str(ROOT / "tests" / "test_example.py")],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
