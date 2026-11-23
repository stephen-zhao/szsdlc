"""Fixtures the operation layer's tests share: the CLI contract's project, driven in-process."""

from __future__ import annotations

import io

import pytest

from _cli.test_contract import build_project


@pytest.fixture
def root(tmp_path):
    """The contract project: EPIC-0001, REQ-0001, IDEA-0001, WI-0001 (idea) and WI-0002 (ready)."""
    return str(build_project(tmp_path).root)


@pytest.fixture
def bulk(tmp_path):
    """The contract project with 30 more ready work items, tagged `bulk`."""
    return str(build_project(tmp_path, entities=30).root)


@pytest.fixture(autouse=True)
def never_a_tty(monkeypatch):
    """An operation never reads stdin; if one did, it would read nothing rather than hang."""
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
