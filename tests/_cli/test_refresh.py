"""szsdlc refresh: the latest from where the project is kept, sending nothing (WI-0047)."""

from __future__ import annotations

import pytest
import shutil
from pathlib import Path

from backend.conftest import DIR, GIT, bare, configured, git, run, user, workdir, write_settings


def _push_from_elsewhere(bare: Path, tmp_path: Path, name: str, text: str) -> None:
    other = tmp_path / f"other-{name}"
    git("clone", "--quiet", str(bare), str(other))
    (other / name).write_text(text)
    git("add", ".", cwd=other)
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", name, cwd=other)
    git("push", "-q", "origin", "HEAD:main", cwd=other)


# ---------------------------------------------------------------------------
# refresh — the latest becomes current, nothing is sent (WI-0047)
# ---------------------------------------------------------------------------


def test_refresh_with_nothing_new_says_so_and_sends_nothing(configured, bare, workdir, capsys):
    run("init")
    head = git("--git-dir", str(bare), "rev-parse", "main")
    capsys.readouterr()
    assert run("refresh") == 0
    assert capsys.readouterr().out == "refresh: nothing new\n"
    assert git("--git-dir", str(bare), "rev-parse", "main") == head


def test_refresh_takes_what_arrived_and_redraws(configured, bare, workdir, tmp_path, capsys):
    run("init")
    other = tmp_path / "other"
    git("clone", "--quiet", str(bare), str(other))
    ideas = other / "ideas"
    ideas.mkdir(exist_ok=True)
    (ideas / "index.md").write_text(
        "## IDEA-0001 — from elsewhere\n\n---\nid: IDEA-0001\nstatus: inbox\n"
        "captured: 2026-11-23\n---\n\nfrom elsewhere\n")
    git("add", ".", cwd=other)
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "theirs", cwd=other)
    git("push", "-q", "origin", "HEAD:main", cwd=other)
    head = git("--git-dir", str(bare), "rev-parse", "main")
    capsys.readouterr()

    assert run("refresh") == 0
    assert capsys.readouterr().out == "refresh: changes arrived\n"
    assert (configured.store / "ideas" / "index.md").is_file()
    inbox = next((configured.store / "views").glob("inbox.md")).read_text()
    assert "from elsewhere" in inbox  # rendered after it arrived
    assert git("--git-dir", str(bare), "rev-parse", "main") == head  # nothing sent


@pytest.mark.parametrize("local", ["dirty", "unpushed"])
def test_refresh_refuses_to_replace_local_state_without_force(configured, bare, workdir,
                                                              tmp_path, capsys, local):
    run("init")
    _push_from_elsewhere(bare, tmp_path, "theirs.md", "theirs")
    mine = configured.store / "mine.md"
    mine.write_text("mine")
    if local == "unpushed":
        git("add", ".", cwd=configured.store)
        git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "mine",
            cwd=configured.store)
    capsys.readouterr()

    assert run("refresh") == 3
    err = capsys.readouterr().err
    assert "would replace them" in err and "Fix: szsdlc refresh --force" in err
    assert mine.read_text() == "mine"
    assert not (configured.store / "theirs.md").exists()


def test_refresh_force_replaces_and_keeps_what_it_replaced(configured, bare, workdir,
                                                          tmp_path, capsys):
    run("init")
    _push_from_elsewhere(bare, tmp_path, "theirs.md", "theirs")
    store = configured.store
    (store / "committed.md").write_text("committed, never pushed")
    git("add", ".", cwd=store)
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "local", cwd=store)
    (store / "dirty.md").write_text("uncommitted")
    head = git("--git-dir", str(bare), "rev-parse", "main")
    capsys.readouterr()

    assert run("refresh", "--force") == 0
    out = capsys.readouterr().out
    assert out.startswith("refresh: changes arrived; local state replaced (kept at refs/szsdlc/replaced/")
    ref = out.split("kept at ")[1].rstrip(")\n")

    assert (store / "theirs.md").is_file()
    assert not (store / "committed.md").exists() and not (store / "dirty.md").exists()
    assert git("rev-parse", "HEAD", cwd=store) == git("rev-parse", "origin/main", cwd=store)
    # Kept, and recoverable from the named ref.
    kept = git("ls-tree", "-r", "--name-only", ref, cwd=store).split()
    assert "committed.md" in kept and "dirty.md" in kept
    # Nothing was sent: neither the kept state nor its ref.
    assert git("--git-dir", str(bare), "rev-parse", "main") == head
    assert "replaced" not in git("--git-dir", str(bare), "for-each-ref")


def test_refresh_from_an_unreachable_remote_replaces_nothing(configured, bare, workdir, capsys):
    run("init")
    mine = configured.store / "mine.md"
    mine.write_text("mine")
    shutil.rmtree(bare)
    capsys.readouterr()
    assert run("refresh", "--force") == 3
    err = capsys.readouterr().err
    assert "could not reach" in err and "nothing was replaced" in err
    assert mine.read_text() == "mine"


def test_refresh_on_workdir_and_dir_is_a_no_op(user, workdir, tmp_path, capsys):
    run("init")
    capsys.readouterr()
    assert run("refresh") == 0
    assert capsys.readouterr().out == "refresh: nothing new\n"

    write_settings(user, {"SZSDLC_BACKEND": "dir", f"{DIR}PATH": str(tmp_path / "kept")})
    run("init")
    capsys.readouterr()
    assert run("refresh") == 0
    assert capsys.readouterr().out == "refresh: nothing new\n"
