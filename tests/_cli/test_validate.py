"""szsdlc validate: every consistency rule, silent when clean."""

from __future__ import annotations

import json

from szsdlc.errors import EXIT_INVALID

from test_validate import clean


def test_a_clean_project_reports_nothing(run, clean):
    """C4 — this runs at every turn end; output on the happy path has no consumer."""
    code, output, err = run("validate")
    assert (code, output, err) == (0, "", "")


def test_verbose_opts_in(run, clean):
    _, output, _ = run("validate", "--verbose")
    assert output.strip() == "clean"


# ---------------------------------------------------------------------------
# The command
# ---------------------------------------------------------------------------
def test_errors_exit_four_and_warnings_alone_exit_zero(run, clean, write_entity):
    write_entity("idea", 1, "status: inbox\ncaptured: 2020-01-01\n", "old\n")
    run("render")
    code, output, _ = run("validate")
    assert code == 0
    assert "warning" in output

    write_entity("work_item", 9, "title: T\nstatus: idea\nopened: 2026-08-01\n",
                 slug="no-parent")
    code, output, _ = run("validate")
    assert code == EXIT_INVALID


def test_findings_are_grouped_most_severe_first(run, clean, write_entity):
    write_entity("idea", 1, "status: inbox\ncaptured: 2020-01-01\n", "old\n")
    write_entity("work_item", 9, "title: T\nstatus: idea\nopened: 2026-08-01\n",
                 slug="no-parent")
    _, output, _ = run("validate")
    levels = [line.split()[0] for line in output.strip().splitlines()[:-1]
              if line.split()[0] in {"error", "warning"}]
    assert levels == sorted(levels)


def test_the_listing_is_bounded_and_the_total_is_printed(run, clean, write_entity):
    for number in range(30):
        write_entity("work_item", 100 + number,
                     "title: T\nstatus: idea\nopened: 2026-08-01\n"
                     "relations:\n  implements: [REQ-0420]\n", slug=f"o{number}")
    _, output, _ = run("validate")
    lines = output.strip().splitlines()
    assert len(lines) == 22  # 20 findings + truncation note + totals
    assert "rerun with --limit 0" in lines[-2]
    assert lines[-1].endswith("warnings") or lines[-1].endswith("warning")


def test_json_carries_every_finding_with_its_fix(run, clean, write_entity):
    write_entity("work_item", 9, "title: T\nstatus: idea\nopened: 2026-08-01\n"
                                 "relations:\n  implements: [REQ-0420]\n",
                 slug="dangles")
    code, output, _ = run("validate", "--json")
    payload = json.loads(output)
    assert code == EXIT_INVALID
    assert {"level", "kind", "ref", "message", "fix"} == set(payload[0])


def test_a_model_invariant_breach_is_not_a_finding(run, clean, monkeypatch):
    """A szsdlc bug is reported as one, never as a statement about the project."""
    from szsdlc.errors import InternalError

    def explode(*_args, **_kwargs):
        raise InternalError("unknown derived kind")

    monkeypatch.setattr("szsdlc.validate.run", explode)
    code, output, err = run("validate")
    assert code == 1
    assert output == ""
    assert err.startswith("internal error:")
