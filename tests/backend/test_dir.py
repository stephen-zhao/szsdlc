"""szsdlc.backend.dir: the project in another local directory."""

from __future__ import annotations

import pytest

from szsdlc import backend
from szsdlc.errors import ConfigError

from .conftest import DIR

from .conftest import DIR, run, write_settings


# ---------------------------------------------------------------------------
# dir
# ---------------------------------------------------------------------------
def test_dir_writes_straight_into_another_directory(user, workdir, tmp_path, capsys):
    store = tmp_path / "trackers"
    write_settings(user, {"SZSDLC_BACKEND": "dir", f"{DIR}PATH": str(store / "{project}")})
    assert run("init") == 0
    assert run("capture", "kept elsewhere") == 0
    assert list(workdir.iterdir()) == []
    assert (store / "myproj" / ".szsdlc" / "config.yml").is_file()
    # Nothing to sync, ever: the files are already where they are kept.
    assert backend.sync_at(workdir, "x", only_if_changed=True) is None
    report = backend.sync_at(workdir, "x")
    assert report.ok and not report.incoming and not report.outgoing


def test_a_relative_dir_path_resolves_against_the_project(workdir):
    write_settings(workdir / ".claude" / "settings.json",
                   {"SZSDLC_BACKEND": "dir", f"{DIR}PATH": "../tracking"})
    assert backend.resolve(workdir).root == workdir.parent / "tracking"


def test_dir_refuses_a_file(user, workdir, tmp_path):
    (tmp_path / "f").write_text("x")
    write_settings(user, {"SZSDLC_BACKEND": "dir", f"{DIR}PATH": str(tmp_path / "f")})
    with pytest.raises(ConfigError, match="not a directory"):
        backend.project_root(workdir)
