"""The `szsdlc` entry point and `szsdlc.cli`, the public shim over `szsdlc._cli`."""

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


def test_the_shim_exports_exactly_its_public_surface():
    """What other code imports from szsdlc.cli, and nothing that drifted in (WI-0057)."""
    import szsdlc.cli as cli
    from szsdlc import _cli

    assert sorted(cli.__all__) == ["COMMANDS", "HIDDEN_COMMANDS", "build_parser",
                                   "compact_help", "main", "run"]
    public = {n for n in vars(cli) if not n.startswith("_") and n not in ("annotations", "sys")}
    assert public == set(cli.__all__)
    assert cli.main is _cli.main and cli.build_parser is _cli.build_parser
