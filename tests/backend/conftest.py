"""What the backend tests share: settings files, a bare git remote on disk, and the CLI.

The git remote is a bare repository on disk, which exercises the real backend end
to end without a network. tests/_cli imports these too, for its sync, refresh and
backend command tests.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from szsdlc import backend, cli

GIT = "SZSDLC_BACKEND_GIT_"
DIR = "SZSDLC_BACKEND_DIR_"


def git(*args: str, cwd: Path | None = None) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True).stdout


def write_settings(path: Path, env: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"env": env}), encoding="utf-8")


@pytest.fixture
def bare(tmp_path) -> Path:
    path = tmp_path / "remote.git"
    git("init", "--bare", "--quiet", "--initial-branch=main", str(path))
    return path


@pytest.fixture
def workdir(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "work" / "myproj"
    path.mkdir(parents=True)
    monkeypatch.chdir(path)
    return path


@pytest.fixture
def user(isolated_settings) -> Path:
    return isolated_settings / "settings.json"


def remote_files(bare: Path, branch: str = "main") -> list[str]:
    return git("--git-dir", str(bare), "ls-tree", "-r", "--name-only", branch).split()


def last_commit(bare: Path, branch: str = "main") -> str:
    return git("--git-dir", str(bare), "log", "-1", "--format=%s", branch).strip()


def run(*argv: str) -> int:
    return cli.main(list(argv))


@pytest.fixture
def configured(user, bare, workdir):
    write_settings(user, {"SZSDLC_BACKEND": "git", f"{GIT}URL": str(bare)})
    return backend.resolve(workdir)
