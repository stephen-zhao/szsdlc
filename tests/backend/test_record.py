"""The record of project sdlc stores (WI-0067): every backend type, keyed by project
sdlc root, with asset roots linked both ways; settings decide and the record follows.

Every test runs with szsdlc's home isolated (`isolated_settings`), so none writes
the developer's own record.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from backend.conftest import DIR, GIT, git, run, write_settings
from szsdlc import backend, hooks
from szsdlc.backend.dir import DirBackend


def record() -> dict:
    return json.loads(backend._project_sdlc_stores_path().read_text(encoding="utf-8"))


def asset_root(tmp_path: Path, name: str, env: dict | None = None) -> Path:
    """An asset root with its own project settings (or none)."""
    path = tmp_path / name
    path.mkdir(parents=True)
    if env is not None:
        write_settings(path / ".claude" / "settings.json", env)
    return path


@pytest.fixture
def home(isolated_settings):
    return isolated_settings


@pytest.fixture
def remote(tmp_path) -> Path:
    path = tmp_path / "remote.git"
    git("init", "--bare", "--quiet", "--initial-branch=main", str(path))
    return path


def test_a_workdir_project_is_recorded_with_its_asset_root(home, tmp_path, monkeypatch):
    project = asset_root(tmp_path, "proj")
    monkeypatch.chdir(project)
    assert run("init") == 0
    stores = record()["project_sdlc_stores"]
    assert stores[str(project)]["backend_type"] == "workdir"
    assert stores[str(project)]["dir"] == str(project)
    assert stores[str(project)]["asset_roots"] == [str(project)]
    assert record()["asset_roots"] == {str(project): str(project)}


def test_a_scratch_folder_records_nothing(home, tmp_path):
    scratch = asset_root(tmp_path, "scratch")
    assert backend.project_sdlc_root(scratch) is None
    assert not backend._project_sdlc_stores_path().exists()


def test_recording_what_is_already_recorded_writes_nothing(home, tmp_path, monkeypatch):
    project = asset_root(tmp_path, "proj")
    monkeypatch.chdir(project)
    assert run("init") == 0
    path = backend._project_sdlc_stores_path()
    before = path.stat().st_mtime_ns, path.read_bytes()
    backend.project_sdlc_root(project)
    assert (path.stat().st_mtime_ns, path.read_bytes()) == before


def test_two_subdirectory_projects_sharing_one_clone_get_two_entries(home, tmp_path, remote, monkeypatch):
    env = {"SZSDLC_BACKEND": "git", f"{GIT}URL": str(remote), f"{GIT}SUBDIR": "projects/{project}"}
    alpha, beta = asset_root(tmp_path, "alpha", env), asset_root(tmp_path, "beta", env)
    for root in (alpha, beta):
        monkeypatch.chdir(root)
        assert run("init") == 0
    stores = record()["project_sdlc_stores"]
    roots = {Path(r).name: r for r in stores}
    assert set(roots) == {"alpha", "beta"}
    assert stores[roots["alpha"]]["dir"] == stores[roots["beta"]]["dir"]  # one clone
    assert record()["asset_roots"] == {str(alpha): roots["alpha"], str(beta): roots["beta"]}
    # A path in one subdirectory project never resolves to its sibling.
    inside_beta = Path(roots["beta"]) / ".szsdlc" / "config.yml"
    assert str(backend.project_sdlc_store_for(inside_beta).project_sdlc_root) == roots["beta"]


def test_two_asset_roots_sharing_one_project_are_both_listed_and_linked(home, tmp_path, remote, monkeypatch):
    env = {"SZSDLC_BACKEND": "git", f"{GIT}URL": str(remote)}
    first, second = asset_root(tmp_path, "first", env), asset_root(tmp_path, "second", env)
    monkeypatch.chdir(first)
    assert run("init") == 0
    root = backend.project_sdlc_root(second)  # the second asset root uses the same project
    entry = record()["project_sdlc_stores"][str(root)]
    assert entry["asset_roots"] == sorted([str(first), str(second)])
    assert record()["asset_roots"] == {str(first): str(root), str(second): str(root)}


def test_an_asset_root_switching_projects_moves_its_link_and_leaves_the_old_list(home, tmp_path, remote,
                                                                                monkeypatch):
    code = asset_root(tmp_path, "code", {"SZSDLC_BACKEND": "git", f"{GIT}URL": str(remote)})
    monkeypatch.chdir(code)
    assert run("init") == 0
    old_root = record()["asset_roots"][str(code)]
    write_settings(code / ".claude" / "settings.json", {"SZSDLC_BACKEND": "dir", f"{DIR}PATH": str(tmp_path / "d")})
    assert run("init") == 0
    data = record()
    new_root = data["asset_roots"][str(code)]
    assert new_root != old_root and data["project_sdlc_stores"][new_root]["backend_type"] == "dir"
    assert data["project_sdlc_stores"][old_root]["asset_roots"] == []  # left the old project's list
    assert data["project_sdlc_stores"][new_root]["asset_roots"] == [str(code)]


def test_a_project_switching_backend_type_is_served_by_its_settings(home, tmp_path, remote, monkeypatch):
    """Settings decide, and the record follows: workdir first, then git in its settings."""
    code = asset_root(tmp_path, "code")
    monkeypatch.chdir(code)
    assert run("init") == 0
    assert record()["project_sdlc_stores"][str(code)]["backend_type"] == "workdir"
    write_settings(code / ".claude" / "settings.json", {"SZSDLC_BACKEND": "git", f"{GIT}URL": str(remote)})
    assert run("init") == 0  # the git store is new and empty, so it is initialised
    root = backend.project_sdlc_root(code)
    assert root != code and record()["asset_roots"][str(code)] == str(root)
    assert record()["project_sdlc_stores"][str(root)]["backend_type"] == "git"


def test_inside_a_clone_no_settings_reach_the_record_still_serves(home, tmp_path, remote, monkeypatch):
    code = asset_root(tmp_path, "code", {"SZSDLC_BACKEND": "git", f"{GIT}URL": str(remote)})
    monkeypatch.chdir(code)
    assert run("init") == 0
    clone = Path(record()["asset_roots"][str(code)])
    assert backend._record_decides(clone / ".szsdlc").type == "git"
    assert backend.project_sdlc_root(clone) is None  # ordinary discovery finds it from there


def test_a_workdir_project_adds_no_project_files_line_to_session_start(home, tmp_path, monkeypatch, capsys):
    project = asset_root(tmp_path, "proj")
    monkeypatch.chdir(project)
    assert run("init") == 0
    capsys.readouterr()
    assert hooks.session_start({"cwd": str(project)}) == 0
    assert "szsdlc project files" not in capsys.readouterr().out
    assert run("backend") == 0
    assert "inside its project sdlc store dir" not in capsys.readouterr().out


def test_an_old_stores_file_is_migrated_and_left_alone(home, tmp_path):
    store = DirBackend(tmp_path / "kept")
    (store.project_sdlc_root / ".szsdlc").mkdir(parents=True)
    (store.project_sdlc_root / ".szsdlc" / "config.yml").write_text("{}\n", encoding="utf-8")
    legacy = home / "szsdlc" / backend.LEGACY_STORES_FILE
    legacy.parent.mkdir(parents=True, exist_ok=True)
    old = json.dumps({str(store.project_sdlc_store_dir): {"type": "dir", "state": {}}})
    legacy.write_text(old, encoding="utf-8")
    found = backend.project_sdlc_store_for(store.project_sdlc_root / ".szsdlc")
    assert found is not None and found.type == "dir"
    assert not backend._project_sdlc_stores_path().exists()  # a read never writes (validate included)
    backend.ensure(store)  # the next write, under the lock, saves the migration
    assert record()["project_sdlc_stores"][str(store.project_sdlc_root)]["asset_roots"] == []
    assert legacy.read_text(encoding="utf-8") == old  # untouched, for older szsdlc versions


def test_migration_skips_a_store_with_no_project_and_refuses_an_unreadable_legacy_file(home, tmp_path):
    scratch = DirBackend(tmp_path / "scratch")
    scratch.project_sdlc_store_dir.mkdir()
    legacy = home / "szsdlc" / backend.LEGACY_STORES_FILE
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(json.dumps({str(scratch.project_sdlc_store_dir): {"type": "dir", "state": {}}}),
                      encoding="utf-8")
    assert backend._read_record()["project_sdlc_stores"] == {}  # no .szsdlc/config.yml: not a project
    legacy.write_text("{broken", encoding="utf-8")
    assert backend._read_record()["project_sdlc_stores"] == {}  # lookups carry on
    with pytest.raises(backend.ConfigError, match="cannot be read to migrate it"):
        backend._read_record(strict=True)  # a write never drops what it could not read


def test_stale_entries_are_reported_never_removed_until_pruned(home, tmp_path, monkeypatch, capsys):
    kept, gone = asset_root(tmp_path, "kept"), asset_root(tmp_path, "gone")
    for project in (kept, gone):
        monkeypatch.chdir(project)
        assert run("init") == 0
    elsewhere = asset_root(tmp_path, "elsewhere")
    backend.ensure(backend.resolve(kept), asset_root=elsewhere)  # a second asset root for `kept`
    elsewhere.rmdir()
    import shutil
    shutil.rmtree(gone)
    stale = backend.stale_project_sdlc_stores()
    # A store that is gone is one finding (its links go with it); `kept` lost an asset root.
    assert sorted(kind for kind, _, _ in stale) == ["asset_root", "store"]
    # Reported, and nothing removed.
    monkeypatch.chdir(kept)
    capsys.readouterr()
    assert run("backend") == 0
    assert re.search(rf"stale records +{len(stale)} \(szsdlc backend prune\)", capsys.readouterr().out)
    assert str(gone) in record()["project_sdlc_stores"]
    # validate names only this project's own: its asset root that is gone.
    from szsdlc import ops
    rows = [r for r in ops.call("validate", {"limit": 0}, str(kept)).rows if r.kind == "stale-record"]
    assert len(rows) == 1 and str(elsewhere) in rows[0].message and rows[0].level == "warning"
    # Pruned on request, one line per removal.
    assert run("backend", "prune") == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == len(stale) and all(line.startswith("removed: ") for line in lines)
    assert backend.stale_project_sdlc_stores() == []
    assert str(gone) not in record()["project_sdlc_stores"]
    assert record()["project_sdlc_stores"][str(kept)]["asset_roots"] == [str(kept)]
    assert run("backend", "prune") == 0 and capsys.readouterr().out == ""  # nothing left to prune


def test_a_damaged_record_is_empty_for_lookups(home, tmp_path):
    path = backend._project_sdlc_stores_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"project_sdlc_stores": []}', encoding="utf-8")
    assert backend.project_sdlc_store_for(tmp_path) is None
    assert backend.stale_project_sdlc_stores() == []


def test_a_link_and_a_list_that_disagree_are_reported_and_pruned(home, tmp_path, monkeypatch):
    project = asset_root(tmp_path, "proj")
    monkeypatch.chdir(project)
    assert run("init") == 0
    other = asset_root(tmp_path, "other")
    path = backend._project_sdlc_stores_path()
    data = record()
    data["asset_roots"][str(other)] = str(project)  # linked, but not listed
    data["asset_roots"][str(tmp_path / "nowhere")] = str(tmp_path / "unrecorded")  # linked to nothing
    path.write_text(json.dumps(data), encoding="utf-8")
    assert sorted(kind for kind, _, _ in backend.stale_project_sdlc_stores()) == ["link", "link"]
    assert len(backend.prune_project_sdlc_stores()) == 2
    assert record()["asset_roots"] == {str(project): str(project)}


def test_entries_of_the_wrong_shape_are_skipped_for_lookups_and_refuse_a_write(home, tmp_path, monkeypatch):
    project = asset_root(tmp_path, "proj")
    monkeypatch.chdir(project)
    assert run("init") == 0
    data = record()
    data["project_sdlc_stores"]["/broken"] = {"dir": 3}
    data["asset_roots"]["/also"] = ["not", "a", "path"]
    backend._project_sdlc_stores_path().write_text(json.dumps(data), encoding="utf-8")
    assert "/broken" not in backend._read_record()["project_sdlc_stores"]
    assert backend.stale_project_sdlc_stores() == []  # nothing crashes on them
    with pytest.raises(backend.ConfigError, match="holds an entry"):
        backend._read_record(strict=True)


def test_pruning_a_stale_listing_keeps_the_store_it_is_linked_to(home, tmp_path, monkeypatch):
    first, second = asset_root(tmp_path, "first"), asset_root(tmp_path, "second")
    for project in (first, second):
        monkeypatch.chdir(project)
        assert run("init") == 0
    data = record()
    data["project_sdlc_stores"][str(first)]["asset_roots"].append(str(second))  # listed, linked elsewhere
    backend._project_sdlc_stores_path().write_text(json.dumps(data), encoding="utf-8")
    assert [kind for kind, _, _ in backend.stale_project_sdlc_stores()] == ["listed"]
    backend.prune_project_sdlc_stores()
    assert record()["project_sdlc_stores"][str(first)]["asset_roots"] == [str(first)]
    assert record()["project_sdlc_stores"][str(second)]["asset_roots"] == [str(second)]
    assert record()["asset_roots"][str(second)] == str(second)


def test_a_link_to_an_asset_root_that_is_gone_is_still_reported(home, tmp_path, monkeypatch):
    project = asset_root(tmp_path, "proj")
    monkeypatch.chdir(project)
    assert run("init") == 0
    data = record()
    data["asset_roots"][str(tmp_path / "gone")] = str(project)  # linked, unlisted, and not on disk
    backend._project_sdlc_stores_path().write_text(json.dumps(data), encoding="utf-8")
    assert [kind for kind, _, _ in backend.stale_project_sdlc_stores()] == ["link"]


def test_settings_still_decide_inside_a_store_nested_in_its_asset_root(home, tmp_path, monkeypatch):
    code = asset_root(tmp_path, "code", {"SZSDLC_BACKEND": "dir", f"{DIR}PATH": str(tmp_path / "code" / "sdlc")})
    monkeypatch.chdir(code)
    assert run("init") == 0
    assert backend._record_decides(code / "sdlc" / ".szsdlc") is None  # the asset root's settings reach it


def test_a_record_that_cannot_be_written_never_fails_init(home, tmp_path, monkeypatch, capsys):
    path = backend._project_sdlc_stores_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{broken", encoding="utf-8")
    project = asset_root(tmp_path, "proj")
    monkeypatch.chdir(project)
    assert run("init") == 0  # the project is made; the record is the next command's to report
    assert (project / ".szsdlc" / "config.yml").is_file()
    assert path.read_text(encoding="utf-8") == "{broken"  # and never overwritten
    capsys.readouterr()
    assert run("backend") == 0
    out = capsys.readouterr().out
    assert re.search(r"^  record +.*cannot be read", out, re.MULTILINE)  # said by szsdlc backend
