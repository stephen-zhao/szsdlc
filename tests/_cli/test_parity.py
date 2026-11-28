"""Parity: every command is an operation, and what the parser makes fits it (WI-0060).

The parser stays hand-written, because its inputs are tuned for agents. This
keeps it honest against the operation layer instead of generating it.
"""

from __future__ import annotations

import pytest

from szsdlc import ops
from szsdlc.cli import HIDDEN_COMMANDS, build_parser
from szsdlc.ops._registry import build_params

#: Command → an argv for it exercising its options, and the operation it runs.
SAMPLES = {
    "capture": (["capture", "a thought", "--type", "idea"], "capture"),
    "refine": (["refine", "IDEA-0001", "--into", "work_item", "--title", "W"], "refine"),
    "inbox": (["inbox", "--limit", "5", "--json"], "inbox"),
    "drop": (["drop", "IDEA-0001", "--reason", "covered"], "drop"),
    "init": (["init", "--name", "demo"], "init"),
    "new": (["new", "work_item", "--title", "T", "--status", "done"], "new"),
    "set": (["set", "WI-0001", "status=done", "title=T", "--force", "--reason", "r"], "set"),
    "tag": (["tag", "WI-0001", "dns", "edge"], "tag"),
    "untag": (["untag", "WI-0001", "dns"], "untag"),
    "link": (["link", "WI-0001", "implements", "REQ-0001"], "link"),
    "unlink": (["unlink", "WI-0001", "parent", "EPIC-0001"], "unlink"),
    "convert": (["convert", "WI-0001", "spike"], "convert"),
    "layout": (["layout", "WI-0001", "directory"], "layout"),
    "log": (["log", "WI-0001", "a note"], "log"),
    "schedule": (["schedule", "WI-0002", "--horizon", "now", "--after", "WI-0001",
                  "--roadmap", "main"], "schedule"),
    "unschedule": (["unschedule", "WI-0002", "--roadmap", "main"], "unschedule"),
    "next": (["next", "--horizon", "now", "--parent", "EPIC-0001", "--limit", "3"], "next"),
    "show": (["show", "WI-0001", "--context"], "show"),
    "context": (["context", "--json"], "context"),
    "list": (["list", "--type", "work_item", "--status", "ready", "--tag", "a", "--tag", "b",
              "--parent", "EPIC-0001", "--uncovered", "--unscheduled", "--limit", "0"], "list"),
    "trace": (["trace", "EPIC-0001", "--depth", "3"], "trace"),
    "standards": (["standards", "match", "src/a.py", "src/b.py"], "standards"),
    "render": (["render", "--verbose"], "render"),
    "sync": (["sync"], "sync"),
    "refresh": (["refresh", "--force"], "refresh"),
    "validate": (["validate", "--verbose", "--limit", "5"], "validate"),
    "backend": (["backend", "show"], "backend"),
    "backend prune": (["backend", "prune"], "backend_prune"),
}


def commands() -> set[str]:
    return set(build_parser()._subparsers._group_actions[0].choices)


def test_every_command_but_hook_is_sampled():
    # A subcommand that runs its own operation is sampled as "command subcommand".
    assert {name.split()[0] for name in SAMPLES} == commands() - HIDDEN_COMMANDS


def test_every_cli_operation_has_a_command_and_every_command_an_operation():
    ran = {op for _, op in SAMPLES.values()}
    assert ran == {name for name, op in ops.OPERATIONS.items() if "cli" in op.interfaces}


def test_hook_is_a_cli_concern_not_an_operation():
    args = build_parser().parse_args(["hook", "stop"])
    assert getattr(args, "op", None) is None
    assert "hook" not in ops.OPERATIONS


@pytest.mark.parametrize("command", sorted(SAMPLES))
def test_what_the_parser_makes_fits_the_operation(command):
    argv, op_name = SAMPLES[command]
    args = build_parser().parse_args(argv)
    op = ops.OPERATIONS[op_name]
    assert args.op == op_name
    params = args.to_params(args)
    assert isinstance(params, op.params)
    # And it round-trips through the mapping form another interface would send.
    assert build_params(op, params.to_dict()) == params


def test_options_reach_their_params():
    args = build_parser().parse_args(SAMPLES["list"][0])
    params = args.to_params(args)
    assert (params.type, params.status, params.tags, params.parent, params.uncovered,
            params.unscheduled, params.limit) == (
        "work_item", "ready", ("a", "b"), "EPIC-0001", True, True, 0)
    args = build_parser().parse_args(SAMPLES["set"][0])
    params = args.to_params(args)
    assert (params.assignments, params.force, params.reason) == (("status=done", "title=T"), True, "r")


def test_json_lifts_the_limit_as_it_always_has():
    args = build_parser().parse_args(["list", "--json"])
    assert args.to_params(args).limit == 0
    args = build_parser().parse_args(["list"])
    assert args.to_params(args).limit == 20
