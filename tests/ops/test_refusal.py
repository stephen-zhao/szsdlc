"""Refusals as data, and the two-shot property a layer down (WI-0010, WI-0060).

The CLI's contract test runs each refusal's printed `Fix:` as a command. Here
the same cases run in-process: the failing call is made as an operation, its
refusal's structured fix is run with `ops.call`, and that must succeed.
"""

from __future__ import annotations

import pytest

from szsdlc import ops
from szsdlc.cli import build_parser
from szsdlc.errors import (BadInput, ConfigError, InternalError, Refused, StalePrecondition,
                           SzsdlcError, UnknownReference, ValidationFailed)

from _cli.test_contract import ERROR_CASES, build_project, invoke  # noqa: F401  (a fixture)


def as_call(argv):
    """The operation and params a command line means, or None if it does not parse."""
    try:
        args = build_parser().parse_args(list(argv))
    except SzsdlcError:
        return None
    return args.op, args.to_params(args)


@pytest.mark.parametrize("name,setup,argv,expected", ERROR_CASES, ids=[c[0] for c in ERROR_CASES])
def test_a_refusals_structured_fix_runs_as_an_operation(tmp_path, invoke, name, setup, argv,
                                                        expected):
    config = build_project(tmp_path)
    if setup:
        setup(config, invoke)
    call = as_call(argv)
    if call is None:  # an argparse refusal: there is no operation to call
        return
    op, params = call

    with pytest.raises(SzsdlcError) as refused:
        ops.call(op, params, str(config.root))

    data = ops.refusal_to_data(refused.value)
    assert data["class"] == refused.value.refusal_class
    assert data["problem"] and "Traceback" not in data["problem"]
    fix = data["fix"]
    if fix is None:
        # A remedy rather than a command, or a form with a placeholder to fill.
        text = refused.value.fix or ""
        assert not text.startswith("szsdlc ") or "<" in text, text
        return
    assert fix["op"] in ops.OPERATIONS
    ops.call(fix["op"], fix["params"], str(config.root))


@pytest.mark.parametrize("name,setup,argv,expected", ERROR_CASES, ids=[c[0] for c in ERROR_CASES])
def test_the_printed_fix_parses_back_to_the_same_call(tmp_path, invoke, name, setup, argv,
                                                      expected):
    """The CLI's rendering and the structured fix are the same thing."""
    config = build_project(tmp_path)
    if setup:
        setup(config, invoke)
    _, _, err = invoke(config.root, *argv)
    printed = err.strip().splitlines()[1][len("Fix: "):].strip()
    call = as_call(argv)
    if call is None:
        return
    with pytest.raises(SzsdlcError) as refused:
        ops.call(call[0], call[1], str(config.root))
    assert refused.value.fix == printed
    assert ops.fix_call(printed) == ops.fix_call(refused.value.fix)


@pytest.mark.parametrize("cls,name", [
    (BadInput, "bad_input"), (UnknownReference, "unknown_reference"), (Refused, "refused"),
    (StalePrecondition, "stale_precondition"), (ValidationFailed, "validation_failed"),
    (ConfigError, "config_error"), (InternalError, "internal"),
])
def test_each_refusal_class_names_itself(cls, name):
    assert cls.refusal_class == name


def test_an_unknown_reference_is_its_own_class_and_names_the_nearest(root):
    with pytest.raises(UnknownReference) as refused:
        ops.call("show", {"ref": "WI-0020"}, root)
    data = ops.refusal_to_data(refused.value)
    assert data["class"] == "unknown_reference"
    assert data["fix"] == {"op": "show", "params": {"ref": "WI-0002", "context": False}}


def test_a_gate_refusal_carries_its_fix_as_a_call(root):
    with pytest.raises(Refused) as refused:
        ops.call("set", {"ref": "WI-0001", "assignments": ["status=done"]}, root)
    data = ops.refusal_to_data(refused.value)
    assert data["class"] == "refused"
    assert data["fix"]["op"] == "set"


def test_a_fix_that_is_not_a_command_is_no_call():
    assert ops.fix_call("declare one under relations: in .szsdlc/config.yml") is None
    assert ops.fix_call("szsdlc set WI-0001 title=<value>") is None
    assert ops.fix_call("szsdlc hook session-start") is None
    assert ops.fix_call("szsdlc nonsense --x") is None
    assert ops.fix_call(None) is None


def test_any_other_exception_is_internal():
    assert ops.refusal_to_data(RuntimeError("boom")) == {
        "class": "internal", "problem": "boom", "fix": None, "see": None}
