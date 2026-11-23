"""szsdlc backend: where the project is kept, and why."""

from __future__ import annotations

import pytest

from szsdlc import backend
from szsdlc.errors import ConfigError

from backend.conftest import DIR, GIT, bare, configured, git, run, user, workdir, write_settings


# ---------------------------------------------------------------------------
# dir
# ---------------------------------------------------------------------------


def test_dir_shows_where_it_is(user, workdir, tmp_path, capsys):
    write_settings(user, {"SZSDLC_BACKEND": "dir", f"{DIR}PATH": str(tmp_path / "d")})
    assert run("backend") == 0
    out = capsys.readouterr().out
    assert "backend  dir" in out and "settings.json" in out and "not created yet" in out


# ---------------------------------------------------------------------------
# git: resolution
# ---------------------------------------------------------------------------


def test_backend_show_and_errors_redact_the_url(user, workdir, tmp_path, capsys):
    url = "https://ghp_secret@127.0.0.1:9/t.git"
    write_settings(user, {"SZSDLC_BACKEND": "git", f"{GIT}URL": url})
    chosen = backend.resolve(workdir)
    assert "ghp_secret" not in chosen.summary()
    assert run("backend") == 0
    assert "ghp_secret" not in capsys.readouterr().out
    with pytest.raises(ConfigError) as caught:
        backend.project_root(workdir)
    assert "ghp_secret" not in caught.value.render()


# ---------------------------------------------------------------------------
# Hooks and the backend command
# ---------------------------------------------------------------------------


def test_backend_show_names_every_source(configured, bare, user, workdir, capsys):
    write_settings(workdir / ".claude" / "settings.local.json", {f"{GIT}BRANCH": "main"})
    assert run("backend") == 0
    rows = {line.split()[0]: line for line in capsys.readouterr().out.splitlines()}
    assert rows["backend"].endswith(f"({user})")
    assert rows["url"].endswith(f"{bare}  ({user})")
    assert rows["branch"].endswith(f"main  ({workdir / '.claude' / 'settings.local.json'})")
    assert rows["subdir"].endswith(".  (default)")


def test_backend_show_defaults_to_workdir(workdir, capsys):
    assert run("backend", "show") == 0
    assert capsys.readouterr().out.strip() == "backend  workdir  (default)"


def test_backend_show_inside_a_store_with_nothing_selected(configured, workdir,
                                                           monkeypatch, capsys):
    run("init")
    monkeypatch.chdir(configured.store)
    monkeypatch.setenv("SZSDLC_BACKEND", "workdir")
    capsys.readouterr()
    assert run("backend") == 0
    assert "inside its store" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Third review round
# ---------------------------------------------------------------------------


def test_backend_show_never_prints_control_characters(user, workdir, capsys):
    write_settings(user, {"SZSDLC_BACKEND": "git", f"{GIT}URL": "/srv/\x1b[2Jt.git"})
    assert run("backend") == 0
    assert "\x1b" not in capsys.readouterr().out
