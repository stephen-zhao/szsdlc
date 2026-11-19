"""Backends: settings layering, where a project is kept, and git sync.

The git remote here is a bare repository on disk, which exercises the real
backend end to end without a network.
"""

from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path

import pytest

from szsdlc import backend, cli, hooks, settings
from szsdlc.errors import ConfigError

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
    assert settings.load(tmp_path)["SZSDLC_BACKEND"].value == "dir"


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
    assert backend.resolve(workdir) is None
    assert backend.select(workdir).type == backend.WORKDIR


def test_only_the_selected_backends_keys_are_read(user, workdir, tmp_path):
    write_settings(user, {"SZSDLC_BACKEND": "git", f"{GIT}URL": "u",
                          f"{DIR}PATH": str(tmp_path / "d")})
    assert backend.resolve(workdir).type == "git"
    write_settings(workdir / ".claude" / "settings.json", {"SZSDLC_BACKEND": "dir"})
    chosen = backend.resolve(workdir)
    assert chosen.type == "dir" and chosen.root == tmp_path / "d"


def test_a_project_can_return_to_workdir(user, workdir):
    write_settings(user, {"SZSDLC_BACKEND": "git", f"{GIT}URL": "u"})
    write_settings(workdir / ".claude" / "settings.json", {"SZSDLC_BACKEND": "workdir"})
    assert backend.resolve(workdir) is None


def test_settings_without_a_selector_do_nothing(user, workdir):
    write_settings(user, {f"{GIT}URL": "u"})
    assert backend.resolve(workdir) is None


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
            self.store = self.root = store

        @classmethod
        def from_settings(cls, found, context):
            return cls(Path(found["WHERE"].value))

        @classmethod
        def restore(cls, store, state):
            return cls(store)

        def state(self):
            return {}

        def describe(self):
            return [("where", str(self.store), ("WHERE",))]

        def summary(self):
            return "somewhere fake"

        def materialize(self):
            self.store.mkdir(parents=True, exist_ok=True)
            calls.append("materialize")

        def has_local_changes(self):
            return True

        def commit(self, reason):
            return backend.SyncReport()

        def sync(self, reason):
            calls.append(reason)
            return backend.SyncReport(pushed=True)

    backend.backend_class("git")  # populate the registry first
    monkeypatch.setitem(backend.BACKENDS, "fake", Fake)
    write_settings(user, {"SZSDLC_BACKEND": "fake",
                          "SZSDLC_BACKEND_FAKE_WHERE": str(workdir.parent / "f")})

    assert run("init") == 0
    assert calls[0] == "materialize" and calls[-1] == "init"
    assert not (workdir / ".szsdlc").exists()


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
    # Nothing to commit or sync, ever.
    assert backend.commit_at(workdir, "x").ok
    assert backend.sync_at(workdir, "x", only_if_changed=True) is None
    report = backend.sync_at(workdir, "x")
    assert report.ok and not report.pulled and not report.pushed


def test_a_relative_dir_path_resolves_against_the_project(workdir):
    write_settings(workdir / ".claude" / "settings.json",
                   {"SZSDLC_BACKEND": "dir", f"{DIR}PATH": "../tracking"})
    assert backend.resolve(workdir).root == workdir.parent / "tracking"


def test_dir_refuses_a_file(user, workdir, tmp_path):
    (tmp_path / "f").write_text("x")
    write_settings(user, {"SZSDLC_BACKEND": "dir", f"{DIR}PATH": str(tmp_path / "f")})
    with pytest.raises(ConfigError, match="not a directory"):
        backend.project_root(workdir)


def test_dir_shows_where_it_is(user, workdir, tmp_path, capsys):
    write_settings(user, {"SZSDLC_BACKEND": "dir", f"{DIR}PATH": str(tmp_path / "d")})
    assert run("backend") == 0
    out = capsys.readouterr().out
    assert "backend  dir" in out and "settings.json" in out and "not created yet" in out


# ---------------------------------------------------------------------------
# git: resolution
# ---------------------------------------------------------------------------


def test_the_clone_is_hidden_beside_claude_by_default(isolated_settings, user, workdir):
    write_settings(user, {"SZSDLC_BACKEND": "git", f"{GIT}URL": "git@h:me/track.git"})
    chosen = backend.resolve(workdir)
    assert chosen.store.parent == isolated_settings / "szsdlc" / "git"
    assert chosen.store.name.startswith("track-")
    assert chosen.root == chosen.store


def test_clone_root_and_path_relocate_it(user, workdir, tmp_path):
    write_settings(user, {"SZSDLC_BACKEND": "git", f"{GIT}URL": "u",
                          f"{GIT}CLONE_ROOT": str(tmp_path / "all")})
    assert backend.resolve(workdir).store.parent == tmp_path / "all"

    write_settings(workdir / ".claude" / "settings.local.json",
                   {f"{GIT}CLONE_PATH": "../{project}-tracker"})
    assert backend.resolve(workdir).store == workdir.parent / "myproj-tracker"


def test_subdir_can_name_the_project(user, workdir):
    write_settings(user, {"SZSDLC_BACKEND": "git", f"{GIT}URL": "u",
                          f"{GIT}SUBDIR": "projects/{project}/"})
    chosen = backend.resolve(workdir)
    assert chosen.root == chosen.store / "projects" / "myproj"


def test_a_subdir_outside_the_store_is_refused(user, workdir):
    write_settings(user, {"SZSDLC_BACKEND": "git", f"{GIT}URL": "u", f"{GIT}SUBDIR": "../x"})
    with pytest.raises(ConfigError):
        backend.resolve(workdir)


# ---------------------------------------------------------------------------
# git: end to end
# ---------------------------------------------------------------------------


@pytest.fixture
def configured(user, bare, workdir):
    write_settings(user, {"SZSDLC_BACKEND": "git", f"{GIT}URL": str(bare)})
    return backend.resolve(workdir)


def test_init_writes_to_the_clone_and_pushes(configured, bare, workdir, capsys):
    assert run("init") == 0
    assert list(workdir.iterdir()) == []
    assert (configured.store / ".szsdlc" / "config.yml").is_file()
    assert ".szsdlc/config.yml" in remote_files(bare)


def test_a_command_that_changes_files_pushes_them(configured, bare, workdir, capsys):
    run("init")
    before = len(remote_files(bare))
    assert run("capture", "remember the milk") == 0
    assert len(remote_files(bare)) > before
    assert last_commit(bare) == "szsdlc: capture"


def test_a_read_only_command_makes_no_commit(configured, bare, workdir, capsys):
    run("init")
    head = git("--git-dir", str(bare), "rev-parse", "main")
    assert run("context") == 0
    assert git("--git-dir", str(bare), "rev-parse", "main") == head


def test_an_explicit_sync_pulls_first(configured, bare, workdir, tmp_path, capsys):
    run("init")
    other = tmp_path / "other"
    git("clone", "--quiet", str(bare), str(other))
    (other / "theirs.md").write_text("x")
    git("add", ".", cwd=other)
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "theirs", cwd=other)
    git("push", "-q", "origin", "HEAD:main", cwd=other)
    assert run("sync") == 0
    assert (configured.store / "theirs.md").is_file()


def test_a_missing_project_says_init(configured, workdir, capsys):
    assert run("context") != 0
    assert "szsdlc init" in capsys.readouterr().err


def test_two_machines_see_each_others_work(configured, bare, workdir, tmp_path,
                                           isolated_settings, monkeypatch, capsys):
    run("init")
    run("capture", "from machine one")

    second = tmp_path / "machine-two"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(second))
    write_settings(second / "settings.json", {"SZSDLC_BACKEND": "git", f"{GIT}URL": str(bare)})
    run("capture", "from machine two")
    capsys.readouterr()

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(isolated_settings))
    assert hooks.user_prompt_submit({"cwd": str(workdir)}) == 0
    assert capsys.readouterr().out == ""
    assert run("inbox") == 0
    listing = capsys.readouterr().out
    assert "machine one" in listing and "machine two" in listing


def test_a_conflict_keeps_local_work_and_says_so(configured, bare, workdir, tmp_path, capsys):
    run("init")
    other = tmp_path / "other"
    git("clone", "--quiet", str(bare), str(other))
    (other / ".szsdlc" / "config.yml").write_text("project:\n  name: theirs\n")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "theirs", cwd=other)
    git("push", "-q", "origin", "HEAD:main", cwd=other)

    (configured.store / ".szsdlc" / "config.yml").write_text("project:\n  name: ours\n")
    report = backend.sync(configured, "test")
    assert not report.ok and "conflict" in report.problem
    assert "ours" in (configured.store / ".szsdlc" / "config.yml").read_text()


def test_an_unreachable_remote_never_fails_the_command(configured, bare, workdir, capsys):
    run("init")
    bare.rename(bare.parent / "gone.git")
    assert run("capture", "offline idea") == 0
    assert "szsdlc backend sync" in capsys.readouterr().err
    assert git("status", "--porcelain", cwd=configured.store) == ""


def test_a_branch_the_remote_lacks_is_created(user, bare, workdir, capsys):
    write_settings(user, {"SZSDLC_BACKEND": "git", f"{GIT}URL": str(bare),
                          f"{GIT}BRANCH": "tracker"})
    assert run("init") == 0
    assert ".szsdlc/config.yml" in remote_files(bare, "tracker")


def test_an_existing_branch_is_checked_out(user, bare, workdir, tmp_path, capsys):
    seed = tmp_path / "seed"
    git("clone", "--quiet", str(bare), str(seed))
    (seed / "README").write_text("hi")
    git("add", ".", cwd=seed)
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "seed", cwd=seed)
    git("push", "-q", "origin", "HEAD:main", "HEAD:tracker", cwd=seed)
    write_settings(user, {"SZSDLC_BACKEND": "git", f"{GIT}URL": str(bare),
                          f"{GIT}BRANCH": "tracker"})
    assert run("init") == 0
    assert {"README", ".szsdlc/config.yml"} <= set(remote_files(bare, "tracker"))


def test_a_foreign_directory_is_not_taken_over(user, bare, workdir, tmp_path):
    taken = tmp_path / "taken"
    taken.mkdir()
    (taken / "precious").write_text("x")
    write_settings(user, {"SZSDLC_BACKEND": "git", f"{GIT}URL": str(bare),
                          f"{GIT}CLONE_PATH": str(taken)})
    with pytest.raises(ConfigError, match="not a clone"):
        backend.project_root(workdir)


def test_a_clone_of_something_else_is_refused(user, bare, workdir, tmp_path):
    other = tmp_path / "other.git"
    git("init", "--bare", "--quiet", str(other))
    clone = tmp_path / "clone"
    git("clone", "--quiet", str(other), str(clone))
    write_settings(user, {"SZSDLC_BACKEND": "git", f"{GIT}URL": str(bare),
                          f"{GIT}CLONE_PATH": str(clone)})
    with pytest.raises(ConfigError, match="is a clone of"):
        backend.project_root(workdir)


def test_an_unclonable_url_is_a_config_error(user, workdir, tmp_path):
    write_settings(user, {"SZSDLC_BACKEND": "git", f"{GIT}URL": str(tmp_path / "nowhere")})
    with pytest.raises(ConfigError, match="could not clone"):
        backend.project_root(workdir)


def test_an_ordinary_project_in_a_git_repo_is_never_committed(project, tmp_path,
                                                              monkeypatch, capsys):
    git("init", "--quiet", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    assert run("capture", "local only") == 0
    assert "No commits yet" in git("status", cwd=tmp_path)
    assert backend.sync_at(tmp_path, "x") is None


def test_working_inside_the_clone_still_syncs(configured, bare, workdir, monkeypatch, capsys):
    run("init")
    monkeypatch.chdir(configured.store)
    assert run("capture", "from inside") == 0
    assert last_commit(bare) == "szsdlc: capture"


# ---------------------------------------------------------------------------
# Hooks and the backend command
# ---------------------------------------------------------------------------


def test_session_start_says_where_the_files_are(configured, workdir, capsys):
    run("init")
    capsys.readouterr()
    assert hooks.session_start({"cwd": str(workdir)}) == 0
    assert f"szsdlc project files: {configured.store}" in capsys.readouterr().out


def test_an_edit_commits_locally_and_the_turn_end_pushes(configured, bare, workdir, capsys):
    run("init")
    run("capture", "one")
    pushed = git("--git-dir", str(bare), "rev-parse", "main")
    idea = next((configured.store / "ideas").rglob("*.md"))

    idea.write_text(idea.read_text() + "\nmore\n")
    payload = {"cwd": str(workdir), "tool_input": {"file_path": str(idea)}}
    assert hooks.post_edit(payload) == 0
    assert git("log", "-1", "--format=%s", cwd=configured.store).startswith("szsdlc: edit")
    assert git("status", "--porcelain", cwd=configured.store) == ""
    assert git("--git-dir", str(bare), "rev-parse", "main") == pushed  # nothing sent yet

    hooks.stop({"cwd": str(workdir)})
    assert git("--git-dir", str(bare), "rev-parse", "main") != pushed
    assert "szsdlc: edit" in git("--git-dir", str(bare), "log", "--format=%s", "main")


def test_inside_a_claude_turn_commands_only_commit(configured, bare, workdir,
                                                   monkeypatch, capsys):
    run("init")
    pushed = git("--git-dir", str(bare), "rev-parse", "main")
    monkeypatch.setenv("CLAUDECODE", "1")
    assert run("capture", "mid-turn") == 0
    assert git("log", "-1", "--format=%s", cwd=configured.store).strip() == "szsdlc: capture"
    assert git("--git-dir", str(bare), "rev-parse", "main") == pushed

    hooks.stop({"cwd": str(workdir)})
    assert "szsdlc: capture" in git("--git-dir", str(bare), "log", "--format=%s", "main")


def test_an_explicit_sync_inside_a_turn_still_pushes(configured, bare, workdir,
                                                     monkeypatch, capsys):
    run("init")
    monkeypatch.setenv("CLAUDECODE", "1")
    (configured.store / "note.md").write_text("x")
    assert run("sync") == 0
    assert "note.md" in remote_files(bare)


def test_a_commit_outside_a_clone_says_so(configured, workdir):
    report = backend.commit(configured, "x")  # never materialized
    assert not report.ok and "not a git clone" in report.problem


def test_prompt_hook_reports_a_failed_sync_into_context(configured, bare, workdir, capsys):
    run("init")
    bare.rename(bare.parent / "gone.git")
    capsys.readouterr()
    hooks.dispatch("user-prompt-submit", io.StringIO(json.dumps({"cwd": str(workdir)})))
    assert "szsdlc backend sync" in capsys.readouterr().out


def test_backend_show_names_every_source(configured, workdir, capsys):
    assert run("backend") == 0
    out = capsys.readouterr().out
    assert "backend  git" in out and "settings.json" in out and "(default)" in out


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


def test_backend_sync_runs_on_demand(configured, bare, workdir, capsys):
    run("init")
    (configured.store / "note.md").write_text("x")
    assert run("backend", "sync") == 0
    assert "note.md" in remote_files(bare)


def test_backend_sync_reports_a_failure(configured, bare, workdir, capsys):
    run("init")
    (configured.store / "note.md").write_text("x")
    bare.rename(bare.parent / "gone.git")
    assert run("backend", "sync") == 3
    assert "could not reach" in capsys.readouterr().err


def test_backend_sync_without_a_backend_is_refused(workdir, capsys):
    assert run("backend", "sync") != 0
    assert "SZSDLC_BACKEND" in capsys.readouterr().err
