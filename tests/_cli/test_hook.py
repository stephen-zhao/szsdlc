"""szsdlc hook: the entry point hooks.json invokes."""

from __future__ import annotations

import io

from szsdlc.cli import main

from test_hooks import seeded


# ---------------------------------------------------------------------------
# The hidden subcommand
# ---------------------------------------------------------------------------


def test_the_hook_command_runs_end_to_end(seeded, capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert main(["-C", str(seeded.root), "hook", "session-start"]) == 0
    assert "entities 2" in capsys.readouterr().out


def test_an_unknown_hook_event_is_refused_compactly(capsys):
    assert main(["hook", "nonsense"]) == 2
    err = capsys.readouterr().err
    assert len(err.strip().splitlines()) <= 3
    assert "usage:" not in err
