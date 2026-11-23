"""szsdlc render: regenerating every view and record, never reaching a remote."""

from __future__ import annotations

from szsdlc.cli import main

from test_backend import GIT, bare, configured, git, run, user, workdir, write_settings
from test_hooks import seeded


# ---------------------------------------------------------------------------
# Stop — the turn-end sync, and the setting that governs it
# ---------------------------------------------------------------------------


def test_an_explicit_sync_still_overwrites_a_hand_edited_view(seeded, capsys):
    """The counterpart: typing the command means what it has always meant."""
    board = seeded.views_dir / "board.md"
    board.write_text(board.read_text("utf-8") + "\nA note somebody typed here.\n",
                     encoding="utf-8")

    main(["-C", str(seeded.root), "render"])
    capsys.readouterr()
    assert "A note somebody typed here." not in board.read_text("utf-8")


# ---------------------------------------------------------------------------
# git: end to end
# ---------------------------------------------------------------------------


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
