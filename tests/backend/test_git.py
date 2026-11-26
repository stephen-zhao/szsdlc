"""szsdlc.backend.git: resolution, and the backend end to end against a bare remote on disk."""

from __future__ import annotations

import io
import json
import pytest
import shutil
from pathlib import Path

from szsdlc import backend, hooks
from szsdlc.errors import ConfigError

from .conftest import DIR, GIT

from .conftest import DIR, GIT, git, last_commit, remote_files, run, write_settings


def push_from_other(bare: Path, tmp_path: Path, change, message: str = "theirs") -> None:
    other = tmp_path / f"other-{message}"
    git("clone", "--quiet", str(bare), str(other))
    change(other)
    git("add", "-A", cwd=other)
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", message, cwd=other)
    git("push", "-q", "origin", "HEAD:main", cwd=other)


# ---------------------------------------------------------------------------
# git: resolution
# ---------------------------------------------------------------------------
def test_the_clone_is_hidden_beside_claude_by_default(isolated_settings, user, workdir):
    write_settings(user, {"SZSDLC_BACKEND": "git", f"{GIT}URL": "git@h:me/track.git"})
    chosen = backend.resolve(workdir)
    assert chosen.project_sdlc_store_dir.parent == isolated_settings / "szsdlc" / "git"
    assert chosen.project_sdlc_store_dir.name.startswith("track-")
    assert chosen.project_sdlc_root == chosen.project_sdlc_store_dir


def test_clone_root_and_path_relocate_it(user, workdir, tmp_path):
    write_settings(user, {"SZSDLC_BACKEND": "git", f"{GIT}URL": "u",
                          f"{GIT}CLONE_ROOT": str(tmp_path / "all")})
    assert backend.resolve(workdir).project_sdlc_store_dir.parent == tmp_path / "all"

    write_settings(workdir / ".claude" / "settings.local.json",
                   {f"{GIT}CLONE_PATH": "../{project}-tracker"})
    assert backend.resolve(workdir).project_sdlc_store_dir == workdir.parent / "myproj-tracker"


def test_subdir_can_name_the_project(user, workdir):
    write_settings(user, {"SZSDLC_BACKEND": "git", f"{GIT}URL": "u",
                          f"{GIT}SUBDIR": "projects/{project}/"})
    chosen = backend.resolve(workdir)
    assert chosen.project_sdlc_root == chosen.project_sdlc_store_dir / "projects" / "myproj"


@pytest.mark.parametrize("subdir", ["../x", "C:/elsewhere", "D:x"])
def test_a_subdir_outside_the_store_is_refused(user, workdir, subdir):
    write_settings(user, {"SZSDLC_BACKEND": "git", f"{GIT}URL": "u", f"{GIT}SUBDIR": subdir})
    with pytest.raises(ConfigError):
        backend.resolve(workdir)


def test_project_names_the_enclosing_checkout_from_a_subdirectory(user, workdir, tmp_path):
    git("init", "--quiet", str(workdir))
    write_settings(user, {"SZSDLC_BACKEND": "dir", f"{DIR}PATH": str(tmp_path / "t" / "{project}")})
    (workdir / "src" / "deep").mkdir(parents=True)
    assert backend.resolve(workdir / "src" / "deep").project_sdlc_root == tmp_path / "t" / "myproj"


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


def test_a_restored_store_keeps_its_subdir(user, bare, workdir, capsys):
    write_settings(user, {"SZSDLC_BACKEND": "git", f"{GIT}URL": str(bare),
                          f"{GIT}SUBDIR": "projects/{project}"})
    assert run("init") == 0
    chosen = backend.resolve(workdir)
    assert backend.project_sdlc_store_for(chosen.project_sdlc_store_dir).project_sdlc_root == chosen.project_sdlc_store_dir / "projects" / "myproj"


# ---------------------------------------------------------------------------
# git: end to end
# ---------------------------------------------------------------------------


def test_init_writes_to_the_clone_and_pushes(configured, bare, workdir, capsys):
    assert run("init") == 0
    assert list(workdir.iterdir()) == []
    assert (configured.project_sdlc_store_dir / ".szsdlc" / "config.yml").is_file()
    assert ".szsdlc/config.yml" in remote_files(bare)


def test_a_command_that_changes_files_pushes_them(configured, bare, workdir, capsys):
    run("init")
    before = len(remote_files(bare))
    assert run("capture", "remember the milk") == 0
    assert len(remote_files(bare)) > before
    assert last_commit(bare) == "szsdlc: capture"


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


def test_a_read_only_command_makes_no_commit(configured, bare, workdir, capsys):
    run("init")
    head = git("--git-dir", str(bare), "rev-parse", "main")
    assert run("context") == 0
    assert git("--git-dir", str(bare), "rev-parse", "main") == head


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

    (configured.project_sdlc_store_dir / ".szsdlc" / "config.yml").write_text("project:\n  name: ours\n")
    report = backend.sync(configured, "test")
    assert not report.ok and "conflict" in report.problem
    assert (configured.project_sdlc_store_dir / ".szsdlc" / "config.yml").read_text() == "project:\n  name: ours\n"
    assert not (configured.project_sdlc_store_dir / ".git" / "rebase-merge").exists()
    assert not (configured.project_sdlc_store_dir / ".git" / "rebase-apply").exists()
    assert git("status", "--porcelain", cwd=configured.project_sdlc_store_dir) == ""


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
    (configured.project_sdlc_store_dir / "ours.md").write_text("y")

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
    assert not (configured.project_sdlc_store_dir / ".git" / "rebase-merge").exists()
    assert (configured.project_sdlc_store_dir / "ours.md").read_text() == "y"


def test_an_unreachable_remote_never_fails_the_command(configured, bare, workdir, capsys):
    run("init")
    bare.rename(bare.parent / "gone.git")
    assert run("capture", "offline idea") == 0
    assert "szsdlc sync" in capsys.readouterr().err
    assert git("status", "--porcelain", cwd=configured.project_sdlc_store_dir) == ""


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
        backend.project_sdlc_root(workdir)


def test_a_clone_of_something_else_is_refused(user, bare, workdir, tmp_path):
    other = tmp_path / "other.git"
    git("init", "--bare", "--quiet", str(other))
    clone = tmp_path / "clone"
    git("clone", "--quiet", str(other), str(clone))
    write_settings(user, {"SZSDLC_BACKEND": "git", f"{GIT}URL": str(bare),
                          f"{GIT}CLONE_PATH": str(clone)})
    with pytest.raises(ConfigError, match="is a clone of"):
        backend.project_sdlc_root(workdir)


def test_a_repository_without_origin_is_refused(user, bare, workdir, tmp_path):
    clone = tmp_path / "lonely"
    git("init", "--quiet", str(clone))
    write_settings(user, {"SZSDLC_BACKEND": "git", f"{GIT}URL": str(bare),
                          f"{GIT}CLONE_PATH": str(clone)})
    with pytest.raises(ConfigError, match="no origin"):
        backend.project_sdlc_root(workdir)


def test_a_clone_on_another_branch_is_refused(user, bare, workdir, tmp_path, capsys):
    clone = tmp_path / "clone"
    write_settings(user, {"SZSDLC_BACKEND": "git", f"{GIT}URL": str(bare),
                          f"{GIT}CLONE_PATH": str(clone)})
    assert run("init") == 0
    write_settings(workdir / ".claude" / "settings.local.json", {f"{GIT}BRANCH": "tracker"})
    with pytest.raises(ConfigError, match="not tracker"):
        backend.project_sdlc_root(workdir)
    assert "tracker" not in git("--git-dir", str(bare), "branch", "--list")


def test_a_clone_containing_the_working_tree_is_refused(user, workdir, tmp_path):
    origin = tmp_path / "origin.git"
    git("init", "--bare", "--quiet", str(origin))
    git("init", "--quiet", str(workdir))
    git("remote", "add", "origin", str(origin), cwd=workdir)
    (workdir / "private.txt").write_text("not for the backend")
    write_settings(workdir / ".claude" / "settings.json",
                   {"SZSDLC_BACKEND": "git", f"{GIT}URL": str(origin), f"{GIT}CLONE_PATH": "."})
    with pytest.raises(ConfigError, match="contains the project asset root"):
        backend.project_sdlc_root(workdir / "sub")
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
        backend.project_sdlc_root(workdir)


def test_ignored_project_files_are_reported_not_dropped_silently(configured, bare, workdir,
                                                                 capsys):
    run("init")
    (configured.project_sdlc_store_dir / ".gitignore").write_text("secret-notes/\n")
    (configured.project_sdlc_store_dir / "secret-notes").mkdir()
    (configured.project_sdlc_store_dir / "secret-notes" / "a.md").write_text("x")
    report = backend.sync(configured, "test")
    assert not report.ok and "secret-notes/" in report.problem and "ignored" in report.problem
    assert report.outgoing  # everything else still went out
    assert "secret-notes/a.md" not in remote_files(bare)


def test_an_unclonable_url_is_a_config_error(user, workdir, tmp_path):
    write_settings(user, {"SZSDLC_BACKEND": "git", f"{GIT}URL": str(tmp_path / "nowhere")})
    with pytest.raises(ConfigError, match="could not clone"):
        backend.project_sdlc_root(workdir)


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
    monkeypatch.chdir(configured.project_sdlc_store_dir)
    assert run("capture", "from inside") == 0
    assert last_commit(bare) == "szsdlc: capture"


# ---------------------------------------------------------------------------
# Hooks and the backend command
# ---------------------------------------------------------------------------
def test_session_start_says_where_the_files_are(configured, workdir, capsys):
    run("init")
    capsys.readouterr()
    assert hooks.session_start({"cwd": str(workdir)}) == 0
    assert f"szsdlc project files: {configured.project_sdlc_store_dir}" in capsys.readouterr().out


def test_an_edit_records_nothing_and_the_turn_end_commits_it(configured, bare, workdir,
                                                             capsys):
    """An edit is not a unit of history. The turn is the boundary."""
    run("init")
    run("capture", "one")
    pushed = git("--git-dir", str(bare), "rev-parse", "main")
    idea = next((configured.project_sdlc_store_dir / "ideas").rglob("*.md"))

    idea.write_text(idea.read_text() + "\nmore\n")
    payload = {"cwd": str(workdir), "tool_input": {"file_path": str(idea)}}
    assert hooks.post_edit(payload) == 0
    # Nothing committed, and nothing lost: the edit is sitting in the store.
    assert git("status", "--porcelain", cwd=configured.project_sdlc_store_dir) != ""
    assert git("--git-dir", str(bare), "rev-parse", "main") == pushed

    hooks.stop({"cwd": str(workdir)})
    assert git("status", "--porcelain", cwd=configured.project_sdlc_store_dir) == ""
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
    edited = sorted((configured.project_sdlc_store_dir / "ideas").rglob("*.md"))
    assert edited, "nothing was captured to edit"
    for idea in edited:
        idea.write_text(idea.read_text() + "\nmarker-9f3c\n")
        hooks.post_edit({"cwd": str(workdir), "tool_input": {"file_path": str(idea)}})
    # Still nothing recorded, and the remote is exactly where it was: the ref
    # is compared, not merely asked for, so a commit made here would show.
    assert git("--git-dir", str(bare), "rev-parse", "main") == before_head
    assert git("status", "--porcelain", cwd=configured.project_sdlc_store_dir) != ""

    hooks.stop({"cwd": str(workdir)})
    after = git("--git-dir", str(bare), "log", "--format=%s", "main").splitlines()
    assert len(after) - before == 1, after
    # And that one commit is the turn's work, not an earlier commit that
    # happened to land while the edits were skipped.
    assert git("status", "--porcelain", cwd=configured.project_sdlc_store_dir) == ""
    head = git("--git-dir", str(bare), "rev-parse", "main")
    assert head != before_head
    for idea in edited:
        relative = idea.relative_to(configured.project_sdlc_store_dir).as_posix()
        assert "marker-9f3c" in git("--git-dir", str(bare), "show", f"main:{relative}")


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
    (configured.project_sdlc_store_dir / "offline.md").write_text("written with no remote")
    capsys.readouterr()

    assert run("sync") != 0  # asked for directly, so the failure is said
    assert "could not reach" in capsys.readouterr().err
    # Recorded regardless: the tree is clean and the work is in history.
    assert git("status", "--porcelain", cwd=configured.project_sdlc_store_dir) == ""
    assert "offline.md" in git("show", "--name-only", "--format=", "HEAD",
                               cwd=configured.project_sdlc_store_dir)

    # And not stranded: the next sync, once the remote is back, sends it.
    git("init", "--bare", "--quiet", "--initial-branch=main", str(bare))
    assert run("sync") == 0
    assert "offline.md" in remote_files(bare)


def test_an_edit_to_the_config_is_swept_in_at_the_turn_end(configured, bare, workdir,
                                                           capsys):
    """The commit is of the project, not of the sources: the config too."""
    run("init")
    config = configured.project_sdlc_store_dir / ".szsdlc" / "config.yml"
    config.write_text(config.read_text() + "\n# tuned\n")
    assert hooks.post_edit({"cwd": str(workdir), "tool_input": {"file_path": str(config)}}) == 0
    assert git("status", "--porcelain", cwd=configured.project_sdlc_store_dir) != ""

    hooks.stop({"cwd": str(workdir)})
    assert git("status", "--porcelain", cwd=configured.project_sdlc_store_dir) == ""
    assert "# tuned" in git("--git-dir", str(bare), "show", "main:.szsdlc/config.yml")


def test_an_edit_outside_the_project_commits_nothing(configured, bare, workdir, capsys):
    run("init")
    (configured.project_sdlc_store_dir / "loose.md").write_text("x")
    elsewhere = workdir / "code.py"
    elsewhere.write_text("x")
    assert hooks.post_edit({"cwd": str(workdir),
                            "tool_input": {"file_path": str(elsewhere)}}) == 0
    assert "loose.md" in git("status", "--porcelain", cwd=configured.project_sdlc_store_dir)


def test_the_edit_hook_never_reaches_the_backend(configured, workdir, monkeypatch, capsys):
    """It renders and nothing else, so it has no backend failure to report.

    The hook used to commit here, which put a git invocation on the critical
    path of every write — and an `index.lock` collision in Claude's context for
    something nobody asked for.
    """
    from szsdlc.backend import git as git_backend

    run("init")
    config = configured.project_sdlc_store_dir / ".szsdlc" / "config.yml"
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
    assert git("status", "--porcelain", cwd=configured.project_sdlc_store_dir) != ""


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


# ---------------------------------------------------------------------------
# Second review round
# ---------------------------------------------------------------------------


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
        backend.project_sdlc_root(workdir)
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


def test_a_first_clone_that_fails_is_said_at_session_start(user, workdir, tmp_path, capsys):
    write_settings(user, {"SZSDLC_BACKEND": "git", f"{GIT}URL": str(tmp_path / "nowhere")})
    assert hooks.session_start({"cwd": str(workdir)}) == 0
    assert "szsdlc backend: " in capsys.readouterr().out


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
        backend.project_sdlc_root(workdir)
    assert not clone.exists()
    monkeypatch.setattr(git_backend.GitBackend, "_git", real)
    assert backend.project_sdlc_root(workdir) == clone


def test_a_restored_clone_whose_origin_moved_is_not_synced(configured, bare, workdir,
                                                           tmp_path, capsys):
    run("init")
    elsewhere = tmp_path / "elsewhere.git"
    git("init", "--bare", "--quiet", str(elsewhere))
    git("remote", "set-url", "origin", str(elsewhere), cwd=configured.project_sdlc_store_dir)
    (configured.project_sdlc_store_dir / "note.md").write_text("x")
    report = backend.sync_at(configured.project_sdlc_store_dir, "test")
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
        backend.project_sdlc_root(workdir)


def test_local_git_work_fits_inside_the_edit_hook():
    from szsdlc.backend import git as git_backend

    hooks_json = json.loads((Path(__file__).parents[2] / "hooks" / "hooks.json").read_text())
    post = hooks_json["hooks"]["PostToolUse"][0]["hooks"][0]["timeout"]
    # add, diff, commit, plus the origin and branch checks before them.
    assert git_backend.LOCAL_TIMEOUT * 3 < post


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
    assert git("status", "--porcelain", cwd=configured.project_sdlc_store_dir) == ""


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
    (chosen.project_sdlc_store_dir / "mine.md").write_text("x")
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
    git("config", "remote.origin.pushurl", str(elsewhere), cwd=configured.project_sdlc_store_dir)
    (configured.project_sdlc_store_dir / "note.md").write_text("x")
    report = backend.sync(configured, "test")
    assert not report.ok and "pushes to" in report.problem
    assert git("--git-dir", str(elsewhere), "rev-list", "--all") == ""


def test_the_clone_config_is_private(configured, workdir, capsys):
    run("init")
    assert (configured.project_sdlc_store_dir / ".git" / "config").stat().st_mode & 0o077 == 0


def test_a_symlinked_metadata_directory_is_refused(user, bare, workdir, tmp_path, capsys):
    push_from_other(bare, tmp_path,
                    lambda other: (other / ".szsdlc").symlink_to(tmp_path / "outside"))
    write_settings(user, {"SZSDLC_BACKEND": "git", f"{GIT}URL": str(bare)})
    with pytest.raises(ConfigError, match=".szsdlc directory"):
        backend.project_sdlc_root(workdir)
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
    config = configured.project_sdlc_store_dir / ".szsdlc" / "config.yml"
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
