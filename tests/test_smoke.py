"""Scaffold smoke tests: the package imports and the console entry point runs."""

import szsdlc
from szsdlc.cli import main


def test_version_is_present():
    assert isinstance(szsdlc.__version__, str)
    assert szsdlc.__version__


def test_version_flag_succeeds(capsys):
    assert main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == szsdlc.__version__


def test_no_args_exits_nonzero_and_says_nothing_on_stdout():
    # Per the command contract, diagnostics go to stderr, never stdout.
    assert main([]) == 2
