"""The suite does not depend on the developer's szsdlc settings (WI-0052).

Checked from outside, in a subprocess with a hostile environment: inside this
session the conftest has already cleaned it, so an in-process check could not
fail. The probe below runs only in that subprocess.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from szsdlc import settings

ROOT = Path(__file__).resolve().parents[1]
PROBE_FLAG = "ISOLATION_PROBE"


@pytest.mark.skipif(not os.environ.get(PROBE_FLAG), reason="run by the test below")
def test_probe_no_setting_reaches_a_test(tmp_path):
    """No layer leaks: not the environment, not the user's settings.json, and
    not the checkout's own `.claude/settings.json` found from the working
    directory. A bare project (no settings of its own) sees nothing."""
    assert settings.load(tmp_path) == {}
    assert settings.load() == {}


def test_nothing_from_the_developer_reaches_the_suite(tmp_path):
    hostile = {
        "SZSDLC_BACKEND": "git",
        # A file URL that does not exist: nothing can reach a network.
        "SZSDLC_BACKEND_GIT_URL": (tmp_path / "nowhere.git").as_uri(),
        "SZSDLC_BACKEND_GIT_BRANCH": "main",
    }
    claude_dir = tmp_path / "claude"
    claude_dir.mkdir()
    (claude_dir / "settings.json").write_text(json.dumps({"env": hostile}), encoding="utf-8")

    env = dict(os.environ, **hostile, CLAUDE_CONFIG_DIR=str(claude_dir), **{PROBE_FLAG: "1"})
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-rs",
         str(ROOT / "tests" / "test_example.py"),
         f"{Path(__file__)}::test_probe_no_setting_reaches_a_test"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
    output = result.stdout[-3000:] + result.stderr[-2000:]
    assert result.returncode == 0, output
    assert "skipped" not in result.stdout, output  # the probe really ran
