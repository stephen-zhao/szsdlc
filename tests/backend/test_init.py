"""szsdlc.backend: settings, selection, recorded project sdlc stores, locking, the sync and
refresh wrappers, and the names the glossary retired (WI-0066)."""

from __future__ import annotations

import pytest
from pathlib import Path

from szsdlc import backend, hooks, settings
from szsdlc.errors import ConfigError

from .conftest import DIR, GIT

from .conftest import DIR, GIT, run, write_settings


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
def test_layers_override_key_by_key(user, workdir):
    write_settings(user, {f"{GIT}URL": "u-user", f"{GIT}CLONE_ROOT": "/tmp/all",
                          "OTHER": "ignored"})
    write_settings(workdir / ".claude" / "settings.json", {f"{GIT}URL": "u-project"})
    write_settings(workdir / ".claude" / "settings.local.json", {f"{GIT}SUBDIR": "x"})

    found = settings.load(workdir / "deeper")
    assert found[f"{GIT}URL"].value == "u-project"
    assert found[f"{GIT}CLONE_ROOT"].value == "/tmp/all"
    assert found[f"{GIT}SUBDIR"].source.endswith("settings.local.json")
    assert "OTHER" not in found


def test_the_process_environment_wins(user, workdir, monkeypatch):
    write_settings(user, {"SZSDLC_BACKEND": "git"})
    monkeypatch.setenv("SZSDLC_BACKEND", "dir")
    assert settings.load(workdir)["SZSDLC_BACKEND"].source == settings.ENVIRONMENT


def test_claude_project_dir_names_the_project_layer(isolated_settings, tmp_path, monkeypatch):
    project = tmp_path / "elsewhere"
    write_settings(project / ".claude" / "settings.json", {"SZSDLC_BACKEND": "dir"})
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    assert settings.load()["SZSDLC_BACKEND"].value == "dir"
    assert settings.load(project / "deep" / "er")["SZSDLC_BACKEND"].value == "dir"
    # Outside it, with no settings of its own: another project, with none.
    assert "SZSDLC_BACKEND" not in settings.load(tmp_path / "unrelated")


def test_the_user_directory_is_not_a_project_layer(isolated_settings, user, monkeypatch):
    write_settings(user, {"SZSDLC_BACKEND": "dir"})
    assert settings.project_dir(isolated_settings / "sub") is None


def test_malformed_settings_are_a_config_error(user, workdir):
    user.write_text("{nope", encoding="utf-8")
    with pytest.raises(ConfigError):
        settings.load(workdir)


def test_booleans_parse_in_every_spelling():
    make = lambda v: settings.Setting("K", v, "here", Path("."))  # noqa: E731
    assert make("Yes").as_bool() and not make("off").as_bool()
    with pytest.raises(ConfigError):
        make("maybe").as_bool()


# ---------------------------------------------------------------------------
# Selecting a backend
# ---------------------------------------------------------------------------
def test_workdir_is_the_default(workdir):
    """There is always a backend. The plain case is one of them, not the lack
    of one — so every caller gets an object and none branches on None."""
    assert backend.resolve(workdir).type == backend.WORKDIR
    assert backend.select(workdir).type == backend.WORKDIR


def test_only_the_selected_backends_keys_are_read(user, workdir, tmp_path):
    write_settings(user, {"SZSDLC_BACKEND": "git", f"{GIT}URL": "u",
                          f"{DIR}PATH": str(tmp_path / "d")})
    assert backend.resolve(workdir).type == "git"
    write_settings(workdir / ".claude" / "settings.json", {"SZSDLC_BACKEND": "dir"})
    chosen = backend.resolve(workdir)
    assert chosen.type == "dir" and chosen.project_sdlc_root == tmp_path / "d"


def test_a_project_can_return_to_workdir(user, workdir):
    write_settings(user, {"SZSDLC_BACKEND": "git", f"{GIT}URL": "u"})
    write_settings(workdir / ".claude" / "settings.json", {"SZSDLC_BACKEND": "workdir"})
    assert backend.resolve(workdir).type == backend.WORKDIR


def test_settings_without_a_selector_do_nothing(user, workdir):
    write_settings(user, {f"{GIT}URL": "u"})
    assert backend.resolve(workdir).type == backend.WORKDIR


def test_an_unknown_backend_is_refused(user, workdir):
    write_settings(user, {"SZSDLC_BACKEND": "carrier-pigeon"})
    with pytest.raises(ConfigError, match="not a known backend"):
        backend.resolve(workdir)


def test_an_unknown_key_of_the_selected_backend_is_refused(user, workdir):
    write_settings(user, {"SZSDLC_BACKEND": "git", f"{GIT}URL": "u", f"{GIT}COLOUR": "blue"})
    with pytest.raises(ConfigError, match="COLOUR"):
        backend.resolve(workdir)


@pytest.mark.parametrize("selected, missing", [("git", f"{GIT}URL"), ("dir", f"{DIR}PATH")])
def test_a_required_key_is_named_when_missing(user, workdir, selected, missing):
    write_settings(user, {"SZSDLC_BACKEND": selected})
    with pytest.raises(ConfigError, match=missing):
        backend.resolve(workdir)


def test_backends_are_pluggable(user, workdir, monkeypatch):
    calls = []

    class Fake:
        type = "fake"
        keys = frozenset({"WHERE"})

        def __init__(self, store):
            self.project_sdlc_store_dir = self.project_sdlc_root = store

        @classmethod
        def from_settings(cls, found, context):
            return cls(Path(found["WHERE"].value))

        @classmethod
        def restore(cls, store, state):
            return cls(store)

        def state(self):
            return {}

        def describe(self):
            return [("where", str(self.project_sdlc_store_dir), ("WHERE",))]

        def summary(self):
            return "somewhere fake"

        def materialize(self):
            self.project_sdlc_store_dir.mkdir(parents=True, exist_ok=True)
            calls.append("materialize")

        def has_unsent_changes(self):
            return True

        def commit(self, reason):
            return backend.SyncReport()

        def sync(self, reason):
            calls.append(reason)
            return backend.SyncReport(outgoing=True)

    backend.backend_class("git")  # populate the registry first
    monkeypatch.setitem(backend.BACKENDS, "fake", Fake)
    write_settings(user, {"SZSDLC_BACKEND": "fake",
                          "SZSDLC_BACKEND_FAKE_WHERE": str(workdir.parent / "f")})

    assert run("init") == 0
    assert calls[0] == "materialize" and calls[-1] == "init"
    assert not (workdir / ".szsdlc").exists()


# ---------------------------------------------------------------------------
# git: resolution
# ---------------------------------------------------------------------------


def test_concurrent_records_keep_every_project_sdlc_store(user, tmp_path):
    import threading

    from szsdlc.backend.dir import DirBackend

    stores = [DirBackend(tmp_path / f"s{i}") for i in range(12)]
    threads = [threading.Thread(target=backend._record_project_sdlc_store, args=(s,)) for s in stores]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert set(backend._read_project_sdlc_stores()) == {str(s.project_sdlc_store_dir) for s in stores}
    assert not list(backend._project_sdlc_stores_path().parent.glob("*.tmp"))


# ---------------------------------------------------------------------------
# Hooks and the backend command
# ---------------------------------------------------------------------------


def test_an_unexpected_sync_failure_is_still_reported(configured, workdir, monkeypatch, capsys):
    run("init")

    def explode(*args, **kwargs):
        raise PermissionError("locks directory is read-only")

    monkeypatch.setattr(backend, "sync_at", explode)
    capsys.readouterr()
    assert hooks.user_prompt_submit({"cwd": str(workdir)}) == 0
    out = capsys.readouterr().out
    assert "szsdlc sync: PermissionError: locks directory is read-only" in out


# ---------------------------------------------------------------------------
# Second review round
# ---------------------------------------------------------------------------


def test_aliases_of_one_store_share_a_lock(isolated_settings, tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real)
    with backend._locked(real):
        pass
    with backend._locked(alias):
        pass
    assert len(list((isolated_settings / "szsdlc" / "locks").iterdir())) == 1


def test_no_project_and_no_backend_stays_silent(workdir, capsys):
    assert hooks.session_start({"cwd": str(workdir)}) == 0
    assert hooks.user_prompt_submit({"cwd": str(workdir)}) == 0
    assert capsys.readouterr().out == ""


def test_an_unreadable_stores_file_is_never_overwritten(user, workdir, tmp_path):
    from szsdlc.backend.dir import DirBackend

    path = backend._project_sdlc_stores_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(ConfigError, match="cannot be read"):
        backend._record_project_sdlc_store(DirBackend(tmp_path / "d"))
    assert path.read_text(encoding="utf-8") == "{broken"


def test_a_filesystem_failure_in_the_backend_never_fails_the_command(configured, workdir,
                                                                     monkeypatch, capsys):
    run("init")

    def explode(*args, **kwargs):
        raise PermissionError("locks directory is read-only")

    monkeypatch.setattr(backend, "sync_at", explode)
    assert run("capture", "still works") == 0
    assert "read-only" in capsys.readouterr().err


def test_dash_c_to_another_project_reads_its_own_settings(isolated_settings, tmp_path,
                                                          monkeypatch):
    here = tmp_path / "here"
    write_settings(here / ".claude" / "settings.json", {"SZSDLC_BACKEND": "workdir"})
    there = tmp_path / "there"
    write_settings(there / ".claude" / "settings.json", {"SZSDLC_BACKEND": "dir"})
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(here))
    assert settings.load(there)["SZSDLC_BACKEND"].value == "dir"
    assert settings.load(here / "sub")["SZSDLC_BACKEND"].value == "workdir"
    assert settings.load()["SZSDLC_BACKEND"].value == "workdir"


# ---------------------------------------------------------------------------
# Third review round
# ---------------------------------------------------------------------------


def test_extensions_never_hide_the_builtin_backends(monkeypatch):
    monkeypatch.setattr(backend, "BACKENDS", {"custom": object})
    monkeypatch.setattr(backend, "_builtins_loaded", False)
    assert backend.backend_class("git").type == "git"
    assert "custom" in backend.BACKENDS


# ---------------------------------------------------------------------------
# refresh — the latest becomes current, nothing is sent (WI-0047)
# ---------------------------------------------------------------------------
def test_a_backend_without_refresh_is_a_problem_not_a_crash(tmp_path):
    class Old:
        type = "old"
        store = tmp_path

    report = backend.refresh(Old())
    assert not report.ok and "the old backend cannot refresh" in report.problem


# ---------------------------------------------------------------------------
# Retired names (WI-0066): each still works, and warns naming its replacement
# ---------------------------------------------------------------------------


@pytest.fixture
def dir_backend(user, workdir, tmp_path):
    write_settings(user, {"SZSDLC_BACKEND": "dir", f"{DIR}PATH": str(tmp_path / "d")})
    chosen = backend.resolve(workdir)
    backend.ensure(chosen)
    return chosen


@pytest.mark.parametrize("old, new", [("root", "project_sdlc_root"),
                                      ("store", "project_sdlc_store_dir")])
def test_a_backends_retired_attributes_still_answer(dir_backend, old, new):
    with pytest.warns(DeprecationWarning, match=f"Backend.{old} is deprecated; use Backend.{new}"):
        assert getattr(dir_backend, old) == getattr(dir_backend, new)


def test_has_local_changes_still_answers(dir_backend):
    with pytest.warns(DeprecationWarning, match=r"use Backend\.has_unsent_changes\(\)"):
        assert dir_backend.has_local_changes() is dir_backend.has_unsent_changes() is False


def test_context_directory_still_works_both_ways(tmp_path):
    with pytest.warns(DeprecationWarning, match=r"use Context\(project_asset_root=\.\.\.\)"):
        context = backend.Context(project="p", home=tmp_path, directory=tmp_path / "a")
    assert context.project_asset_root == tmp_path / "a"
    with pytest.warns(DeprecationWarning, match="use Context.project_asset_root"):
        assert context.directory == tmp_path / "a"
    assert backend.Context("p", tmp_path) == backend.Context("p", tmp_path, None)


@pytest.mark.parametrize("old, new", [
    ("managed", "project_sdlc_store_for"),
    ("project_root", "project_sdlc_root"),
    ("project_directory", "project_asset_root"),
])
def test_a_retired_function_still_answers(dir_backend, workdir, old, new):
    where = dir_backend.project_sdlc_store_dir if old == "managed" else workdir
    with pytest.warns(DeprecationWarning, match=f"backend.{old}\\(\\) is deprecated; use szsdlc.backend.{new}"):
        answer = getattr(backend, old)(where)
    expected = getattr(backend, new)(where)
    if old == "managed":
        assert answer.project_sdlc_store_dir == expected.project_sdlc_store_dir
    else:
        assert answer == expected


def test_the_stores_file_path_keeps_its_old_name_for_the_viewer():
    with pytest.warns(DeprecationWarning, match="_project_sdlc_stores_path"):
        assert backend._registry_path() == backend._project_sdlc_stores_path()


# ---------------------------------------------------------------------------
# Locations (WI-0071)
# ---------------------------------------------------------------------------


def test_a_backend_without_from_location_never_claims_one(isolated_settings, monkeypatch):
    class Plain:
        type = "plain"

    monkeypatch.setitem(backend.BACKENDS, "aaa-plain", Plain)
    assert backend.from_location("not a url") is None
    assert backend.from_location("https://host/org/repo.git").type == "git"


def test_backends_are_asked_in_a_stable_order(isolated_settings, monkeypatch):
    seen = []

    def claimer(name):
        class Claims:
            type = name

            @classmethod
            def from_location(cls, text, context):
                seen.append(name)
                return cls() if name == "zz" else None
        return Claims

    monkeypatch.setitem(backend.BACKENDS, "zz", claimer("zz"))
    monkeypatch.setitem(backend.BACKENDS, "aa", claimer("aa"))
    assert backend.from_location("something only zz reads").type == "zz"
    assert seen == ["aa", "zz"]


def test_the_display_helpers_are_public():
    assert backend.redact("https://token@host/org/repo.git?access_token=x") == "https://***@host/org/repo.git?***"
    assert backend.redact("git@host:org/repo.git") == "git@host:org/repo.git"
    assert backend.project_sdlc_store_dir_name("/x/tracker-0123456789") == "tracker"
    assert backend.project_sdlc_store_dir_name("/x/plain") == "plain"
    from szsdlc.backend import git
    assert git.redact is backend.redact  # still importable where it lived
