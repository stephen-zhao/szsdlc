"""Backends: settings layering, where a project is kept, and git sync.

The git remote here is a bare repository on disk, which exercises the real
backend end to end without a network.
"""

from __future__ import annotations

import io
import json
import shutil
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


def test_the_workdir_backend_has_nothing_to_say_and_nothing_to_do(workdir):
    """The degenerate implementation, pinned.

    Every one of these is a protocol obligation with no work behind it, and
    two are reached by nothing today — `restore` because this backend is never
    recorded, `summary` because it is only said when the project is elsewhere.
    They are asserted so that the next backend, written by copying this one,
    does not inherit an answer nobody ever read.
    """
    assert run("init") == 0
    chosen = backend.resolve(workdir)
    assert chosen.describe() == []            # no settings, and you are here
    assert chosen.summary() == "in the working directory"
    assert chosen.materialize() is None       # the tree already exists
    assert backend.sync(chosen, "x").ok  # nothing to bring into agreement

    restored = backend.backend_class("workdir").restore(workdir, {})
    assert restored.root == workdir  # the project, not an empty directory


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
            return backend.SyncReport(outgoing=True)

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


@pytest.mark.parametrize("subdir", ["../x", "C:/elsewhere", "D:x"])
def test_a_subdir_outside_the_store_is_refused(user, workdir, subdir):
    write_settings(user, {"SZSDLC_BACKEND": "git", f"{GIT}URL": "u", f"{GIT}SUBDIR": subdir})
    with pytest.raises(ConfigError):
        backend.resolve(workdir)


def test_project_names_the_enclosing_checkout_from_a_subdirectory(user, workdir, tmp_path):
    git("init", "--quiet", str(workdir))
    write_settings(user, {"SZSDLC_BACKEND": "dir", f"{DIR}PATH": str(tmp_path / "t" / "{project}")})
    (workdir / "src" / "deep").mkdir(parents=True)
    assert backend.resolve(workdir / "src" / "deep").root == tmp_path / "t" / "myproj"


@pytest.mark.parametrize("url, shown", [
    ("https://ghp_secret@github.com/me/t.git", "https://***@github.com/me/t.git"),
    ("https://me:hunter2@host/t.git", "https://***@host/t.git"),
    ("ssh://git:pw@host/t.git", "ssh://git:***@host/t.git"),
    ("ssh://git@host/t.git", "ssh://git@host/t.git"),
    ("git@host:me/t.git", "git@host:me/t.git"),
    ("/srv/t.git", "/srv/t.git"),
])
def test_credentials_in_the_url_are_never_shown(url, shown):
    from szsdlc.backend.git import redact

    assert redact(url) == shown


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


def test_a_restored_store_keeps_its_subdir(user, bare, workdir, capsys):
    write_settings(user, {"SZSDLC_BACKEND": "git", f"{GIT}URL": str(bare),
                          f"{GIT}SUBDIR": "projects/{project}"})
    assert run("init") == 0
    chosen = backend.resolve(workdir)
    assert backend.managed(chosen.store).root == chosen.store / "projects" / "myproj"


def test_concurrent_registrations_keep_every_store(user, tmp_path):
    import threading

    from szsdlc.backend.dir import DirBackend

    stores = [DirBackend(tmp_path / f"s{i}") for i in range(12)]
    threads = [threading.Thread(target=backend._register, args=(s,)) for s in stores]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert set(backend._read_registry()) == {str(s.store) for s in stores}
    assert not list(backend._registry_path().parent.glob("*.tmp"))


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


def test_sync_does_what_it_has_always_done(configured, bare, workdir, capsys):
    """`sync` renders **and** reaches the remote, as the command always did.

    The word has named this since 0.2.4 and is not deprecated: an upgrade must
    not change what an existing project's `szsdlc sync` does, and this is the
    test that says so. `render` is the new name, for the local half alone.
    """
    run("init")
    assert run("capture", "a thought the alias should send") == 0
    idea = next((configured.store / "ideas").rglob("*.md"))
    idea.write_text(idea.read_text() + "\nmarker-7a1b\n")
    head = git("--git-dir", str(bare), "rev-parse", "main")
    capsys.readouterr()

    assert run("sync") == 0
    captured = capsys.readouterr()
    # It renders, and it reaches the remote — both, as it always did.
    relative = idea.relative_to(configured.store).as_posix()
    assert git("--git-dir", str(bare), "rev-parse", "main") != head
    assert "marker-7a1b" in git("--git-dir", str(bare), "show", f"main:{relative}")
    inbox = next(p for p in remote_files(bare) if p.endswith("inbox.md"))
    assert "a thought the alias should send" in git(
        "--git-dir", str(bare), "show", f"main:{inbox}")
    # Silent on success, and in particular not deprecated.
    assert captured.err == ""
    assert "deprecated" not in captured.out


def test_sync_on_the_workdir_backend_is_just_a_render(user, workdir, capsys):
    """The other half of "unchanged", and the ordinary case.

    On `workdir` there is nothing to bring into step, so a sync renders and
    stops — exactly what `szsdlc sync` did in a working-tree project before
    any of this.
    """
    assert run("init") == 0
    assert run("capture", "a thought in the working tree") == 0
    for view in Path(workdir).rglob("inbox.md"):
        view.write_text("stale\n")  # as an interrupted turn leaves it
    capsys.readouterr()

    # A success, not a refusal: every project has a backend, so `szsdlc sync`
    # works everywhere, and here the render is the whole of it.
    assert run("sync", "--verbose") == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    inbox = next(Path(workdir).rglob("inbox.md"))
    assert "a thought in the working tree" in inbox.read_text()


def test_render_never_reaches_the_remote(configured, bare, workdir, capsys):
    """`render` is the one command documented not to touch the network.

    It writes generated files, so the post-command sync would happily commit
    and push them — which would make "no network" false for the only command
    that claims it. The rendered files wait for the next commit or sync.
    """
    run("init")
    assert run("capture", "something to render a view for") == 0
    # The capture has already been sent; this is the state `render` starts from.
    head = git("--git-dir", str(bare), "rev-parse", "main")
    # A source written straight into the store, so a redraw is genuinely owed
    # and `render` has something of its own to write.
    idea = next((configured.store / "ideas").rglob("*.md"))
    idea.write_text(idea.read_text().replace("something to render a view for",
                                             "a title only render can propagate"))

    assert run("render") == 0
    assert git("status", "--porcelain", cwd=configured.store) != ""  # written, not sent
    assert git("--git-dir", str(bare), "rev-parse", "main") == head

    # Not stranded: the next sync sends what render wrote.
    assert run("sync") == 0
    assert git("--git-dir", str(bare), "rev-parse", "main") != head


def test_a_terminal_command_renders_before_it_sends(configured, bare, workdir, capsys):
    """A terminal command is its own commit boundary, so it renders first.

    Without this the remote received entities whose generated views still
    described the project as it was before them — and if the next sync never
    came, it kept them.
    """
    run("init")
    assert run("capture", "a thought the inbox should show") == 0

    inbox = next(p for p in remote_files(bare) if p.endswith("inbox.md"))
    assert "a thought the inbox should show" in git(
        "--git-dir", str(bare), "show", f"main:{inbox}")


def test_an_explicit_sync_renders_before_it_sends(configured, bare, workdir, capsys):
    """Sending a stale view records a project as something it is not."""
    run("init")
    assert run("capture", "explicit backend sync") == 0
    for view in configured.root.rglob("inbox.md"):
        view.write_text("stale\n")

    assert run("sync") == 0
    inbox = next(p for p in remote_files(bare) if p.endswith("inbox.md"))
    assert "explicit backend sync" in git("--git-dir", str(bare), "show", f"main:{inbox}")


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
    assert (configured.store / ".szsdlc" / "config.yml").read_text() == "project:\n  name: ours\n"
    assert not (configured.store / ".git" / "rebase-merge").exists()
    assert not (configured.store / ".git" / "rebase-apply").exists()
    assert git("status", "--porcelain", cwd=configured.store) == ""


def test_a_rebase_that_times_out_is_aborted(configured, bare, workdir, tmp_path,
                                           monkeypatch, capsys):
    from szsdlc.backend import git as git_backend

    run("init")
    other = tmp_path / "other"
    git("clone", "--quiet", str(bare), str(other))
    (other / "theirs.md").write_text("x")
    git("add", ".", cwd=other)
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "theirs", cwd=other)
    git("push", "-q", "origin", "HEAD:main", cwd=other)
    (configured.store / "ours.md").write_text("y")

    real = git_backend.GitBackend._git

    def slow_rebase(self, *args, **kwargs):
        if args[:1] == ("rebase",) and "--abort" not in args:
            # Leave a rebase in progress, as one killed by the timeout would.
            real(self, "rebase", "--quiet", "--exec", "false", args[-1], check=False,
                 identity=True)
            raise git_backend.GitError(list(args), None, "timed out after 45s")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(git_backend.GitBackend, "_git", slow_rebase)
    report = backend.sync(configured, "test")
    assert not report.ok and "timed out" in report.problem
    assert not (configured.store / ".git" / "rebase-merge").exists()
    assert (configured.store / "ours.md").read_text() == "y"


def test_an_unreachable_remote_never_fails_the_command(configured, bare, workdir, capsys):
    run("init")
    bare.rename(bare.parent / "gone.git")
    assert run("capture", "offline idea") == 0
    assert "szsdlc sync" in capsys.readouterr().err
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


def test_a_repository_without_origin_is_refused(user, bare, workdir, tmp_path):
    clone = tmp_path / "lonely"
    git("init", "--quiet", str(clone))
    write_settings(user, {"SZSDLC_BACKEND": "git", f"{GIT}URL": str(bare),
                          f"{GIT}CLONE_PATH": str(clone)})
    with pytest.raises(ConfigError, match="no origin"):
        backend.project_root(workdir)


def test_a_clone_on_another_branch_is_refused(user, bare, workdir, tmp_path, capsys):
    clone = tmp_path / "clone"
    write_settings(user, {"SZSDLC_BACKEND": "git", f"{GIT}URL": str(bare),
                          f"{GIT}CLONE_PATH": str(clone)})
    assert run("init") == 0
    write_settings(workdir / ".claude" / "settings.local.json", {f"{GIT}BRANCH": "tracker"})
    with pytest.raises(ConfigError, match="not tracker"):
        backend.project_root(workdir)
    assert "tracker" not in git("--git-dir", str(bare), "branch", "--list")


def test_a_clone_containing_the_working_tree_is_refused(user, workdir, tmp_path):
    origin = tmp_path / "origin.git"
    git("init", "--bare", "--quiet", str(origin))
    git("init", "--quiet", str(workdir))
    git("remote", "add", "origin", str(origin), cwd=workdir)
    (workdir / "private.txt").write_text("not for the backend")
    write_settings(workdir / ".claude" / "settings.json",
                   {"SZSDLC_BACKEND": "git", f"{GIT}URL": str(origin), f"{GIT}CLONE_PATH": "."})
    with pytest.raises(ConfigError, match="contains the working tree"):
        backend.project_root(workdir / "sub")
    assert git("--git-dir", str(origin), "rev-list", "--all") == ""


def test_an_option_shaped_url_is_refused(user, workdir):
    write_settings(user, {"SZSDLC_BACKEND": "git",
                          f"{GIT}URL": "--upload-pack=touch /tmp/pwned"})
    with pytest.raises(ConfigError, match="not a repository URL"):
        backend.resolve(workdir)


def test_a_subdir_symlinked_out_of_the_clone_is_refused(user, bare, workdir, tmp_path, capsys):
    seed = tmp_path / "seed"
    git("clone", "--quiet", str(bare), str(seed))
    (seed / "escape").symlink_to(tmp_path / "outside")
    git("add", ".", cwd=seed)
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "seed", cwd=seed)
    git("push", "-q", "origin", "HEAD:main", cwd=seed)
    write_settings(user, {"SZSDLC_BACKEND": "git", f"{GIT}URL": str(bare),
                          f"{GIT}SUBDIR": "escape"})
    with pytest.raises(ConfigError, match="outside the clone"):
        backend.project_root(workdir)


def test_ignored_project_files_are_reported_not_dropped_silently(configured, bare, workdir,
                                                                 capsys):
    run("init")
    (configured.store / ".gitignore").write_text("secret-notes/\n")
    (configured.store / "secret-notes").mkdir()
    (configured.store / "secret-notes" / "a.md").write_text("x")
    report = backend.sync(configured, "test")
    assert not report.ok and "secret-notes/" in report.problem and "ignored" in report.problem
    assert report.outgoing  # everything else still went out
    assert "secret-notes/a.md" not in remote_files(bare)


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

    # `workdir` is a backend like the others, so syncing reports a no-op
    # rather than refusing — and a no-op is what it must be. The repository is
    # the user's: szsdlc renders into it and stops there.
    report = backend.sync_at(tmp_path, "x")
    assert report.ok and not report.incoming and not report.outgoing
    assert "No commits yet" in git("status", cwd=tmp_path)


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


def test_an_edit_records_nothing_and_the_turn_end_commits_it(configured, bare, workdir,
                                                             capsys):
    """An edit is not a unit of history. The turn is the boundary."""
    run("init")
    run("capture", "one")
    pushed = git("--git-dir", str(bare), "rev-parse", "main")
    idea = next((configured.store / "ideas").rglob("*.md"))

    idea.write_text(idea.read_text() + "\nmore\n")
    payload = {"cwd": str(workdir), "tool_input": {"file_path": str(idea)}}
    assert hooks.post_edit(payload) == 0
    # Nothing committed, and nothing lost: the edit is sitting in the store.
    assert git("status", "--porcelain", cwd=configured.store) != ""
    assert git("--git-dir", str(bare), "rev-parse", "main") == pushed

    hooks.stop({"cwd": str(workdir)})
    assert git("status", "--porcelain", cwd=configured.store) == ""
    assert git("--git-dir", str(bare), "rev-parse", "main") != pushed
    assert "szsdlc: turn end" in git("--git-dir", str(bare), "log", "--format=%s", "main")


def test_a_whole_turn_is_one_commit(configured, bare, workdir, monkeypatch, capsys):
    """Twelve edits and three commands are one change to the project.

    The frequency is the point: a commit per edit made the log a transcript of
    the tool calls that produced a turn rather than a record of what it changed.
    """
    run("init")
    before_head = git("--git-dir", str(bare), "rev-parse", "main")
    before = len(git("--git-dir", str(bare), "log", "--format=%s", "main").splitlines())
    monkeypatch.setenv("CLAUDECODE", "1")

    for n in range(3):
        assert run("capture", f"thought {n}") == 0
    edited = sorted((configured.store / "ideas").rglob("*.md"))
    assert edited, "nothing was captured to edit"
    for idea in edited:
        idea.write_text(idea.read_text() + "\nmarker-9f3c\n")
        hooks.post_edit({"cwd": str(workdir), "tool_input": {"file_path": str(idea)}})
    # Still nothing recorded, and the remote is exactly where it was: the ref
    # is compared, not merely asked for, so a commit made here would show.
    assert git("--git-dir", str(bare), "rev-parse", "main") == before_head
    assert git("status", "--porcelain", cwd=configured.store) != ""

    hooks.stop({"cwd": str(workdir)})
    after = git("--git-dir", str(bare), "log", "--format=%s", "main").splitlines()
    assert len(after) - before == 1, after
    # And that one commit is the turn's work, not an earlier commit that
    # happened to land while the edits were skipped.
    assert git("status", "--porcelain", cwd=configured.store) == ""
    head = git("--git-dir", str(bare), "rev-parse", "main")
    assert head != before_head
    for idea in edited:
        relative = idea.relative_to(configured.store).as_posix()
        assert "marker-9f3c" in git("--git-dir", str(bare), "show", f"main:{relative}")


def test_an_explicit_sync_inside_a_turn_still_pushes(configured, bare, workdir,
                                                     monkeypatch, capsys):
    run("init")
    monkeypatch.setenv("CLAUDECODE", "1")
    (configured.store / "note.md").write_text("x")
    assert run("sync") == 0
    assert "note.md" in remote_files(bare)


def test_a_sync_outside_a_clone_says_so(configured, workdir):
    report = backend.sync(configured, "x")  # never materialized
    assert not report.ok and "not a git clone" in report.problem


def test_the_git_backend_keeps_work_when_the_remote_is_gone(configured, bare, workdir,
                                                            capsys):
    """Degrading to local-only is `git`'s own business, not an operation.

    There is no command for "record but do not send": a sync that cannot
    reach the remote still records what it was given, says so once, and the
    next one sends it. A reachable remote cannot show this — a sync that
    quietly fetched would leave the branch unchanged and pass — so the remote
    is taken away.
    """
    run("init")
    shutil.rmtree(bare)
    (configured.store / "offline.md").write_text("written with no remote")
    capsys.readouterr()

    assert run("sync") != 0  # asked for directly, so the failure is said
    assert "could not reach" in capsys.readouterr().err
    # Recorded regardless: the tree is clean and the work is in history.
    assert git("status", "--porcelain", cwd=configured.store) == ""
    assert "offline.md" in git("show", "--name-only", "--format=", "HEAD",
                               cwd=configured.store)

    # And not stranded: the next sync, once the remote is back, sends it.
    git("init", "--bare", "--quiet", "--initial-branch=main", str(bare))
    assert run("sync") == 0
    assert "offline.md" in remote_files(bare)


def test_an_edit_to_the_config_is_swept_in_at_the_turn_end(configured, bare, workdir,
                                                           capsys):
    """The commit is of the project, not of the sources: the config too."""
    run("init")
    config = configured.store / ".szsdlc" / "config.yml"
    config.write_text(config.read_text() + "\n# tuned\n")
    assert hooks.post_edit({"cwd": str(workdir), "tool_input": {"file_path": str(config)}}) == 0
    assert git("status", "--porcelain", cwd=configured.store) != ""

    hooks.stop({"cwd": str(workdir)})
    assert git("status", "--porcelain", cwd=configured.store) == ""
    assert "# tuned" in git("--git-dir", str(bare), "show", "main:.szsdlc/config.yml")


def test_an_edit_outside_the_project_commits_nothing(configured, bare, workdir, capsys):
    run("init")
    (configured.store / "loose.md").write_text("x")
    elsewhere = workdir / "code.py"
    elsewhere.write_text("x")
    assert hooks.post_edit({"cwd": str(workdir),
                            "tool_input": {"file_path": str(elsewhere)}}) == 0
    assert "loose.md" in git("status", "--porcelain", cwd=configured.store)


def test_the_edit_hook_never_reaches_the_backend(configured, workdir, monkeypatch, capsys):
    """It renders and nothing else, so it has no backend failure to report.

    The hook used to commit here, which put a git invocation on the critical
    path of every write — and an `index.lock` collision in Claude's context for
    something nobody asked for.
    """
    from szsdlc.backend import git as git_backend

    run("init")
    config = configured.store / ".szsdlc" / "config.yml"
    config.write_text(config.read_text() + "\n# edited\n")

    def explode(*args, **kwargs):
        raise AssertionError("the edit hook reached the backend")

    monkeypatch.setattr(backend, "sync_at", explode)
    monkeypatch.setattr(backend, "sync", explode)
    monkeypatch.setattr(git_backend.GitBackend, "sync", explode)
    capsys.readouterr()
    assert hooks.post_edit({"cwd": str(workdir),
                            "tool_input": {"file_path": str(config)}}) == 0
    assert capsys.readouterr().out == ""
    # The wrappers are not the only route to a commit, so assert the outcome
    # too: an edit leaves the store dirty, whichever way it got there.
    assert git("status", "--porcelain", cwd=configured.store) != ""


def test_an_unexpected_sync_failure_is_still_reported(configured, workdir, monkeypatch, capsys):
    run("init")

    def explode(*args, **kwargs):
        raise PermissionError("locks directory is read-only")

    monkeypatch.setattr(backend, "sync_at", explode)
    capsys.readouterr()
    assert hooks.user_prompt_submit({"cwd": str(workdir)}) == 0
    out = capsys.readouterr().out
    assert "szsdlc sync: PermissionError: locks directory is read-only" in out


def test_session_start_describes_the_state_after_the_pull(configured, bare, workdir,
                                                          tmp_path, monkeypatch, capsys):
    run("init")
    other = tmp_path / "other"
    git("clone", "--quiet", str(bare), str(other))
    config = other / ".szsdlc" / "config.yml"
    config.write_text(config.read_text() + "\n# from the other machine\n")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "theirs", cwd=other)
    git("push", "-q", "origin", "HEAD:main", cwd=other)

    seen = []
    real = hooks._load
    monkeypatch.setattr(hooks, "_load", lambda where: seen.append(real(where)) or seen[-1])
    assert hooks.session_start({"cwd": str(workdir)}) == 0
    assert len(seen) == 2  # loaded again once the pull changed things
    session = seen[-1][0]
    assert "from the other machine" in (session.config.root / ".szsdlc" / "config.yml").read_text()


def test_the_turn_end_validates_what_it_pulled(configured, bare, workdir, tmp_path, capsys):
    run("init")
    assert hooks.stop({"cwd": str(workdir)}) == hooks.EXIT_OK
    other = tmp_path / "other"
    git("clone", "--quiet", str(bare), str(other))
    (other / "ideas").mkdir(exist_ok=True)
    (other / "ideas" / "IDEA-0099.md").write_text(
        "---\nid: IDEA-0099\nstatus: nonsense\ncaptured: 2026-01-01\n---\nbad\n")
    git("add", ".", cwd=other)
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "theirs", cwd=other)
    git("push", "-q", "origin", "HEAD:main", cwd=other)
    capsys.readouterr()
    assert hooks.stop({"cwd": str(workdir)}) == hooks.EXIT_BLOCK
    assert "IDEA-0099" in capsys.readouterr().err


def test_prompt_hook_reports_a_failed_sync_into_context(configured, bare, workdir, capsys):
    run("init")
    bare.rename(bare.parent / "gone.git")
    capsys.readouterr()
    hooks.dispatch("user-prompt-submit", io.StringIO(json.dumps({"cwd": str(workdir)})))
    assert "szsdlc sync" in capsys.readouterr().out


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


def test_an_explicit_sync_runs_on_demand(configured, bare, workdir, capsys):
    run("init")
    (configured.store / "note.md").write_text("x")
    assert run("sync") == 0
    assert "note.md" in remote_files(bare)


def test_an_explicit_sync_reports_a_failure(configured, bare, workdir, capsys):
    """Asked for directly, so answered honestly: a failure refuses. The same
    failure under another command is a warning, because that command worked."""
    run("init")
    (configured.store / "note.md").write_text("x")
    bare.rename(bare.parent / "gone.git")
    assert run("sync") == 3
    assert "could not reach" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Second review round
# ---------------------------------------------------------------------------


def push_from_other(bare: Path, tmp_path: Path, change, message: str = "theirs") -> None:
    other = tmp_path / f"other-{message}"
    git("clone", "--quiet", str(bare), str(other))
    change(other)
    git("add", "-A", cwd=other)
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", message, cwd=other)
    git("push", "-q", "origin", "HEAD:main", cwd=other)


def test_a_remote_helper_url_never_runs_the_helper(user, workdir, tmp_path, monkeypatch):
    helpers = tmp_path / "bin"
    helpers.mkdir()
    marker = tmp_path / "ran"
    helper = helpers / "git-remote-evil"
    helper.write_text(f"#!/bin/sh\ntouch {marker}\n")
    helper.chmod(0o755)
    monkeypatch.setenv("PATH", f"{helpers}:{__import__('os').environ['PATH']}")
    write_settings(user, {"SZSDLC_BACKEND": "git", f"{GIT}URL": "evil::whatever"})
    with pytest.raises(ConfigError, match="remote helper"):
        backend.project_root(workdir)
    assert not marker.exists()

    # And git itself refuses it, even where the user's git config allows it.
    from szsdlc.backend.git import GitBackend

    gitconfig = tmp_path / "gitconfig"
    gitconfig.write_text("[protocol \"evil\"]\n\tallow = always\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    with pytest.raises(ConfigError, match="could not clone"):
        GitBackend("evil::whatever", tmp_path / "clone").materialize()
    assert not marker.exists()


def test_credentials_in_a_query_are_never_shown():
    from szsdlc.backend.git import redact

    assert redact("https://host/t.git?access_token=s3cret") == "https://host/t.git?***"


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


def test_a_first_clone_that_fails_is_said_at_session_start(user, workdir, tmp_path, capsys):
    write_settings(user, {"SZSDLC_BACKEND": "git", f"{GIT}URL": str(tmp_path / "nowhere")})
    assert hooks.session_start({"cwd": str(workdir)}) == 0
    assert "szsdlc backend: " in capsys.readouterr().out


def test_no_project_and_no_backend_stays_silent(workdir, capsys):
    assert hooks.session_start({"cwd": str(workdir)}) == 0
    assert hooks.user_prompt_submit({"cwd": str(workdir)}) == 0
    assert capsys.readouterr().out == ""


def test_a_clone_killed_part_way_is_cleared_for_the_next_try(user, bare, workdir, tmp_path,
                                                            monkeypatch):
    from szsdlc.backend import git as git_backend

    clone = tmp_path / "clone"
    write_settings(user, {"SZSDLC_BACKEND": "git", f"{GIT}URL": str(bare),
                          f"{GIT}CLONE_PATH": str(clone)})
    real = git_backend.GitBackend._git

    def dies_mid_clone(self, *args, **kwargs):
        if args[:1] == ("clone",):
            clone.mkdir()
            (clone / "partial").write_text("x")
            raise git_backend.GitError(list(args), None, "timed out after 45s")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(git_backend.GitBackend, "_git", dies_mid_clone)
    with pytest.raises(ConfigError, match="could not clone"):
        backend.project_root(workdir)
    assert not clone.exists()
    monkeypatch.setattr(git_backend.GitBackend, "_git", real)
    assert backend.project_root(workdir) == clone


def test_a_restored_clone_whose_origin_moved_is_not_synced(configured, bare, workdir,
                                                           tmp_path, capsys):
    run("init")
    elsewhere = tmp_path / "elsewhere.git"
    git("init", "--bare", "--quiet", str(elsewhere))
    git("remote", "set-url", "origin", str(elsewhere), cwd=configured.store)
    (configured.store / "note.md").write_text("x")
    report = backend.sync_at(configured.store, "test")
    assert not report.ok and "not a clone of" in report.problem
    assert git("--git-dir", str(elsewhere), "rev-list", "--all") == ""


def test_without_a_branch_the_clone_must_be_on_the_remote_default(user, bare, workdir,
                                                                  tmp_path, capsys):
    clone = tmp_path / "clone"
    write_settings(user, {"SZSDLC_BACKEND": "git", f"{GIT}URL": str(bare),
                          f"{GIT}CLONE_PATH": str(clone)})
    assert run("init") == 0
    git("remote", "set-head", "origin", "main", cwd=clone)
    git("checkout", "-q", "-b", "side", cwd=clone)
    with pytest.raises(ConfigError, match="not main"):
        backend.project_root(workdir)


def test_an_unreadable_registry_is_never_overwritten(user, workdir, tmp_path):
    from szsdlc.backend.dir import DirBackend

    path = backend._registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(ConfigError, match="cannot be read"):
        backend._register(DirBackend(tmp_path / "d"))
    assert path.read_text(encoding="utf-8") == "{broken"


def test_a_filesystem_failure_in_the_backend_never_fails_the_command(configured, workdir,
                                                                     monkeypatch, capsys):
    run("init")

    def explode(*args, **kwargs):
        raise PermissionError("locks directory is read-only")

    monkeypatch.setattr(backend, "sync_at", explode)
    assert run("capture", "still works") == 0
    assert "read-only" in capsys.readouterr().err


def test_local_git_work_fits_inside_the_edit_hook():
    from szsdlc.backend import git as git_backend

    hooks_json = json.loads((Path(__file__).parent.parent / "hooks" / "hooks.json").read_text())
    post = hooks_json["hooks"]["PostToolUse"][0]["hooks"][0]["timeout"]
    # add, diff, commit, plus the origin and branch checks before them.
    assert git_backend.LOCAL_TIMEOUT * 3 < post


def test_an_explicit_sync_reaches_the_remote_once(configured, bare, workdir,
                                                  monkeypatch, capsys):
    from szsdlc.backend import git as git_backend

    run("init")
    run("sync")
    calls = []
    real = git_backend.GitBackend.sync
    monkeypatch.setattr(git_backend.GitBackend, "sync",
                        lambda self, reason: calls.append(reason) or real(self, reason))
    assert run("sync") == 0
    assert calls == ["sync"]


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


def test_the_turn_end_redraws_what_it_pulled(configured, bare, workdir, tmp_path, capsys):
    run("init")
    assert hooks.stop({"cwd": str(workdir)}) == hooks.EXIT_OK

    def add_idea(other):
        (other / "ideas").mkdir(exist_ok=True)
        (other / "ideas" / "IDEA-0042.md").write_text(
            "---\nid: IDEA-0042\nstatus: inbox\ncaptured: 2026-01-01\n---\nfrom afar\n")

    push_from_other(bare, tmp_path, add_idea)
    capsys.readouterr()
    assert hooks.stop({"cwd": str(workdir)}) == hooks.EXIT_OK, capsys.readouterr().err
    assert git("status", "--porcelain", cwd=configured.store) == ""


def test_a_pulled_config_that_breaks_the_project_blocks_the_turn(configured, bare, workdir,
                                                                 tmp_path, capsys):
    run("init")
    push_from_other(bare, tmp_path,
                    lambda other: (other / ".szsdlc" / "config.yml").write_text("{nope: [\n"))
    capsys.readouterr()
    assert hooks.stop({"cwd": str(workdir)}) == hooks.EXIT_BLOCK
    assert "after syncing" in capsys.readouterr().err


def test_a_pulled_symlink_out_of_the_clone_stops_the_sync(user, bare, workdir, tmp_path,
                                                          capsys):
    write_settings(user, {"SZSDLC_BACKEND": "git", f"{GIT}URL": str(bare),
                          f"{GIT}SUBDIR": "proj"})
    assert run("init") == 0
    chosen = backend.resolve(workdir)

    def swap(other):
        import shutil

        shutil.rmtree(other / "proj")
        (other / "proj").symlink_to(tmp_path / "outside")

    push_from_other(bare, tmp_path, swap)
    (chosen.store / "mine.md").write_text("x")
    report = backend.sync(chosen, "test")
    assert not report.ok and "outside the clone" in report.problem
    assert "mine.md" not in remote_files(bare)


# ---------------------------------------------------------------------------
# Third review round
# ---------------------------------------------------------------------------


def test_a_clone_that_pushes_elsewhere_is_refused(configured, bare, workdir, tmp_path, capsys):
    run("init")
    elsewhere = tmp_path / "elsewhere.git"
    git("init", "--bare", "--quiet", str(elsewhere))
    git("config", "remote.origin.pushurl", str(elsewhere), cwd=configured.store)
    (configured.store / "note.md").write_text("x")
    report = backend.sync(configured, "test")
    assert not report.ok and "pushes to" in report.problem
    assert git("--git-dir", str(elsewhere), "rev-list", "--all") == ""


def test_the_clone_config_is_private(configured, workdir, capsys):
    run("init")
    assert (configured.store / ".git" / "config").stat().st_mode & 0o077 == 0


def test_a_symlinked_metadata_directory_is_refused(user, bare, workdir, tmp_path, capsys):
    push_from_other(bare, tmp_path,
                    lambda other: (other / ".szsdlc").symlink_to(tmp_path / "outside"))
    write_settings(user, {"SZSDLC_BACKEND": "git", f"{GIT}URL": str(bare)})
    with pytest.raises(ConfigError, match=".szsdlc directory"):
        backend.project_root(workdir)
    assert not (tmp_path / "outside").exists()


@pytest.mark.parametrize("url, secret", [
    ("https://tok@[::1/t.git", "tok"),
    ("https://host/t.git?\x1b]0;pwn\x07", "\x1b"),
])
def test_redaction_fails_closed(url, secret):
    from szsdlc.backend.git import redact

    assert secret not in redact(url)


def test_credentials_in_git_errors_are_scrubbed_in_any_form():
    from szsdlc.backend.git import scrub

    assert scrub("fatal: unable to access 'https://user:pw@host/x/': 403") == \
        "fatal: unable to access 'https://***@host/x/': 403"


def test_backend_show_never_prints_control_characters(user, workdir, capsys):
    write_settings(user, {"SZSDLC_BACKEND": "git", f"{GIT}URL": "/srv/\x1b[2Jt.git"})
    assert run("backend") == 0
    assert "\x1b" not in capsys.readouterr().out


def test_without_a_branch_sync_learns_the_remote_default(user, bare, workdir, tmp_path,
                                                         capsys):
    clone = tmp_path / "clone"
    write_settings(user, {"SZSDLC_BACKEND": "git", f"{GIT}URL": str(bare),
                          f"{GIT}CLONE_PATH": str(clone)})
    assert run("init") == 0  # cloned while empty: no origin/HEAD yet
    assert "origin/HEAD" not in git("branch", "-r", cwd=clone)
    git("checkout", "-q", "-b", "side", cwd=clone)
    (clone / "stray.md").write_text("x")
    report = backend.sync_at(clone, "test")
    assert not report.ok and "not main" in report.problem
    assert "side" not in git("--git-dir", str(bare), "branch", "--list")


def test_extensions_never_hide_the_builtin_backends(monkeypatch):
    monkeypatch.setattr(backend, "BACKENDS", {"custom": object})
    monkeypatch.setattr(backend, "_builtins_loaded", False)
    assert backend.backend_class("git").type == "git"
    assert "custom" in backend.BACKENDS


def test_a_sync_never_outlasts_its_budget(configured, workdir, monkeypatch, capsys):
    from szsdlc.backend import git as git_backend

    run("init")
    monkeypatch.setattr(git_backend, "SYNC_BUDGET", 0)
    report = backend.sync(configured, "test")
    assert not report.ok and "out of time" in report.problem


def test_a_broken_config_is_still_committed_and_synced(configured, bare, workdir, capsys):
    """A config edit that breaks the project is a change like any other.

    It is the one most worth sending: the repair may have to come from the
    machine that can still load the project.
    """
    run("init")
    config = configured.store / ".szsdlc" / "config.yml"
    config.write_text("{nope: [\n")
    assert hooks.post_edit({"cwd": str(workdir), "tool_input": {"file_path": str(config)}}) == 0
    capsys.readouterr()
    assert hooks.stop({"cwd": str(workdir)}) == hooks.EXIT_OK
    assert "szsdlc backend: " in capsys.readouterr().err
    assert "{nope" in git("--git-dir", str(bare), "show", "main:.szsdlc/config.yml")


def test_the_redraw_after_a_pull_is_pushed_too(configured, bare, workdir, tmp_path, capsys):
    run("init")
    assert hooks.stop({"cwd": str(workdir)}) == hooks.EXIT_OK

    def add_idea(other):
        (other / "ideas").mkdir(exist_ok=True)
        (other / "ideas" / "IDEA-0042.md").write_text(
            "---\nid: IDEA-0042\nstatus: inbox\ncaptured: 2026-01-01\n---\nfrom afar\n")

    push_from_other(bare, tmp_path, add_idea, "unrendered")
    assert hooks.stop({"cwd": str(workdir)}) == hooks.EXIT_OK
    assert last_commit(bare) == "szsdlc: redraw after sync"


def test_a_redraw_that_fails_after_a_pull_is_said(configured, bare, workdir, tmp_path,
                                                  monkeypatch, capsys):
    from szsdlc import render

    run("init")
    push_from_other(bare, tmp_path, lambda other: (other / "theirs.md").write_text("x"))

    def broken(*args, **kwargs):
        raise RuntimeError("template missing")

    monkeypatch.setattr(render, "render", broken)
    capsys.readouterr()
    hooks.stop({"cwd": str(workdir)})
    assert "redrawing after the sync failed: RuntimeError: template missing" in \
        capsys.readouterr().err
