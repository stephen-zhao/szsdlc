"""szsdlc sync: render, then put the project in step with where it is kept."""

from __future__ import annotations

from pathlib import Path

from szsdlc import backend
from szsdlc.ids import IdSpace
from szsdlc.model import load_all

from backend.conftest import GIT, bare, configured, git, remote_files, run, user, workdir, write_settings
from test_render import broken, world


# ---------------------------------------------------------------------------
# Degrade visibly, never silently
# ---------------------------------------------------------------------------


def test_sync_succeeds_against_a_broken_graph(run, broken):
    """Views are most needed exactly when something is wrong."""
    code, output, err = run("sync")
    assert code == 0 and output == "" and err == ""
    assert (broken.views_dir / "board.md").is_file()


# ---------------------------------------------------------------------------
# compare() and sync
# ---------------------------------------------------------------------------


def test_sync_is_silent_on_success_and_verbose_opts_in(run, world):
    """C4 — this runs after every write and at every turn end."""
    code, output, err = run("sync")
    assert (code, output, err) == (0, "", "")

    entity = load_all(world, IdSpace(world)).by_text("WI-0001")
    entity.set_tags(["something-new"])
    entity.save()
    _, output, _ = run("sync", "--verbose")
    assert "views/tag-index.md" in output


def test_there_is_no_check_flag(run, world):
    """Staleness is a validate rule; CI asks one question, not two."""
    code, _, err = run("sync", "--check")
    assert code != 0
    assert "usage:" not in err


# ---------------------------------------------------------------------------
# git: end to end
# ---------------------------------------------------------------------------


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


def test_an_explicit_sync_renders_before_it_sends(configured, bare, workdir, capsys):
    """Sending a stale view records a project as something it is not."""
    run("init")
    assert run("capture", "explicit backend sync") == 0
    for view in configured.root.rglob("inbox.md"):
        view.write_text("stale\n")

    assert run("sync") == 0
    inbox = next(p for p in remote_files(bare) if p.endswith("inbox.md"))
    assert "explicit backend sync" in git("--git-dir", str(bare), "show", f"main:{inbox}")


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


# ---------------------------------------------------------------------------
# Hooks and the backend command
# ---------------------------------------------------------------------------


def test_an_explicit_sync_inside_a_turn_still_pushes(configured, bare, workdir,
                                                     monkeypatch, capsys):
    run("init")
    monkeypatch.setenv("CLAUDECODE", "1")
    (configured.store / "note.md").write_text("x")
    assert run("sync") == 0
    assert "note.md" in remote_files(bare)


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
