"""The public listing of project sdlc stores (WI-0063): `szsdlc.backend.project_sdlc_stores()`,
the `project_sdlc_stores` operation and `szsdlc backend list`. Reads only.

Every test runs with szsdlc's home isolated (`isolated_settings`).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from backend.conftest import GIT, git, run, write_settings
from szsdlc import backend, ops
from szsdlc.backend.dir import DirBackend


@pytest.fixture
def home(isolated_settings):
    return isolated_settings


@pytest.fixture
def remote(tmp_path) -> Path:
    path = tmp_path / "remote.git"
    git("init", "--bare", "--quiet", "--initial-branch=main", str(path))
    return path


def asset_root(tmp_path: Path, name: str, env: dict | None = None) -> Path:
    path = tmp_path / name
    path.mkdir(parents=True)
    if env is not None:
        write_settings(path / ".claude" / "settings.json", env)
    return path


def init(monkeypatch, where: Path) -> None:
    monkeypatch.chdir(where)
    assert run("init") == 0


def write_record(text: str) -> Path:
    path = backend._project_sdlc_stores_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_nothing_recorded_lists_nothing(home, tmp_path, monkeypatch, capsys):
    assert backend.project_sdlc_stores() == []
    assert ops.call("project_sdlc_stores", {}, None).stores == ()
    monkeypatch.chdir(tmp_path)
    capsys.readouterr()
    assert run("backend", "list") == 0
    assert capsys.readouterr().out == ""
    assert not backend._project_sdlc_stores_path().exists()


def test_every_store_is_listed_with_its_type_and_whether_it_exists(home, tmp_path, remote, monkeypatch, capsys):
    plain = asset_root(tmp_path, "plain")
    kept = asset_root(tmp_path, "kept", {"SZSDLC_BACKEND": "git", f"{GIT}URL": str(remote)})
    for project in (plain, kept):
        init(monkeypatch, project)
    path = backend._project_sdlc_stores_path()
    before = path.read_bytes()
    stores = backend.project_sdlc_stores()
    assert [s.project_sdlc_root for s in stores] == sorted(s.project_sdlc_root for s in stores)
    by_type = {s.backend_type: s for s in stores}
    assert set(by_type) == {"workdir", "git"}
    assert by_type["workdir"].project_sdlc_root == plain
    assert by_type["workdir"].project_sdlc_store_dir == plain
    assert by_type["workdir"].asset_roots == (plain,)
    assert by_type["git"].asset_roots == (kept,)
    assert by_type["git"].project_sdlc_store_dir != kept  # a clone kept elsewhere
    assert all(s.exists for s in stores)
    # The operation's mapping form, as another interface would send it, holds no backend state.
    listed = ops.call("project_sdlc_stores", {}, None).to_dict()["stores"]
    assert {s["backend_type"] for s in listed} == {"workdir", "git"}
    assert all(set(s) == {"project_sdlc_root", "project_sdlc_store_dir", "asset_roots", "backend_type",
                          "exists"} for s in listed)
    assert str(remote) not in json.dumps(listed)
    # A store that is gone is still listed, as gone, until it is pruned.
    shutil.rmtree(plain)
    (gone,) = [s for s in backend.project_sdlc_stores() if s.backend_type == "workdir"]
    assert not gone.exists
    monkeypatch.chdir(tmp_path)
    capsys.readouterr()
    assert run("backend", "list") == 0
    out = capsys.readouterr().out.splitlines()
    assert f"{plain}  workdir  (gone)" in out and f"  asset root  {plain}" in out
    assert f"  asset root  {kept}" in out
    assert path.read_bytes() == before  # listing never writes


def test_a_project_with_two_asset_roots_lists_both(home, tmp_path, remote, monkeypatch):
    env = {"SZSDLC_BACKEND": "git", f"{GIT}URL": str(remote)}
    second, first = asset_root(tmp_path, "second", env), asset_root(tmp_path, "first", env)
    init(monkeypatch, second)
    backend.project_sdlc_root(first)  # the other asset root uses the same project
    (store,) = backend.project_sdlc_stores()
    assert store.asset_roots == (first, second)


@pytest.mark.parametrize("text", ["not json", "[]", '{"project_sdlc_stores": []}'])
def test_a_damaged_record_lists_nothing_and_never_raises(home, tmp_path, monkeypatch, capsys, text):
    path = write_record(text)
    assert backend.project_sdlc_stores() == []
    monkeypatch.chdir(tmp_path)
    assert run("backend", "list") == 0
    assert path.read_text(encoding="utf-8") == text  # left exactly as it is


def test_an_entry_of_the_wrong_shape_is_skipped_and_the_rest_listed(home, tmp_path, monkeypatch):
    project = asset_root(tmp_path, "proj")
    init(monkeypatch, project)
    data = json.loads(backend._project_sdlc_stores_path().read_text(encoding="utf-8"))
    data["project_sdlc_stores"]["/broken"] = {"dir": 3}
    write_record(json.dumps(data))
    assert [s.project_sdlc_root for s in backend.project_sdlc_stores()] == [project]


def test_an_old_stores_file_is_listed_without_writing_the_new_one(home, tmp_path):
    store = DirBackend(tmp_path / "kept")
    (store.project_sdlc_root / ".szsdlc").mkdir(parents=True)
    (store.project_sdlc_root / ".szsdlc" / "config.yml").write_text("{}\n", encoding="utf-8")
    legacy = home / "szsdlc" / backend.LEGACY_STORES_FILE
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(json.dumps({str(store.project_sdlc_store_dir): {"type": "dir", "state": {}}}),
                      encoding="utf-8")
    (listed,) = backend.project_sdlc_stores()
    assert listed.backend_type == "dir" and listed.exists and listed.asset_roots == ()
    assert not backend._project_sdlc_stores_path().exists()
